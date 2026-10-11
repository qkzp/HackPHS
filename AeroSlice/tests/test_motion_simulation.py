from dataclasses import replace
from pathlib import Path
from threading import Event
import json
import numpy as np
import pytest
from scipy.spatial import ConvexHull
from AeroSlice.model import box_mesh, write_stl, geometry_digest
from AeroSlice.file_import import load_model
from AeroSlice.motion_simulation import MotionSettings, trajectory, projected_area, compare_motion


def shape_mesh(name):
    if name=='cube':return box_mesh(np.zeros(3),np.array([30.,30.,30.]))
    if name=='irregular':
        polygon=np.array([(0,0),(30,0),(30,10),(10,10),(10,30),(0,30)],float)
        points=np.vstack([np.c_[polygon,np.zeros(6)],np.c_[polygon,np.full(6,30)]])
        caps=[(0,1,3),(1,2,3),(0,3,5),(3,4,5)]
        faces=[tuple(reversed(face)) for face in caps]+[tuple(i+6 for i in face) for face in caps]
        for i in range(6):
            j=(i+1)%6;faces.extend([(i,j,j+6),(i,j+6,i+6)])
        return points[np.array(faces)]
    if name=='prism':
        base=np.array([(0,0),(30,0),(0,30)],float)
        points=np.vstack([np.c_[base,np.zeros(3)],np.c_[base,np.full(3,30)]])
    elif name=='cylinder':
        angles=np.linspace(0,2*np.pi,32,endpoint=False);base=15*np.c_[np.cos(angles),np.sin(angles)]
        points=np.vstack([np.c_[base,np.zeros(32)],np.c_[base,np.full(32,30)]])
    elif name=='sphere':
        index=np.arange(128);z=1-2*(index+.5)/128;theta=np.pi*(3-np.sqrt(5))*index;r=np.sqrt(1-z*z)
        points=15*np.c_[r*np.cos(theta),r*np.sin(theta),z]+[0,0,15]
    else:raise ValueError(name)
    hull=ConvexHull(points);faces=points[hull.simplices].copy()
    normals=np.cross(faces[:,1]-faces[:,0],faces[:,2]-faces[:,0]);flip=np.sum(normals*hull.equations[:,:3],axis=1)<0
    faces[flip]=faces[flip][:,[0,2,1]]
    return faces


@pytest.fixture(params=['cube','prism','cylinder','sphere','irregular'])
def uploaded_shape(request,tmp_path):
    path=tmp_path/(request.param+'.stl');write_stl(path,shape_mesh(request.param))
    return load_model(path)


def completed_report(model,before=10.,after=8.,rotation=(0,0,0)):
    # Unit-test contract fixture only; real slicing is exercised separately below.
    return {'status':'AUTOMATED SUCCESS','input_sha256':model.sha256,'settings':{'rotation':rotation},'selected':'optimized',
            'candidates':[{'id':'baseline','status':'PASS','metrics':{'mass_g':before},'densities':[15,15,15],'validation':{'external_mesh_preserved':True}},
                          {'id':'optimized','status':'PASS','metrics':{'mass_g':after},'densities':[10,15,10],'validation':{'external_mesh_preserved':True}}]}


def test_arbitrary_shapes_use_uploaded_geometry_and_identical_mass_physics(uploaded_shape):
    model=uploaded_shape;original=model.path.read_bytes();settings=MotionSettings()
    result=compare_motion(model,completed_report(model),settings)
    assert geometry_digest(result['triangles'])==geometry_digest(model.triangles)
    assert result['payload_projection']['area_mm2']>0
    assert result['optimized']['time_to_target_s']<result['original']['time_to_target_s']
    np.testing.assert_allclose(result['original']['height_m'],trajectory(10,settings)['height_m'])
    assert model.path.read_bytes()==original


def test_equal_masses_equal_motion_and_zero_improvement(uploaded_shape):
    result=compare_motion(uploaded_shape,completed_report(uploaded_shape,10,10),MotionSettings())
    for field in ('time_s','height_m','velocity_m_s','acceleration_m_s2'):
        np.testing.assert_array_equal(result['original'][field],result['optimized'][field])
    assert result['climb_time_reduction_percent']==0


def test_numerical_integration_matches_constant_acceleration_solution():
    settings=MotionSettings(friction_n=0,drag_coefficient=0,fixed_mass_g=100)
    result=trajectory(10,settings);a=(settings.thrust_n-.11*settings.gravity_m_s2)/.11
    assert result['time_to_target_s']==pytest.approx(np.sqrt(2*settings.target_height_m/a),rel=1e-7)
    np.testing.assert_allclose(result['height_m'],.5*a*result['time_s']**2,atol=1e-9)


def test_drag_integration_matches_quadratic_drag_analytic_arrival():
    settings=MotionSettings();mass=.11;force=settings.thrust_n-mass*settings.gravity_m_s2-settings.friction_n
    drag=.5*settings.air_density_kg_m3*settings.drag_coefficient*settings.assembly_area_m2
    expected=np.arccosh(np.exp(settings.target_height_m*drag/mass))*mass/np.sqrt(force*drag)
    assert trajectory(10,settings)['time_to_target_s']==pytest.approx(expected,rel=1e-7)


def test_fixed_mass_drag_friction_and_stall():
    settings=MotionSettings();light=trajectory(5,settings);heavy=trajectory(30,settings)
    assert light['time_to_target_s']<heavy['time_to_target_s']
    assert trajectory(10,replace(settings,fixed_mass_g=120))['time_to_target_s']>trajectory(10,settings)['time_to_target_s']
    assert trajectory(10,replace(settings,friction_n=.1))['time_to_target_s']>trajectory(10,settings)['time_to_target_s']
    assert trajectory(10,replace(settings,drag_coefficient=0))['time_to_target_s']<trajectory(10,settings)['time_to_target_s']
    stalled=trajectory(10,replace(settings,thrust_n=.1))
    assert stalled['time_to_target_s'] is None and not stalled['height_m'].any()
    not_reached=trajectory(10,replace(settings,duration_s=.01))
    assert not_reached['time_to_target_s'] is None and 0<not_reached['height_m'][-1]<1


def test_actual_concave_projection_is_not_hull_or_bounding_box():
    triangles=shape_mesh('irregular')
    area=projected_area(triangles,resolution=300)
    assert area['area_mm2']==pytest.approx(500,abs=1e-7)
    assert area['area_mm2']<900
    assert projected_area(shape_mesh('prism'),resolution=300)['area_mm2']==pytest.approx(450,abs=1e-7)


def test_orientation_and_projection_budget(uploaded_shape):
    rotation=(90,0,0)
    result=compare_motion(uploaded_shape,completed_report(uploaded_shape,rotation=rotation),MotionSettings())
    np.testing.assert_allclose(result['triangles'],uploaded_shape.oriented(rotation))
    assert projected_area(np.tile(shape_mesh('cube'),(4200,1,1)))['area_mm2'] is None


@pytest.mark.parametrize('mutation',['stale','failed','missing_mass','shape_changed','unsliced'])
def test_no_stale_missing_or_unverified_cases(cube,mutation):
    model=load_model(cube);report=completed_report(model)
    if mutation=='stale':report['input_sha256']='different'
    if mutation=='failed':report['status']='FAILED'
    if mutation=='missing_mass':report['candidates'][1]['metrics']={}
    if mutation=='shape_changed':report['candidates'][1]['validation']['external_mesh_preserved']=False
    if mutation=='unsliced':report['candidates'][1]['status']='NOT TESTED'
    with pytest.raises(ValueError):compare_motion(model,report,MotionSettings())


@pytest.mark.parametrize('settings',[MotionSettings(thrust_n=float('nan')),MotionSettings(fixed_mass_g=-1),MotionSettings(target_height_m=0),MotionSettings(duration_s=0)])
def test_bad_inputs(settings):
    with pytest.raises(ValueError):trajectory(10,settings)


def test_cancelled_calculation():
    cancel=Event();cancel.set()
    with pytest.raises(InterruptedError):trajectory(10,MotionSettings(),cancel)


@pytest.mark.integration
@pytest.mark.parametrize('name',['prism','cylinder','sphere','irregular'])
def test_real_slicing_varied_models_and_motion(name,tmp_path):
    from AeroSlice.slicer import OrcaSlicer,Profiles,DEFAULT_EXECUTABLE,project_contract
    from AeroSlice.optimizer import optimize
    from AeroSlice.structural import weight_saving_settings
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    path=tmp_path/(name+'.stl');write_stl(path,shape_mesh(name));model=load_model(path);original=path.read_bytes()
    report=optimize(model,weight_saving_settings(15,candidate_count=3),tmp_path/'runs',OrcaSlicer(),Profiles())
    assert report['status']=='AUTOMATED SUCCESS',report['message']
    result=compare_motion(model,report,MotionSettings())
    assert result['original']['payload_mass_g']==report['candidates'][0]['metrics']['mass_g']
    selected=next(c for c in report['candidates'] if c['id']==report['selected'])
    assert result['optimized']['payload_mass_g']==selected['metrics']['mass_g']
    assert result['climb_time_reduction_percent']>0
    assert project_contract(Path(selected['project']))['geometry']==geometry_digest(result['triangles'])
    assert path.read_bytes()==original
    assert all(Path(file).is_file() for file in report['exports'].values())
