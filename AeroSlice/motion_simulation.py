"""Deterministic vertical carriage physics using completed slicer mass estimates.

STL coordinates are mm. The dynamics use kg, m, s, N; the shared exposed carriage
area and Cd determine drag. Mesh projection is descriptive, never a mass or drag
substitute. No optimization settings or external surfaces are modified.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from threading import Event
import math
import numpy as np
from scipy.integrate import solve_ivp


@dataclass(frozen=True)
class MotionSettings:
    thrust_n: float = 1.5
    fixed_mass_g: float = 100.
    friction_n: float = .05
    assembly_area_m2: float = .01
    drag_coefficient: float = 1.
    air_density_kg_m3: float = 1.225
    target_height_m: float = 1.
    duration_s: float = 30.
    gravity_m_s2: float = 9.80665

    def validate(self):
        values=asdict(self)
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError('Motion inputs must be finite numbers.')
        for key in ('fixed_mass_g','friction_n','assembly_area_m2','drag_coefficient','air_density_kg_m3','thrust_n'):
            if values[key]<0:raise ValueError(f'{key.replace("_", " ")} cannot be negative.')
        if self.target_height_m<=0 or self.gravity_m_s2<=0 or not 0<self.duration_s<=600:
            raise ValueError('Height and gravity must be positive; simulation duration must be between 0 and 600 seconds.')


def trajectory(payload_mass_g: float, settings: MotionSettings, cancel: Event | None = None):
    settings.validate()
    if not math.isfinite(payload_mass_g) or payload_mass_g<=0:
        raise ValueError('A positive completed OrcaSlicer payload mass is required.')
    mass=(payload_mass_g+settings.fixed_mass_g)/1000
    drag=.5*settings.air_density_kg_m3*settings.drag_coefficient*settings.assembly_area_m2
    lift=settings.thrust_n-mass*settings.gravity_m_s2-settings.friction_n
    if lift<=0:
        # Starts from rest on a bottom stop; insufficient thrust cannot cause
        # motion below the rail origin. Constant thrust never changes this state.
        time=np.linspace(0,settings.duration_s,301)
        zero=np.zeros_like(time)
        return dict(payload_mass_g=payload_mass_g,total_mass_g=mass*1000,
                    time_s=time,height_m=zero.copy(),velocity_m_s=zero.copy(),acceleration_m_s2=zero.copy(),
                    time_to_target_s=None,status='Cannot lift from rest',initial_net_lift_n=lift)
    def derivative(time,state):
        if cancel and cancel.is_set():raise InterruptedError('Motion simulation cancelled.')
        velocity=state[1]
        force=settings.thrust_n-mass*settings.gravity_m_s2-drag*velocity*abs(velocity)-settings.friction_n
        return [velocity,force/mass]
    def arrival(time,state):return state[0]-settings.target_height_m
    arrival.terminal=True;arrival.direction=1
    solution=solve_ivp(derivative,(0,settings.duration_s),(0.,0.),events=arrival,
                       dense_output=True,rtol=1e-8,atol=1e-10,max_step=.05)
    if not solution.success:raise ValueError('Vertical motion integration failed: '+solution.message)
    reached=bool(len(solution.t_events[0]))
    end=float(solution.t_events[0][0]) if reached else settings.duration_s
    time=np.linspace(0,end,401);height,velocity=solution.sol(time)
    acceleration=(lift-drag*velocity*np.abs(velocity))/mass
    return dict(payload_mass_g=payload_mass_g,total_mass_g=mass*1000,time_s=time,
                height_m=height,velocity_m_s=velocity,acceleration_m_s2=acceleration,
                time_to_target_s=end if reached else None,
                status='Reached target' if reached else 'Target not reached within duration',initial_net_lift_n=lift)


def projected_area(triangles, axis='z', cancel=None, resolution=256):
    """Union of actual triangle projections by scanline quadrature (not a hull).

    Overlapping facets count once, and concave silhouettes/holes remain empty.
    Two raster resolutions provide a discretization difference, not a validated
    uncertainty bound. Large meshes skip this optional diagnostic to bound cost.
    """
    if axis not in 'xyz' or len(axis)!=1:raise ValueError('Projection axis must be x, y or z.')
    if len(triangles)>50_000:
        return {'area_mm2':None,'method':'Unavailable: optional projection budget is 50,000 facets; dynamics use assembly area.'}
    plane=np.delete(np.asarray(triangles,dtype=float),'xyz'.index(axis),axis=2)
    edges=np.stack([plane, np.roll(plane,-1,axis=1)],axis=2)
    p,q=edges[:,:,0,:],edges[:,:,1,:]
    bottom=plane[:,:,1].min();top=plane[:,:,1].max()
    def integrate(rows):
        step=(top-bottom)/rows;area=0.
        for y in bottom+(np.arange(rows)+.5)*step:
            if cancel and cancel.is_set():raise InterruptedError('Motion simulation cancelled.')
            active=(np.minimum(p[:,:,1],q[:,:,1])<=y)&(np.maximum(p[:,:,1],q[:,:,1])>y)
            denominator=q[:,:,1]-p[:,:,1]
            x=p[:,:,0]+np.divide(y-p[:,:,1],denominator,out=np.zeros_like(denominator),where=active)*(q[:,:,0]-p[:,:,0])
            low=np.where(active,x,np.inf).min(axis=1);high=np.where(active,x,-np.inf).max(axis=1)
            usable=high>low;low,high=low[usable],high[usable]
            if len(low):
                order=np.argsort(low);low,high=low[order],high[order]
                previous=np.r_[-np.inf,np.maximum.accumulate(high)[:-1]]
                area+=float(np.maximum(0,high-np.maximum(low,previous)).sum())*step
        return area
    coarse=integrate(resolution//2);fine=integrate(resolution)
    return {'area_mm2':fine,'coarse_fine_difference_mm2':abs(fine-coarse),
            'axis':axis,'method':f'Actual projected triangle union; {resolution}-scanline numerical approximation; not used for enclosed-payload drag.'}


def sliced_cases(model,report):
    """Reject stale, failed, unsliced, or geometry-changing report pairs."""
    if report is None or report.get('status')!='AUTOMATED SUCCESS':
        raise ValueError('Waiting for a successful original/optimized slicing comparison.')
    model.verify_original()
    if report.get('input_sha256')!=model.sha256:
        raise ValueError('The slicing results belong to a different uploaded model. Optimize this model first.')
    candidates=report.get('candidates') or []
    baseline=next((c for c in candidates if c.get('id')=='baseline'),None)
    selected=next((c for c in candidates if c.get('id')==report.get('selected')),None)
    result=[]
    for case in (baseline,selected):
        if case is None or case.get('status')!='PASS' or not case.get('validation',{}).get('external_mesh_preserved'):
            raise ValueError('Completed slices with verified unchanged external geometry are required.')
        mass=case.get('metrics',{}).get('mass_g')
        if not isinstance(mass,(int,float)) or not math.isfinite(mass) or mass<=0:
            raise ValueError('Completed OrcaSlicer mass estimates are missing or invalid.')
        densities=case.get('densities',())
        if len(densities)!=3 or not all(isinstance(d,(int,float)) and math.isfinite(d) and 0<=d<=100 for d in densities):
            raise ValueError('Completed regional infill settings are missing or invalid.')
        result.append({'id':case['id'],'payload_mass_g':mass,'densities':tuple(densities)})
    return result


def compare_motion(model,report,settings: MotionSettings,cancel=None):
    cases=sliced_cases(model,report)
    rotation=tuple(report.get('settings',{}).get('rotation',()))
    if len(rotation)!=3 or not np.isfinite(rotation).all():
        raise ValueError('Completed model orientation settings are missing or invalid.')
    triangles=model.oriented(rotation)
    original=trajectory(cases[0]['payload_mass_g'],settings,cancel)
    optimized=trajectory(cases[1]['payload_mass_g'],settings,cancel)
    before,after=original['time_to_target_s'],optimized['time_to_target_s']
    improvement=(before-after)/before*100 if before is not None and after is not None else None
    return {'input_sha256':model.sha256,'triangles':triangles,'orientation_degrees':rotation,
            'bounds_mm':np.array([triangles.min(axis=(0,1)),triangles.max(axis=(0,1))]),
            'dimensions_mm':np.ptp(triangles,axis=(0,1)),
            'payload_projection':projected_area(triangles,cancel=cancel),
            'original':original,'optimized':optimized,'cases':cases,'settings':asdict(settings),
            'climb_time_reduction_percent':improvement,
            'source':'PHYSICS SIMULATION · unvalidated apparatus assumptions; OrcaSlicer estimated payload masses',
            'drag_scope':'Same exposed carriage area and drag coefficient for both cases; enclosed STL silhouette is diagnostic only.'}
