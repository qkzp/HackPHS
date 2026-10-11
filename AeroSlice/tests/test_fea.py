"""Numerical verification, not printer/material validation."""
from dataclasses import replace
from threading import Event
import numpy as np
import pytest
from AeroSlice.fea import FEASettings,VoxelFEA,strain_matrix,direct_search,shell_from_profiles
from AeroSlice.model import box_mesh
from AeroSlice.structural import Settings
from AeroSlice.slicer import Cancelled


def isotropic(**kwargs):
    values=dict(e_xy_mpa=1000.,e_z_mpa=1000.,nu_xy=0.,nu_xz=0.,g_xz_mpa=500.,shell_mm=(0.,)*6,mesh_cells=4)
    values.update(kwargs);return FEASettings(**values)


def test_affine_patch_reproduces_constant_strain():
    from AeroSlice.fea import OFFSETS
    h=np.array([2.,3.,4.]);nodes=OFFSETS*h
    gradient=np.array([[.01,.02,.03],[.04,.05,.06],[.07,.08,.09]])
    u=(nodes@gradient.T+np.array([2.,3.,4.])).ravel()
    expected=[.01,.05,.09,.06,.14,.1]
    for point in [(0,0,0),(-.4,.7,.2),(.57,-.57,.57)]:
        assert np.allclose(strain_matrix(h,point)@u,expected,atol=1e-14)


def test_axial_bar_matches_closed_form_and_force_balance():
    mesh=VoxelFEA(box_mesh(np.zeros(3),np.full(3,10.)),isotropic())
    r=mesh.solve((100,)*3)
    # delta=F L/(A E); sigma=F/A.
    assert r['max_displacement_mm']==pytest.approx(10*10/(100*1000),rel=1e-10)
    assert r['max_von_mises_mpa']==pytest.approx(10/100,rel=1e-10)
    assert r['relative_residual']<1e-10 and r['relative_force_balance_error']<1e-10


def test_linearity_load_modulus_and_density():
    triangles=box_mesh(np.zeros(3),np.full(3,10.));s=isotropic()
    a=VoxelFEA(triangles,s).solve((100,)*3)
    doubled=VoxelFEA(triangles,replace(s,force_n=20)).solve((100,)*3)
    stiffer=VoxelFEA(triangles,replace(s,e_xy_mpa=2000,e_z_mpa=2000,g_xz_mpa=1000)).solve((100,)*3)
    porous=VoxelFEA(triangles,s).solve((50,)*3)
    assert doubled['max_displacement_mm']==pytest.approx(2*a['max_displacement_mm'])
    assert doubled['max_von_mises_mpa']==pytest.approx(2*a['max_von_mises_mpa'])
    assert stiffer['max_displacement_mm']==pytest.approx(a['max_displacement_mm']/2)
    assert porous['max_displacement_mm']==pytest.approx(4*a['max_displacement_mm'])
    assert porous['material_volume_proxy_mm3']==pytest.approx(a['material_volume_proxy_mm3']/2)


def test_cantilever_refinement_approaches_beam_solution():
    s=isotropic(force_n=1,force_axis='z');triangles=box_mesh(np.zeros(3),np.array([24.,6.,6.]))
    coarse=VoxelFEA(triangles,s,'x',cells=4).solve((100,)*3)
    fine=VoxelFEA(triangles,s,'x',cells=12).solve((100,)*3)
    expected=24**3/(3*1000*(6*6**3/12))+24/((5/6)*500*36)
    assert abs(fine['max_displacement_mm']-expected)<abs(coarse['max_displacement_mm']-expected)
    assert fine['max_displacement_mm']==pytest.approx(expected,rel=.15)


def test_exhaustive_search_limits_and_unstable_mesh_rejection():
    triangles=box_mesh(np.zeros(3),np.full(3,10.))
    settings=Settings(minimum=10,maximum=20,densities=(10,20),protected_minima=(10,10,10))
    s=isotropic(force_n=.01,mesh_tolerance=.5)
    result,_=direct_search(triangles,settings,s,Event(),lambda _:None)
    assert result['combinations_tested']==8
    assert len({tuple(r['densities']) for r in result['candidates']})==8
    assert result['feasible_count']>0
    tight,_=direct_search(triangles,settings,replace(s,max_displacement_mm=1e-12),Event(),lambda _:None)
    assert tight['feasible_count']==0
    sensitive,_=direct_search(triangles,settings,replace(s,force_axis='x',mesh_tolerance=1e-12),Event(),lambda _:None)
    assert any(not r['mesh_check_pass'] and not r['feasible'] for r in sensitive['candidates'])
    assert all(a<b for a,b in zip(result['coarse_mesh']['grid_shape'],result['refined_mesh']['grid_shape']))


def test_cancellation_and_disconnected_mesh():
    flag=Event();flag.set()
    with pytest.raises(Cancelled):VoxelFEA(box_mesh(np.zeros(3),np.full(3,10.)),isotropic(),cancel=flag)
    triangles=np.concatenate([box_mesh(np.zeros(3),np.full(3,4.)),box_mesh(np.array([8.,0.,0.]),np.array([12.,4.,4.]))])
    with pytest.raises(ValueError,match='disconnected'):VoxelFEA(triangles,isotropic(),cells=12)


@pytest.mark.parametrize('changes',[{'force_n':0},{'e_z_mpa':-1},{'mesh_cells':3},{'mesh_tolerance':0},{'force_n':float('nan')},{'material_source':''},{'safety_factor':.5}])
def test_invalid_inputs(changes):
    with pytest.raises(ValueError):replace(isotropic(),**changes).validate()


def test_shell_thickness_comes_from_actual_print_settings():
    resolved={'machine':{'nozzle_diameter':['0.4']},'process':{'layer_height':'.2','wall_loops':'2','outer_wall_line_width':'.42','inner_wall_line_width':'.45','bottom_shell_layers':'3','top_shell_layers':'5'}}
    assert shell_from_profiles(resolved)==pytest.approx((.87,.87,.87,.87,.6,1.))

@pytest.mark.integration
def test_real_fea_guided_export_and_no_feasible_fallback(cube,tmp_path):
    from pathlib import Path
    from AeroSlice.file_import import load_model
    from AeroSlice.optimizer import optimize
    from AeroSlice.slicer import OrcaSlicer,Profiles,DEFAULT_EXECUTABLE
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    model=load_model(cube);original=cube.read_bytes()
    settings=Settings(candidate_count=3,minimum=10,maximum=20,densities=(10,20),protected_minima=(10,10,10))
    s=FEASettings(mesh_cells=4,mesh_tolerance=.5,force_n=1)
    r=optimize(model,settings,tmp_path/'runs',OrcaSlicer(),Profiles(),fea=s)
    assert r['status']=='AUTOMATED SUCCESS',r['message']
    assert r['fea']['combinations_tested']==8
    assert r['fea_selected']['limits_pass']
    selected=next(c for c in r['candidates'] if c['id']==r['selected'])
    assert selected['fea']['feasible']
    assert set(r['exports'])=={'baseline.gcode','optimized.gcode'}
    folder=Path(r['run_directory'])
    assert (folder/'fea_search.csv').is_file() and (folder/'fea_selected.png').is_file()
    assert cube.read_bytes()==original
    failed=optimize(model,settings,tmp_path/'failed',OrcaSlicer(),Profiles(),fea=replace(s,max_displacement_mm=1e-12))
    assert failed['status']=='FAILED' and not failed['exports']
    assert failed['fea']['feasible_count']==0
    assert not list(Path(failed['run_directory']).rglob('*.gcode'))


def test_relative_screening_needs_no_actual_load_and_ignores_absolute_limits():
    triangles=box_mesh(np.zeros(3),np.full(3,10.))
    settings=Settings(minimum=10,maximum=20,densities=(10,20),protected_minima=(10,10,10))
    s=isotropic(assessment='relative_stiffness',minimum_stiffness_ratio=.75,mesh_tolerance=.5)
    result,_=direct_search(triangles,settings,s,Event(),lambda _:None)
    changed,_=direct_search(triangles,settings,replace(s,force_n=1000,strength_xy_mpa=1e-9,strength_z_mpa=1e-9,shear_mpa=1e-9,max_displacement_mm=1e-15),Event(),lambda _:None)
    assert result['inputs']['force_n']==changed['inputs']['force_n']==1
    a={tuple(r['densities']):r for r in result['candidates']};b={tuple(r['densities']):r for r in changed['candidates']}
    assert a[(20,20,20)]['refined']['relative_stiffness_ratio']==pytest.approx(1)
    assert a[(10,10,10)]['refined']['relative_stiffness_ratio']==pytest.approx(.25)
    assert not a[(10,10,10)]['feasible']
    assert b[(20,20,20)]['feasible'] and not b[(20,20,20)]['refined']['limits_pass']
    for values in a:
        assert a[values]['feasible']==b[values]['feasible']
        assert a[values]['refined']['relative_stiffness_ratio']==pytest.approx(b[values]['refined']['relative_stiffness_ratio'])
