"""Deterministic discrete regional search, authentic slicer ranking and export."""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from threading import Event
from typing import Callable
import csv
import itertools
import json
import shutil
import time
from datetime import datetime, timezone
import numpy as np
from .file_import import load_model
from .model import Model, write_stl, geometry_digest
from .structural import Settings, Region, make_regions
from .slicer import OrcaSlicer, Profiles, Cancelled, build_modifiers, project_contract
from .validation import inspect_gcode, validate_difference

@dataclass(frozen=True)
class Candidate:
    id: str
    densities: tuple[float,float,float]
    reason: str
    feasible: bool


def feasible(densities, regions: list[Region], settings: Settings) -> bool:
    if any(not r.minimum <= d <= r.maximum for r,d in zip(regions,densities)): return False
    weights=np.array([r.demand*r.volume_fraction for r in regions])
    if weights.sum()==0: weights=np.ones(3)/3
    return bool(float(np.dot(weights,densities)) + 1e-8 >= settings.coverage*settings.baseline*weights.sum())


def generate_candidates(regions: list[Region], settings: Settings) -> list[Candidate]:
    settings.validate()
    baseline=(settings.baseline,)*3
    options=sorted(set([d for d in settings.densities if settings.minimum<=d<=settings.maximum]+[settings.baseline]))
    combos=[v for v in itertools.product(options,repeat=3) if feasible(v,regions,settings)]
    def score(v):
        mass_proxy=sum(d*r.volume_fraction for d,r in zip(v,regions))
        alignment=sum((v[i]-v[j])*(regions[i].demand-regions[j].demand) for i in range(3) for j in range(i))
        return (round(mass_proxy,8),-round(alignment,8),v)
    combos.sort(key=score)
    result=[Candidate('baseline',baseline,'Uniform control; existing wall, layer and filament settings retained.',feasible(baseline,regions,settings))]
    uniform=next((v for v in combos if len(set(v))==1 and v!=baseline),None)
    if uniform:
        result.append(Candidate('uniform_lightweight',uniform,'Lightest feasible uniform comparison within the density grid.',True))
    for v in combos:
        if v==baseline or v==uniform or len(set(v))==1: continue
        result.append(Candidate(f'adaptive_{len(result):02d}',v,'Low volume-weighted infill proxy, subject to demand-weighted coverage and protected minima.',True))
        if len(result)>=settings.candidate_count: break
    return result


def write_reports(work: Path, report: dict) -> None:
    (work/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    with (work/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['candidate','densities_percent','status','mass_g_estimated','filament_mm_estimated','print_time_s_estimated','reason'])
        writer.writeheader()
        for c in report.get('candidates',[]):
            metrics=c.get('metrics',{})
            writer.writerow({'candidate':c['id'],'densities_percent':';'.join(map(str,c['densities'])),
                             'status':c.get('status','NOT TESTED'),'mass_g_estimated':metrics.get('mass_g'),
                             'filament_mm_estimated':metrics.get('filament_mm'),'print_time_s_estimated':metrics.get('time_s'),'reason':c['reason']})
    lines=[f"Status: {report['status']}",report.get('message',''),'','Validation:']
    for c in report.get('candidates',[]): lines.append(f"{c['id']}: {c.get('status','NOT TESTED')} {c.get('error','')} {json.dumps(c.get('validation',{}))}")
    lines.extend(['','Assumptions and limitations:',*report.get('assumptions',[]),
                  ('User-supplied independent stiffness measurement recorded; see report.json and loading/support conditions.' if isinstance(report.get('stiffness'),dict) else 'Structural stiffness has not been independently verified.'),
                  'Guided vertical travel is not free flight. Lower mass alone does not establish reduced drag or increased stiffness.',
                  'Physical printer and rig testing: NOT TESTED.'])
    (work/'validation_report.txt').write_text('\n'.join(lines))
    (work/'NOTES.md').write_text('\n'.join(report.get('assumptions',[])))
    (work/'README.md').write_text('AeroSlice run\n\n'+report.get('message','')+'\n\nOnly listed files in report.json are validated exports. Slicer estimates are not physical measurements. Check the actual material and printer before printing.\n')


def manual_steps(settings: Settings) -> list[str]:
    return [
        'Open the original payload in OrcaSlicer; select Adventurer 5M, the actual filament and a compatible process.',
        f'Orient it using rotations {settings.rotation}; preserve the external mesh.',
        f'Add three adjacent box modifiers along {settings.contact_axis}, using the reported region bounds. Do not overlap their interiors.',
        'Set each modifier sparse infill density to one candidate row; keep walls, layers, pattern and material settings fixed.',
        'Slice the baseline and candidate separately, save native projects and G-code, then import the authentic G-code into AeroSlice for comparison.',
        'Review Orca preview and printer settings before any physical print.']

def optimize(model: Model, settings: Settings, output: Path, slicer: OrcaSlicer, profiles: Profiles,
             progress: Callable[[str],None] = lambda _: None, semiautomated: bool = False, fea=None) -> dict:
    settings.validate();model.verify_original()
    output=Path(output).expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    work=output/f"AeroSlice-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{time.time_ns()%100000:05d}"
    work.mkdir()
    triangles=model.oriented(settings.rotation)
    progress('Analyzing geometry and declared loading assumptions…')
    regions,assumptions=make_regions(triangles,settings)
    candidates=generate_candidates(regions,settings)
    report={'status':'NOT TESTED','message':'Slicing has not yet run.','run_directory':str(work),
            'input_model':str(model.path),'input_sha256':model.sha256,'input_kind':model.kind,
            'settings':asdict(settings),'profiles':asdict(profiles),'regions':[asdict(r) for r in regions],
            'assumptions':model.warnings+assumptions,'candidates':[asdict(c) for c in candidates],
            'exports':{},'stiffness':'NOT YET VERIFIED','center_of_mass':'Unavailable: no resolved printed-material distribution.',
            'physical_printer_test':'NOT TESTED','rig_test':'NOT TESTED'}
    compatibility=slicer.discover(work)
    for name in ('baseline_slicing','modifiers','candidate_slicing','toolpath_changes','geometry_preservation','settings_preservation','project_reopening','material_estimates','physical_printer'):
        compatibility['checks'][name]={'status':'NOT TESTED'}
    report['orca_version']=slicer.version
    try:
        if slicer.cancel.is_set(): raise Cancelled('Optimization cancelled.')
        if semiautomated or any(c['status']=='FAIL' for c in compatibility['checks'].values()):
            if fea is not None:
                raise ValueError('FEA-guided G-code generation requires automatic mode and resolved slicer profiles. No heuristic fallback was used.')
            report['status']='SEMIAUTOMATED';report['message']='Mode: Semiautomated. Candidate recommendations were generated; no printer-ready files have been produced.'
            report['manual_steps']=manual_steps(settings)
            (work/'manual_workflow.txt').write_text('\n'.join(report['manual_steps']))
            return report
        fea_mesh=None
        if fea is not None:
            from .fea import direct_search, write_search, shell_from_profiles
            resolved=slicer.prepare_profiles(profiles,work/'fea_profiles',settings.baseline)
            fea=replace(fea,shell_mm=shell_from_profiles(resolved))
            search,fea_mesh=direct_search(triangles,settings,fea,slicer.cancel,progress)
            report['fea']=search;report['assumptions']=model.warnings+search['limitations']
            report['assumptions'].append('FEA mode uses regional minima and simulation limits; heuristic demand coverage is not a structural constraint in this mode.')
            write_search(work,search)
            feasible_rows=[r for r in search['candidates'] if r['feasible']]
            if not feasible_rows:raise ValueError('No FEA candidate passed the selected assessment limits and mesh-sensitivity checks. Review fea_search.json; no optimized export was generated.')
            baseline=(settings.baseline,)*3
            baseline_row=next((r for r in search['candidates'] if tuple(r['densities'])==baseline),None)
            candidates=[Candidate('baseline',baseline,'Uniform reference; FEA limits evaluated separately.',bool(baseline_row and baseline_row['feasible']))]
            for row in feasible_rows:
                values=tuple(row['densities'])
                if values==baseline:continue
                name='uniform_lightweight' if len(set(values))==1 and values[0]<settings.baseline else f'fea_{len(candidates):03d}'
                candidates.append(Candidate(name,values,'Passed both mesh resolutions under the specified, unvalidated FEA assumptions.',True))
                if len(candidates)>=settings.candidate_count:break
            report['candidates']=[asdict(c) for c in candidates]
            for row in report['candidates']:
                row['fea']=next((r for r in search['candidates'] if r['densities']==list(row['densities'])),None)
            search['slicing_shortlist_count']=len(candidates)
            search['selection_scope']='Lowest Orca-estimated mass among slice-validated candidates in the simulation-ranked shortlist; not a global optimum.'
            write_search(work,search)
        progress('Loading verified Adventurer 5M, process and filament profiles…')
        write_stl(work/'payload.stl',triangles)
        seed,resolved=slicer.establish_project(work/'payload.stl',work/'setup',profiles,settings.baseline)
        report['resolved_profiles']=resolved
        compatibility['checks']['baseline_slicing']={'status':'NOT TESTED'}
        compatibility['checks']['profiles']={'status':'PASS','detail':{k:v.get('name') for k,v in resolved.items()}}
        original_digest=geometry_digest(triangles)
        if geometry_digest(load_model(seed).triangles)!=original_digest: raise ValueError('Orca seed project changed the external mesh.')
        base_metrics=None;base_contract=None;good=[]
        for index,candidate in enumerate(candidates):
            if slicer.cancel.is_set(): raise Cancelled('Optimization cancelled.')
            progress(f'Slicing {index+1}/{len(candidates)}: {candidate.id} — {candidate.densities}%')
            row=report['candidates'][index]
            folder=work/'candidates'/candidate.id;folder.mkdir(parents=True)
            try:
                project=folder/'input.3mf'
                build_modifiers(seed,project,candidate.densities,settings.contact_axis)
                before=project_contract(project)
                exported,gcode=slicer.slice(project,folder/'sliced',candidate.id)
                contract=project_contract(exported)
                metrics=inspect_gcode(gcode,settings.contact_axis)
                if index==0:
                    base_metrics=metrics;base_contract=contract
                    compatibility['checks']['baseline_slicing']={'status':'PASS'}
                    compatibility['checks']['material_estimates']={'status':'PASS'}
                validation=validate_difference(base_metrics,metrics,candidate.densities,settings.baseline)
                validation['external_mesh_preserved']=contract['geometry']==original_digest
                validation['modifier_densities_preserved']=contract['densities']==list(candidate.densities)
                validation['non_target_settings_preserved']=contract['settings']==base_contract['settings'] and contract['part_settings']==base_contract['part_settings']
                validation['prepared_project_mesh_preserved']=before['geometry']==original_digest
                validation['valid']=all(validation[k] for k in ('valid','external_mesh_preserved','modifier_densities_preserved','non_target_settings_preserved','prepared_project_mesh_preserved'))
                row.update(status='PASS' if validation['valid'] else 'FAIL',metrics=asdict(metrics),validation=validation,project=str(exported),gcode=str(gcode))
                if not validation['valid']:
                    row['error']='Slicer output failed geometry, settings or regional-toolpath validation.'
                    if index==0: raise ValueError(row['error'])
                elif candidate.feasible:
                    good.append(row)
                    if index>0:
                        for check in ('modifiers','candidate_slicing','toolpath_changes','geometry_preservation','settings_preservation'):
                            compatibility['checks'][check]={'status':'PASS','detail':candidate.id}
            except Cancelled: raise
            except Exception as exc:
                row.update(status='FAIL',error=str(exc))
                if index==0: raise
        if not good:raise ValueError('No candidate passed both simulation/design limits and slicing validation. See the individual reports.')
        adaptive=[c for c in good if c['id'].startswith(('adaptive_','fea_')) and len(set(c['densities']))>1]
        if not adaptive and fea is None: raise ValueError('No regional candidate passed every heuristic and slicing constraint. See individual validation results.')
        rank=lambda c:(c['metrics']['mass_g'],c['metrics']['time_s'],c['densities'])
        report['best_adaptive_candidate']=min(adaptive,key=rank)['id'] if adaptive else None
        report['lightest_feasible_candidate']=min(good,key=rank)['id']
        selected=min(good,key=rank)
        if selected['id']=='baseline':
            raise ValueError('The uniform baseline is the lightest feasible sliced candidate. No improved optimized export is available for these settings.')
        progress('Reopening and reslicing the selected native project to validate reproducibility…')
        reopened,regcode=slicer.slice(Path(selected['project']),work/'reopen','verified')
        repeat=inspect_gcode(regcode,settings.contact_axis)
        if repeat.sparse_digest!=selected['metrics']['sparse_digest'] or abs(repeat.mass_g-selected['metrics']['mass_g'])>.02:
            raise ValueError('Selected project failed reproducible reopening / reslicing.')
        if project_contract(reopened)['geometry']!=original_digest: raise ValueError('Reopened project changed geometry.')
        model.verify_original()
        if fea_mesh is not None:
            from .fea import save_fields
            report['fea_selected']=save_fields(work,fea_mesh,selected['densities'])
            report['fea_selected']['relative_stiffness_ratio']=selected['fea']['refined']['relative_stiffness_ratio']
            compatibility['checks']['fea']={'status':'PASS','detail':'Numerical checks and user-defined screening limits passed; physical strength remains unvalidated.'}
        # Native projects remain internal validation artifacts, not print exports.
        for source,name in [(report['candidates'][0]['gcode'],'baseline.gcode'),
                            (selected['gcode'],'optimized.gcode')]:
            target=work/name;shutil.copy2(source,target)
            if target.stat().st_size==0: raise ValueError(f'Export is empty: {target}')
            report['exports'][name]=str(target)
        savings=(base_metrics.mass_g-selected['metrics']['mass_g'])/base_metrics.mass_g*100
        report.update(status='AUTOMATED SUCCESS',selected=selected['id'],savings_percent_estimated=savings,
                      message='Lightest candidate satisfying the selected heuristic and slicing constraints. Structural stiffness has not been independently verified.')
        if fea is not None:
            report['message']='Lightest slice-validated candidate in the FEA-ranked shortlist, satisfying the specified simulation limits on both meshes. Material and failure models are unvalidated; this is not a physical strength guarantee.'
            if fea.assessment=='relative_stiffness':
                report['message']='Weight-first result: lightest slice-validated candidate in the shortlist retaining the requested simulated stiffness relative to the baseline. The outer model shape is preserved. No actual thrust, failure load or physical strength was assessed.'
        if selected['id']=='uniform_lightweight': report['message']+=' The uniform lightweight control won; adaptive redistribution did not yield the lowest mass in this search.'
        if savings<=0: report['message']+=' No material reduction was achieved for this search.'
        if settings.fixed_hardware_mass_g is not None:
            report['total_moving_mass_g_estimated']={'baseline':base_metrics.mass_g+settings.fixed_hardware_mass_g,'optimized':selected['metrics']['mass_g']+settings.fixed_hardware_mass_g}
        # Add a genuinely sliced uniform comparison nearest the adaptive estimated mass.
        uniforms=[c for c in good if len(set(c['densities']))==1]
        if uniforms:
            match=min(uniforms,key=lambda c:abs(c['metrics']['mass_g']-selected['metrics']['mass_g']))
            report['nearest_uniform_comparison']={'candidate':match['id'],'mass_difference_g_estimated':match['metrics']['mass_g']-selected['metrics']['mass_g'],
                                                 'note':'Nearest available uniform design; exact mass matching is not guaranteed.'}
        for name in ('baseline_slicing','modifiers','candidate_slicing','toolpath_changes','geometry_preservation','settings_preservation','project_reopening','material_estimates'):
            compatibility['checks'][name]={'status':'PASS','detail':selected['id']}
    except Cancelled as exc:
        report.update(status='FAILED',message=str(exc),cancelled=True)
    except Exception as exc:
        report.update(status='FAILED',message=str(exc))
        compatibility['checks']['optimization']={'status':'FAIL','detail':str(exc)}
        report['manual_steps']=manual_steps(settings)
        (work/'manual_workflow.txt').write_text('Automatic generation failed. Switch to Mode: Semiautomated to use these steps.\n\n'+'\n'.join(report['manual_steps']))
    finally:
        report['compatibility']=compatibility
        (work/'compatibility_report.json').write_text(json.dumps(compatibility,indent=2))
        try:
            from .visualization import save_heatmap
            save_heatmap(regions,candidates,work/'infill_heatmap.png')
        except Exception as exc: report['plot_error']=str(exc)
        write_reports(work,report)
    return report
