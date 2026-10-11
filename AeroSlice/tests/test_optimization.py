import numpy as np
import pytest
from AeroSlice.file_import import load_model
from AeroSlice.structural import Settings,make_regions
from AeroSlice.optimizer import generate_candidates,feasible,optimize
from AeroSlice.slicer import OrcaSlicer,Profiles


def test_determinism_constraints_and_search(cube):
    model=load_model(cube);s=Settings();regions,notes=make_regions(model.triangles,s)
    a=generate_candidates(regions,s);assert a==generate_candidates(regions,s);assert len(a)==8
    assert regions[0].demand>regions[2].demand
    assert a[0].densities==(20.,20.,20.)
    assert any(len(set(c.densities))>1 for c in a)
    for c in a:
        assert feasible(c.densities,regions,s)
        assert all(r.minimum<=d<=r.maximum for r,d in zip(regions,c.densities))
    assert any('heuristic' in note.lower() for note in notes)


def test_reversed_contact_and_transverse(cube):
    model=load_model(cube)
    regions,_=make_regions(model.triangles,Settings(contact_face='max'))
    assert regions[2].demand>regions[0].demand
    regions,_=make_regions(model.triangles,Settings(load_axis='x'))
    assert len(set(r.demand for r in regions))==1

@pytest.mark.parametrize('settings',[Settings(minimum=25),Settings(protected_minima=(100,10,10)),Settings(coverage=2),Settings(acceleration=float('nan')),Settings(candidate_count=1)])
def test_invalid_settings(settings):
    with pytest.raises(ValueError):settings.validate()


def test_impossible_search(cube):
    model=load_model(cube);s=Settings(minimum=20,maximum=20,protected_minima=(20,20,20))
    regions,_=make_regions(model.triangles,s);assert len(generate_candidates(regions,s))==1


def test_no_fake_slicing_with_missing_executable(cube,tmp_path):
    result=optimize(load_model(cube),Settings(),tmp_path/'output',OrcaSlicer('/does/not/exist'),Profiles())
    assert result['status']=='SEMIAUTOMATED'
    assert not result['exports'];assert not list((tmp_path/'output').rglob('*.gcode'));assert not list((tmp_path/'output').rglob('*.3mf'))
    assert all('metrics' not in c for c in result['candidates'])
    assert result['compatibility']['checks']['physical_printer']['status']=='NOT TESTED'


def test_unfastened_contact_loss_is_reported(cube):
    regions,notes=make_regions(load_model(cube).triangles,Settings(acceleration=-12.))
    assert len(set(r.demand for r in regions))==1
    assert any('contact may be lost' in n for n in notes)


def test_cancellation_is_failed_without_fake_exports(cube,tmp_path):
    from threading import Event
    event=Event();event.set()
    result=optimize(load_model(cube),Settings(),tmp_path/'output',OrcaSlicer('/not/a/program',cancel=event),Profiles())
    assert result['status']=='FAILED';assert result['cancelled'];assert not result['exports']
    assert not list((tmp_path/'output').rglob('*.gcode'))
