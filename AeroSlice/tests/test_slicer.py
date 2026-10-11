from pathlib import Path
from threading import Event
import json
import numpy as np
import pytest
from AeroSlice.file_import import load_model
from AeroSlice.model import box_mesh,write_stl,geometry_digest
from AeroSlice.optimizer import optimize
from AeroSlice.slicer import OrcaSlicer,Profiles,DEFAULT_EXECUTABLE,project_contract
from AeroSlice.structural import Settings
from AeroSlice.validation import inspect_gcode,validate_difference


def test_missing_executable_report(tmp_path):
    r=OrcaSlicer('/not/a/program').discover(tmp_path)
    assert r['checks']['executable']['status']=='FAIL'


def test_too_small_for_first_layer_stops_before_cli(tmp_path,monkeypatch):
    p=tmp_path/'tiny.stl';write_stl(p,box_mesh(np.zeros(3),np.full(3,.0254)))
    slicer=OrcaSlicer()
    monkeypatch.setattr(slicer,'prepare_profiles',lambda *args: {'process':{'initial_layer_print_height':'.2'}})
    def unexpected_cli(*args):
        pytest.fail('Unprintable size should be diagnosed before launching OrcaSlicer')
    monkeypatch.setattr(slicer,'run',unexpected_cli)
    with pytest.raises(ValueError,match='Size: 0.0254 × 0.0254 × 0.0254 mm'):
        slicer.establish_project(p,tmp_path/'setup',Profiles(),20)

@pytest.mark.integration
@pytest.mark.parametrize('shape',['cube','wedge_rotated'])
def test_real_end_to_end(tmp_path,shape):
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('NOT TESTED: installed OrcaSlicer required')
    mesh=box_mesh(np.array([-15.,-15.,0.]),np.array([15.,15.,30.]))
    if shape=='wedge_rotated':
        mesh=mesh.copy()
        # Sloped top: half-height at negative x; watertight tapered payload.
        upper=mesh[:,:,2]>20;mesh[:,:,2][upper]=22.5+.25*mesh[:,:,0][upper]
    path=tmp_path/'payload.stl';write_stl(path,mesh);original=path.read_bytes()
    settings=Settings(candidate_count=5,rotation=(0.,0.,90.) if shape!='cube' else (0.,0.,0.))
    r=optimize(load_model(path),settings,tmp_path/'out',OrcaSlicer(),Profiles())
    assert r['status']=='AUTOMATED SUCCESS',r['message']
    work=Path(r['run_directory']);assert path.read_bytes()==original
    assert set(r['exports'])=={'baseline.3mf','optimized.3mf','baseline.gcode','optimized.gcode'}
    a=inspect_gcode(work/'baseline.gcode');b=inspect_gcode(work/'optimized.gcode')
    assert a.sparse_digest!=b.sparse_digest;assert a.mass_g>0 and b.mass_g>0
    assert a.filament_mm>0 and b.time_s>0
    assert project_contract(work/'baseline.3mf')['geometry']==project_contract(work/'optimized.3mf')['geometry']
    assert project_contract(work/'baseline.3mf')['settings']==project_contract(work/'optimized.3mf')['settings']
    assert len(project_contract(work/'optimized.3mf')['densities'])==3
    assert r['compatibility']['checks']['project_reopening']['status']=='PASS'
    assert r['compatibility']['checks']['physical_printer']['status']=='NOT TESTED'


def test_fake_gcode_rejected(tmp_path):
    p=tmp_path/'fake.gcode';p.write_text('; fabricated demonstration\nG1 X1 Y2 E1\n')
    with pytest.raises(ValueError,match='Missing authentic'):inspect_gcode(p)

@pytest.mark.integration
def test_profile_detection_and_incompatible_rejected(tmp_path):
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('NOT TESTED: installed OrcaSlicer required')
    slicer=OrcaSlicer();r=slicer.discover(tmp_path)
    assert r['checks']['profile_discovery']['status']=='PASS'
    assert slicer.resolve(Profiles().machine)['printer_model']=='Flashforge Adventurer 5M'
    with pytest.raises(ValueError,match='not Pro'):
        slicer.prepare_profiles(Profiles(machine='Flashforge Adventurer 5M Pro 0.4 Nozzle'),tmp_path/'wrong',20)


def test_external_process_timeout_and_cancellation(tmp_path):
    """Process-control test only; this fixture is not an OrcaSlicer integration test."""
    import os
    from AeroSlice.slicer import Cancelled
    fake=tmp_path/'long_process';fake.write_text('#!/bin/sh\nexec sleep 10\n');fake.chmod(0o755)
    slicer=OrcaSlicer(str(fake),timeout=.2)
    with pytest.raises(TimeoutError):slicer.run([],tmp_path/'timeout.log')
    flag=Event();flag.set();slicer=OrcaSlicer(str(fake),cancel=flag)
    with pytest.raises(Cancelled):slicer.run([],tmp_path/'cancel.log')


@pytest.mark.integration
def test_native_project_reimport_and_x_axis_slabs(tmp_path,cube):
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('NOT TESTED: installed OrcaSlicer required')
    slicer=OrcaSlicer();slicer.discover(tmp_path/'discover')
    seed,_=slicer.establish_project(cube,tmp_path/'setup',Profiles(),20)
    uploaded=load_model(seed);assert uploaded.kind=='Orca project 3MF'
    result=optimize(uploaded,Settings(candidate_count=4,contact_axis='x',load_axis='x',contact_face='max'),tmp_path/'out',slicer,Profiles())
    assert result['status']=='AUTOMATED SUCCESS',result['message']
    selected=next(c for c in result['candidates'] if c['id']==result['selected'])
    assert selected['validation']['infill_geometry_verified']
    uploaded.verify_original()
