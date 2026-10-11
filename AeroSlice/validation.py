"""Authentic slicer metrics and positive-extrusion XY toolpath verification."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import re
import math
import numpy as np

@dataclass
class SliceMetrics:
    mass_g: float
    filament_mm: float
    time_s: float
    sparse_segments: int
    sparse_digest: str
    region_digests: list[str]
    region_segments: list[int]
    exterior_bounds: list[list[float]]
    source: str = 'OrcaSlicer estimates; not measured'


def parse_duration(value: str) -> float:
    factors={'d':86400,'h':3600,'m':60,'s':1}
    matches=re.findall(r'([\d.]+)\s*([dhms])',value)
    if not matches: raise ValueError('Slicer printing-time estimate is missing or unrecognized.')
    return sum(float(n)*factors[u] for n,u in matches)


def inspect_gcode(path: Path, axis: str = 'z', region_bounds: tuple[float,float] | None = None) -> SliceMetrics:
    text=path.read_text(errors='strict')
    if not text.strip(): raise ValueError('Slicer produced an empty file.')
    def number(key):
        match=re.search(r'^;\s*'+re.escape(key)+r'\s*=\s*([0-9., ]+)',text,re.M)
        if not match: raise ValueError(f'Missing authentic slicer metric: {key}')
        values=[float(x.strip()) for x in match[1].split(',') if x.strip()]
        if not values or not all(math.isfinite(v) and v>0 for v in values): raise ValueError('Invalid slicer material estimate.')
        return sum(values)
    mass=number('filament used [g]');filament=number('filament used [mm]')
    time_match=re.search(r'^;\s*estimated printing time \(normal mode\)\s*=\s*(.+)$',text,re.M)
    if not time_match: raise ValueError('Missing authentic slicer time estimate.')
    duration=parse_duration(time_match[1])
    if duration<=0: raise ValueError('Printing time must be positive.')
    xyz=np.zeros(3); e=0.; rel_e=False; rel_xyz=False; role=''; segments=[]; outer=[]
    for line in text.splitlines():
        if line.startswith(';TYPE:') or line.startswith('; FEATURE:'):
            role=line.split(':',1)[1].strip().lower();continue
        command=line.split(';',1)[0].strip()
        if not command: continue
        tokens=command.split(); op=tokens[0].upper()
        if op=='M83': rel_e=True;continue
        if op=='M82': rel_e=False;continue
        if op=='G90': rel_xyz=False;continue
        if op=='G91': rel_xyz=True;continue
        vals={m[1]:float(m[2]) for m in re.finditer(r'([XYZE])\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)',command)}
        if op=='G92':
            for j,k in enumerate('XYZ'):
                if k in vals: xyz[j]=vals[k]
            if 'E' in vals: e=vals['E']
            continue
        if op in ('G2','G3'):
            raise ValueError('Arc toolpaths are unsupported by this validator. Disable arc fitting in OrcaSlicer.')
        if op not in ('G0','G1'): continue
        end=xyz.copy()
        for j,k in enumerate('XYZ'):
            if k in vals: end[j]=vals[k]+(xyz[j] if rel_xyz else 0)
        extrusion=vals.get('E',0) if rel_e else vals.get('E',e)-e
        if 'E' in vals: e=e+vals['E'] if rel_e else vals['E']
        if extrusion>1e-8 and np.linalg.norm(end[:2]-xyz[:2])>1e-6:
            if role in ('sparse infill','internal infill'):
                segments.append((xyz.copy(),end.copy()))
            if role in ('outer wall','external perimeter'): outer.extend((xyz.copy(),end.copy()))
        xyz=end
    if not outer: raise ValueError('No authentic outer-wall extrusion geometry found.')
    if not segments: raise ValueError('No sparse infill found; geometry may be too small or solid. Regional optimization cannot be verified.')
    ext=np.array(outer);low,high=ext.min(axis=0),ext.max(axis=0)
    ax='xyz'.index(axis);start,stop=region_bounds or (low[ax],high[ax])
    buckets=[[],[],[]]
    records=[]
    for p,q in segments:
        record=tuple(sorted((tuple(np.round(p,4)),tuple(np.round(q,4)))))
        records.append(record)
        midpoint=(p[ax]+q[ax])/2
        bucket=min(2,max(0,int((midpoint-start)/max(stop-start,1e-9)*3)))
        buckets[bucket].append(record)
    def digest(records): return hashlib.sha256(repr(sorted(records)).encode()).hexdigest()
    return SliceMetrics(mass,filament,duration,len(records),digest(records),[digest(b) for b in buckets],
                        [len(b) for b in buckets],[low.tolist(),high.tolist()])


def validate_difference(baseline: SliceMetrics, candidate: SliceMetrics, densities: tuple[float,...], uniform: float) -> dict:
    changed=[i for i,(a,b) in enumerate(zip(baseline.region_digests,candidate.region_digests)) if a!=b]
    targets=[i for i,d in enumerate(densities) if d!=uniform]
    # G-code extent tolerance is nozzle-width scale; exact geometry is separately hashed.
    extent_ok=np.allclose(baseline.exterior_bounds,candidate.exterior_bounds,atol=.05,rtol=0)
    infill_ok=bool(set(changed)&set(targets)) if targets else candidate.sparse_digest==baseline.sparse_digest
    return {'external_toolpath_bounds_preserved':bool(extent_ok),'infill_geometry_verified':infill_ok,
            'changed_regions':changed,'target_regions':targets,
            'valid':bool(extent_ok and infill_ok),
            'note':'Positive-extrusion sparse-infill XYZ segments compared; no E scaling or flow edits.'}
