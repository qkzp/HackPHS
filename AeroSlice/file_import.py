"""Strict STL/3MF import; reads originals without ever modifying them."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, BadZipFile
import hashlib
import re
import struct
import xml.etree.ElementTree as ET
import numpy as np
from .model import Model

CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PROD = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
Q = lambda name: f'{{{CORE}}}{name}'
UNITS = {'micron': .001, 'millimeter': 1., 'centimeter': 10., 'inch': 25.4, 'foot': 304.8, 'meter': 1000.}


def safe_xml(data: bytes) -> ET.Element:
    if len(data) > 150_000_000 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Unsupported or oversized XML in 3MF.')
    return ET.fromstring(data)


def stl_geometry(data: bytes) -> np.ndarray:
    if len(data) >= 84:
        count = struct.unpack_from('<I', data, 80)[0]
        if count and len(data) == 84 + 50 * count:
            dtype = np.dtype([('normal','<f4',(3,)),('vertices','<f4',(3,3)),('attr','<u2')])
            return np.frombuffer(data, dtype=dtype, count=count, offset=84)['vertices'].astype(float)
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ValueError('Corrupted binary STL: triangle count does not match file size.') from exc
    if not text.lstrip().lower().startswith('solid') or 'endsolid' not in text.lower():
        raise ValueError('This file is not an ASCII or binary STL.')
    facets = re.findall(r'facet\s+normal\s+.*?endfacet', text, re.I | re.S)
    triangles = []
    for facet in facets:
        vertices = re.findall(r'vertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)', facet, re.I)
        if len(vertices) != 3: raise ValueError('Every STL facet must contain three vertices.')
        triangles.append([[float(v) for v in row] for row in vertices])
    return np.asarray(triangles, dtype=float)


def matrix(value: str | None) -> np.ndarray:
    if not value: return np.eye(4)
    values = np.array([float(x) for x in value.split()])
    if len(values) != 12 or not np.isfinite(values).all(): raise ValueError('Invalid 3MF transform.')
    result = np.eye(4)
    result[:3, :3] = values[:9].reshape(3, 3).T
    result[:3, 3] = values[9:]
    return result


def read_3mf(path: Path) -> tuple[np.ndarray, str, list[str]]:
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)): raise ValueError('3MF contains duplicate archive entries.')
        if sum(i.file_size for i in archive.infolist()) > 250_000_000:
            raise ValueError('3MF unpacked size exceeds 250 MB.')
        if not {'3D/3dmodel.model','[Content_Types].xml','_rels/.rels'} <= set(names):
            raise ValueError('3MF is missing required package content types, relationships or model root.')
        content_types=safe_xml(archive.read('[Content_Types].xml'))
        relationships=safe_xml(archive.read('_rels/.rels'))
        if not any(r.get('Target','').lstrip('/')=='3D/3dmodel.model' and r.get('Type','').endswith('/3dmodel') for r in relationships):
            raise ValueError('3MF has no supported model-root relationship.')
        native = 'Metadata/project_settings.config' in names and 'Metadata/model_settings.config' in names
        settings = safe_xml(archive.read('Metadata/model_settings.config')) if native else None
        skip = set()
        warnings = []
        if native:
            plates = settings.findall('plate')
            if len(plates) != 1: raise ValueError('Only single-plate Orca projects are supported.')
            for part in settings.findall('object/part'):
                subtype = part.get('subtype', 'normal_part')
                if subtype == 'negative_part':
                    raise ValueError('Negative-volume projects require Orca mesh boolean processing; export the final STL first.')
                if subtype != 'normal_part': skip.add(part.get('id'))
            warnings.append('Project geometry imported. Existing modifiers and settings are replaced by the selected AeroSlice profiles; the original is preserved.')
        documents = {}
        def document(name):
            if name not in documents:
                documents[name] = safe_xml(archive.read(name))
            return documents[name]
        main = document('3D/3dmodel.model')
        unit = main.get('unit', 'millimeter')
        if unit not in UNITS: raise ValueError('Unsupported 3MF unit.')
        items = main.findall(Q('build')+'/'+Q('item'))
        if len(items) != 1: raise ValueError('Only one build object / instance is supported. Export the removable payload separately.')
        def visit(name, oid, transform, ancestors):
            if (name, oid) in ancestors: raise ValueError('3MF has circular component references.')
            doc = document(name)
            obj = next((o for o in doc.findall(Q('resources')+'/'+Q('object')) if o.get('id') == oid), None)
            if obj is None: raise ValueError('3MF references missing geometry.')
            mesh = obj.find(Q('mesh'))
            if mesh is not None:
                vs = np.array([[float(v.get(a,'')) for a in ('x','y','z')] for v in mesh.findall(Q('vertices')+'/'+Q('vertex'))])
                fs = np.array([[int(t.get(a,'')) for a in ('v1','v2','v3')] for t in mesh.findall(Q('triangles')+'/'+Q('triangle'))])
                if vs.size == 0 or fs.size == 0 or fs.min() < 0 or fs.max() >= len(vs):
                    raise ValueError('3MF mesh contains invalid vertices or faces.')
                points = vs[fs]
                return [points @ transform[:3,:3].T + transform[:3,3]]
            parts = []
            for component in obj.findall(Q('components')+'/'+Q('component')):
                cid = component.get('objectid')
                if native and cid in skip: continue
                target = component.get(f'{{{PROD}}}path', name)
                if target.startswith('/'): target = target.lstrip('/')
                elif target != name: target = str(PurePosixPath(name).parent / target)
                if '..' in PurePosixPath(target).parts: raise ValueError('Unsupported 3MF component path.')
                parts.extend(visit(target,cid,transform @ matrix(component.get('transform')), ancestors+[(name,oid)]))
            return parts
        meshes = visit('3D/3dmodel.model', items[0].get('objectid'), matrix(items[0].get('transform')), [])
        if not meshes: raise ValueError('3MF contains no printable geometry.')
        if len(meshes) != 1: raise ValueError('Multi-part payloads are unsupported; export a single combined STL first.')
        return meshes[0]*UNITS[unit], 'Orca project 3MF' if native else 'Geometry 3MF', warnings


def load_model(filename: str | Path) -> Model:
    path = Path(filename).expanduser().resolve()
    if not path.is_file(): raise ValueError('Model file does not exist.')
    if path.stat().st_size > 250_000_000: raise ValueError('Model exceeds the 250 MB import limit.')
    try:
        data = path.read_bytes()
        if path.suffix.lower() not in ('.stl', '.3mf'): raise ValueError('Choose an STL or 3MF file.')
        if data[:4] == b'PK\x03\x04':
            if path.suffix.lower() != '.3mf': raise ValueError('File contents are 3MF but the extension is incorrect.')
            triangles, kind, warnings = read_3mf(path)
        else:
            if path.suffix.lower() != '.stl': raise ValueError('File is not a valid 3MF archive.')
            triangles, kind, warnings = stl_geometry(data), 'STL geometry', ['STL units assumed millimeters.']
        if triangles.ndim != 3 or triangles.shape[1:] != (3,3) or not len(triangles):
            raise ValueError('No usable triangle geometry was found.')
        if len(triangles) > 1_000_000: raise ValueError('Model exceeds one million triangles; simplify it before importing.')
        if not np.isfinite(triangles).all(): raise ValueError('Geometry contains nonfinite coordinates.')
        areas = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0]),axis=1)
        if np.any(areas < 1e-10): raise ValueError('Geometry contains degenerate triangles. Repair it in OrcaSlicer first.')
        dimensions = np.ptp(triangles,axis=(0,1))
        if np.any(dimensions < .001): raise ValueError('Model must have nonzero dimensions on all three axes.')
        if dimensions.max() < 1:
            size = ' × '.join(f'{d:.6g}' for d in dimensions)
            warnings.append(f'Very small model: {size} mm. Check the intended size and export units before printing; the app does not automatically resize your model.')
        # A closed manifold is needed for the payload-volume heuristic.
        verts = np.round(triangles.reshape(-1,3),6)
        _, inv = np.unique(verts,axis=0,return_inverse=True)
        faces = inv.reshape(-1,3)
        edges = np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1)
        _, counts = np.unique(edges,axis=0,return_counts=True)
        if np.any(counts != 2): raise ValueError('Mesh is open or nonmanifold. Repair the model before optimization.')
        return Model(path,kind,triangles,hashlib.sha256(data).hexdigest(),warnings)
    except (OSError, BadZipFile, ET.ParseError, KeyError, IndexError, struct.error) as exc:
        raise ValueError(f'Cannot import model: {exc}') from exc
