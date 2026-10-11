"""OrcaSlicer CLI adapter verified against installed 2.4.2.

Only flags found in executable --help are used. Native 3MF parts use the
modifier_part subtype and sparse_infill_density metadata from Orca's 3MF
serializer. Every runtime project is reopened/resliced by Orca before success.
No network, printing, motor control, G-code editing, or user preset mutation.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Event
from zipfile import ZipFile, ZIP_DEFLATED
import json
import os
import platform
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
import numpy as np
from .file_import import CORE, PROD, Q, matrix, safe_xml, load_model
from .model import box_mesh, geometry_digest

DEFAULT_EXECUTABLE = '/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer'
REQUIRED_FLAGS = ('--load-settings','--load-filaments','--export-3mf','--outputdir','--slice','--orient','--arrange')

class Cancelled(RuntimeError): pass

@dataclass
class Profiles:
    machine: str = 'Flashforge Adventurer 5M 0.4 Nozzle'
    process: str = '0.20mm Standard @Flashforge AD5M 0.4 Nozzle'
    filament: str = 'Flashforge Generic PLA'

class OrcaSlicer:
    def __init__(self, executable: str = '', profile_root: str = '', timeout: float = 180., cancel: Event | None = None):
        discovered = shutil.which('orca-slicer') or shutil.which('OrcaSlicer')
        self.executable = Path(executable or (DEFAULT_EXECUTABLE if Path(DEFAULT_EXECUTABLE).is_file() else discovered or DEFAULT_EXECUTABLE)).expanduser()
        self.root=Path(profile_root).expanduser() if profile_root else self.executable.parent.parent/'Resources/profiles'
        self.timeout=timeout
        self.cancel=cancel or Event()
        self.index: dict[str,Path]={}
        self.version='NOT TESTED'
        self.help=''

    def run(self, args: list[str], log: Path, timeout: float | None = None) -> str:
        log.parent.mkdir(parents=True,exist_ok=True)
        command=[str(self.executable),*args]
        log.with_suffix('.command.json').write_text(json.dumps(command,indent=2))
        with log.open('w') as stream:
            proc=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+(timeout or self.timeout)
            try:
                while proc.poll() is None:
                    if self.cancel.is_set(): raise Cancelled('Operation cancelled. No automatic success was recorded.')
                    if time.monotonic()>deadline: raise TimeoutError(f'OrcaSlicer exceeded {timeout or self.timeout:.0f} seconds. See {log}')
                    time.sleep(.1)
            except BaseException:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill();proc.wait()
                raise
        text=log.read_text(errors='replace')
        if proc.returncode:
            raise RuntimeError(f'OrcaSlicer exit {proc.returncode}: {text[-1800:]}\nLog: {log}')
        return text

    def discover(self, work: Path) -> dict:
        report={'os':platform.platform(),'python':platform.python_version(),'executable':str(self.executable),
                'profile_root':str(self.root),'checks':{}}
        checks=report['checks']
        if not self.executable.is_file() or not os.access(self.executable,os.X_OK):
            checks['executable']={'status':'FAIL','detail':'OrcaSlicer executable not found. Select it in the application.'}
            return report
        try:
            self.help=self.run(['--help'],work/'orca_help.log',30)
            self.version=self.help.splitlines()[0].rstrip(':')
            checks['cli']={'status':'PASS','detail':self.version}
            for flag in REQUIRED_FLAGS:
                if flag not in self.help: raise ValueError(f'Installed OrcaSlicer does not advertise {flag}.')
            checks['required_flags']={'status':'PASS','detail':list(REQUIRED_FLAGS)}
            for path in self.root.rglob('*.json'):
                try:
                    value=json.loads(path.read_text())
                    if 'name' in value: self.index.setdefault(value['name'],path)
                except (OSError,ValueError): continue
            if not self.index: raise ValueError('No printer/process/filament profiles found. Select the profiles directory.')
            checks['profile_discovery']={'status':'PASS','detail':len(self.index)}
        except Exception as exc: checks['discovery']={'status':'FAIL','detail':str(exc)}
        report['orca_version']=self.version
        return report

    def resolve(self, name: str, seen: tuple[str,...] = ()) -> dict:
        path=Path(name).expanduser()
        if not path.is_file():
            if name not in self.index: raise ValueError(f'Profile not found: {name}')
            path=self.index[name]
        if str(path) in seen: raise ValueError('Circular profile inheritance.')
        data=json.loads(path.read_text())
        result=self.resolve(data['inherits'],seen+(str(path),)) if data.get('inherits') else {}
        result.update(data);result.pop('inherits',None)
        return result

    def prepare_profiles(self, selected: Profiles, work: Path, density: float) -> dict:
        values={kind:self.resolve(getattr(selected,kind)) for kind in ('machine','process','filament')}
        machine=values['machine']
        if machine.get('printer_model') != 'Flashforge Adventurer 5M':
            raise ValueError('Select the Flashforge Adventurer 5M profile (not Pro or AD5X).')
        for kind in ('process','filament'):
            compatible=values[kind].get('compatible_printers',[])
            if compatible and machine['name'] not in compatible:
                raise ValueError(f'{kind.title()} profile does not list the selected printer as compatible.')
            if values[kind].get('compatible_printers_condition'):
                raise ValueError('Conditional profile compatibility is unsupported. Select a profile with an explicit printer list.')
        if values['process'].get('enable_arc_fitting') not in (None, '', '0', 0, False):
            raise ValueError('Arc-fitting profiles are unsupported by the current toolpath validator. Select a process with arc fitting disabled.')
        if values['process'].get('post_process'):
            raise ValueError('Profiles with post-processing scripts are unsupported. Select a profile without scripts.')
        values['process']['sparse_infill_density']=f'{density:g}%'
        machine['printer_settings_id']=machine['name']
        work.mkdir(parents=True,exist_ok=True)
        for kind,data in values.items(): (work/f'{kind}.json').write_text(json.dumps(data,indent=2))
        return values

    def establish_project(self, stl: Path, work: Path, selected: Profiles, density: float) -> tuple[Path,dict]:
        profiles=self.prepare_profiles(selected,work/'profiles',density)
        self.run(['--load-settings',str(work/'profiles/machine.json')+';'+str(work/'profiles/process.json'),
                  '--load-filaments',str(work/'profiles/filament.json'),'--orient','0','--arrange','1',
                  '--export-3mf','seed.3mf','--outputdir',str(work),str(stl)],work/'establish.log')
        project=work/'seed.3mf'
        if not project.is_file(): raise ValueError('OrcaSlicer did not export a native project.')
        load_model(project)
        return project,profiles

    def slice(self, project: Path, work: Path, name: str) -> tuple[Path,Path]:
        work.mkdir(parents=True,exist_ok=True)
        self.run(['--orient','0','--arrange','0','--slice','0','--export-3mf',name+'.3mf',
                  '--outputdir',str(work),str(project)],work/'slice.log')
        exported=work/(name+'.3mf')
        gcode=list(work.glob('*.gcode'))
        if not exported.is_file() or len(gcode)!=1: raise ValueError('Expected one native project and one real G-code file.')
        load_model(exported)  # Reopen ZIP/XML and validate printable geometry.
        return exported,gcode[0]


def package(path: Path) -> dict[str,bytes]:
    with ZipFile(path) as z: return {n:z.read(n) for n in z.namelist()}


def build_modifiers(seed: Path, output: Path, densities: tuple[float,float,float], axis: str) -> None:
    """Three adjacent box modifiers in the parent object's coordinate frame.

    Seed is an Orca-generated one-object project with one printable volume.
    No arbitrary or overlapping uploaded modifiers are patched. Rebuilding from
    validated geometry deliberately replaces imported project settings.
    """
    ET.register_namespace('',CORE);ET.register_namespace('p',PROD)
    ET.register_namespace('BambuStudio','http://schemas.bambulab.com/package/2021')
    files=package(seed)
    main=safe_xml(files['3D/3dmodel.model']);cfg=safe_xml(files['Metadata/model_settings.config'])
    objects=main.findall(Q('resources')+'/'+Q('object'))
    if len(objects)!=1: raise ValueError('Seed project is not a single object.')
    components=objects[0].find(Q('components'))
    if components is None or len(components)!=1: raise ValueError('Seed project is not a single printable volume.')
    first=components[0];meshfile=first.get(f'{{{PROD}}}path','').lstrip('/')
    if not meshfile: raise ValueError('Seed uses an unsupported geometry layout.')
    sub=safe_xml(files[meshfile]);resources=sub.find(Q('resources'))
    normal=next(o for o in resources if o.get('id')==first.get('objectid'))
    vertices=normal.findall(Q('mesh')+'/'+Q('vertices')+'/'+Q('vertex'))
    vs=np.array([[float(v.get(a)) for a in 'xyz'] for v in vertices])
    transform=matrix(first.get('transform'))
    transformed=vs @ transform[:3,:3].T+transform[:3,3]
    lo,hi=transformed.min(axis=0),transformed.max(axis=0);ax='xyz'.index(axis)
    ids=[int(o.get('id')) for o in resources]+[int(o.get('id')) for o in objects]
    start=max(ids)+1
    object_cfg=cfg.find('object')
    if object_cfg is None: raise ValueError('Missing Orca object metadata.')
    for i,density in enumerate(densities):
        low,high=lo.copy(),hi.copy()
        # Expand only transverse bounds; axial slabs meet at exact boundaries.
        for a in range(3):
            if a!=ax: low[a]-=.01;high[a]+=.01
        low[ax]=lo[ax]+i*(hi[ax]-lo[ax])/3
        high[ax]=lo[ax]+(i+1)*(hi[ax]-lo[ax])/3
        triangles=box_mesh(low,high)
        oid=str(start+i)
        obj=ET.SubElement(resources,Q('object'),id=oid,type='model')
        obj.set(f'{{{PROD}}}UUID',str(uuid.uuid4()))
        mesh=ET.SubElement(obj,Q('mesh'));vnode=ET.SubElement(mesh,Q('vertices'));tnode=ET.SubElement(mesh,Q('triangles'))
        for triangle in triangles:
            offset=len(vnode)
            for p in triangle: ET.SubElement(vnode,Q('vertex'),{a:f'{v:.9g}' for a,v in zip('xyz',p)})
            ET.SubElement(tnode,Q('triangle'),v1=str(offset),v2=str(offset+1),v3=str(offset+2))
        ET.SubElement(components,Q('component'),{'objectid':oid,f'{{{PROD}}}path':'/'+meshfile,
                                              f'{{{PROD}}}UUID':str(uuid.uuid4()),'transform':'1 0 0 0 1 0 0 0 1 0 0 0'})
        part=ET.SubElement(object_cfg,'part',id=oid,subtype='modifier_part')
        ET.SubElement(part,'metadata',key='name',value=f'AeroSlice region {i+1} ({axis})')
        ET.SubElement(part,'metadata',key='sparse_infill_density',value=f'{density:g}%')
    for name,node in [('3D/3dmodel.model',main),(meshfile,sub),('Metadata/model_settings.config',cfg)]:
        files[name]=ET.tostring(node,encoding='utf-8',xml_declaration=True)
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        for name,data in files.items():
            if '.gcode' not in name and name not in ('Metadata/slice_info.config','Metadata/filament_sequence.json'):
                z.writestr(name,data)


def project_contract(path: Path) -> dict:
    files=package(path)
    config=safe_xml(files['Metadata/model_settings.config'])
    densities=[];nontarget=[]
    for obj in config.findall('object'):
        nontarget.append([(m.get('key'),m.get('value')) for m in obj.findall('metadata') if m.get('key') not in ('name',)])
        for part in obj.findall('part'):
            if part.get('subtype')=='modifier_part':
                d=next((m.get('value') for m in part.findall('metadata') if m.get('key')=='sparse_infill_density'),None)
                if d is None: raise ValueError('Exported modifier lost its density setting.')
                densities.append(float(d.rstrip('%')))
            nontarget.append([(m.get('key'),m.get('value')) for m in part.findall('metadata') if m.get('key') not in
                              ('sparse_infill_density','name','matrix','source_file','source_object_id','source_volume_id','source_offset_x','source_offset_y','source_offset_z')])
    settings=json.loads(files['Metadata/project_settings.config'])
    # Runtime-exported settings must agree in every key; densities are part metadata.
    return {'densities':densities,'settings':settings,'part_settings':nontarget,
            'geometry':geometry_digest(load_model(path).triangles)}
