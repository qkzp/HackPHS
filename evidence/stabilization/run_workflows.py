"""Retained audit evidence: actual OrcaSlicer workflows, never synthetic slices.

Run from the repository root with .venv/bin/python evidence/stabilization/run_workflows.py.
The shape generator is a test fixture; production import/slicing/physics code is used.
"""
from pathlib import Path
import json
import sys
from time import perf_counter
import platform

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'AeroSlice/tests')]
from test_motion_simulation import shape_mesh
from AeroSlice.file_import import load_model
from AeroSlice.model import write_stl,geometry_digest
from AeroSlice.structural import weight_saving_settings
from AeroSlice.slicer import OrcaSlicer,Profiles,project_contract
from AeroSlice.optimizer import optimize
from AeroSlice.fea import FEASettings
from AeroSlice.motion_simulation import MotionSettings,compare_motion
from AeroSlice.validation import inspect_gcode


def main():
    evidence=ROOT/'evidence/stabilization';models=evidence/'models';models.mkdir(exist_ok=True)
    results=[]
    for name in ('cube','prism','cylinder','sphere','irregular'):
        path=models/(name+'.stl');write_stl(path,shape_mesh(name));model=load_model(path);original=path.read_bytes()
        for mode in ('weight','weight_and_fea_95'):
            started=perf_counter()
            fea=FEASettings(assessment='relative_stiffness',minimum_stiffness_ratio=.95,force_n=1) if mode.endswith('95') else None
            report=optimize(model,weight_saving_settings(15,candidate_count=3),ROOT/'outputs/stabilization',OrcaSlicer(),Profiles(),fea=fea)
            row={'shape':name,'mode':mode,'seconds':round(perf_counter()-started,2),
                 'status':report['status'],'message':report['message'],'run_directory':report['run_directory']}
            assert path.read_bytes()==original
            if report['status']=='AUTOMATED SUCCESS':
                before=next(c for c in report['candidates'] if c['id']=='baseline')
                selected=next(c for c in report['candidates'] if c['id']==report['selected'])
                a=inspect_gcode(Path(report['exports']['baseline.gcode']))
                b=inspect_gcode(Path(report['exports']['optimized.gcode']))
                assert a.mass_g==before['metrics']['mass_g'] and b.mass_g==selected['metrics']['mass_g']
                assert a.mass_g>b.mass_g and a.sparse_digest!=b.sparse_digest
                assert all(selected['validation'][k] for k in ('valid','infill_geometry_verified','external_mesh_preserved','modifier_densities_preserved','non_target_settings_preserved'))
                oriented=geometry_digest(model.oriented(report['settings']['rotation']))
                assert project_contract(Path(before['project']))['geometry']==oriented
                assert project_contract(Path(selected['project']))['geometry']==oriented
                result=compare_motion(model,report,MotionSettings())
                assert len(result['triangles'])==len(model.triangles)
                assert geometry_digest(result['triangles'])==oriented
                row.update(baseline_mass_g=a.mass_g,optimized_mass_g=b.mass_g,
                           savings_percent=report['savings_percent_estimated'],selected_densities=selected['densities'],
                           original_climb_s=result['original']['time_to_target_s'],optimized_climb_s=result['optimized']['time_to_target_s'],
                           climb_time_reduction_percent=result['climb_time_reduction_percent'],geometry_preserved=True,
                           sparse_toolpaths_changed=True,reopening_verified=report['compatibility']['checks']['project_reopening']['status']=='PASS')
                if fea is not None:
                    row['minimum_predicted_stiffness_percent']=100*min(selected['fea'][m]['relative_stiffness_ratio'] for m in ('coarse','refined'))
                    assert row['minimum_predicted_stiffness_percent']>=95-1e-8
            else:
                assert report['status']=='FAILED' and not report['exports']
                assert not (Path(report['run_directory'])/'optimized.gcode').exists()
                row['no_invalid_export']=True
            results.append(row)
            (evidence/'workflows.json').write_text(json.dumps({'python':platform.python_version(),'cases':results},indent=2,allow_nan=False))
            print(json.dumps(row),flush=True)


if __name__=='__main__':main()
