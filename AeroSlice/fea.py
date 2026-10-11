"""Experimental voxel Q1 linear-elastic FEA and exhaustive three-region search.

N, mm, MPa units. Transversely isotropic solid material, axes tied to print Z.
Core elasticity and screening strengths scale by user-specified density powers.
Fractional voxel shell/core mixture is an approximation, not a resolved toolpath
or a calibrated failure model. No physical validation or safety certification.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
from itertools import product
from threading import Event
from pathlib import Path
import csv
import json
import warnings
import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve, MatrixRankWarning
from .slicer import Cancelled

LIMITATIONS = [
    'Experimental linear static simulation: small strain, small displacement, no buckling, fatigue, creep, impact or thermal effects.',
    'Full selected face is clamped; a total static force is distributed over the opposite face. This is not the earlier unfastened support assumption.',
    'Only the supplied total force is modeled; self-weight, acceleration and hardware mass are not automatically added in FEA mode.',
    'Voxel geometry approximates the STL; holes and features below the mesh spacing may disappear.',
    'Print-Z transverse isotropy, density power laws and fractional shell/core mixing are user assumptions, not calibrated printer/material properties.',
    'Stress utilization is a homogenized screening criterion, not a prediction of filament fracture or layer adhesion failure.',
    'Two mesh resolutions are compared; this sensitivity check is not proof of asymptotic convergence.',
    'Predictions are unvalidated. Physical strength, printer and hardware performance remain NOT TESTED.',
]

@dataclass
class FEASettings:
    assessment: str = 'absolute_limits'
    minimum_stiffness_ratio: float = .75
    force_n: float = 10.
    force_axis: str = 'z'
    e_xy_mpa: float = 2000.
    e_z_mpa: float = 1000.
    nu_xy: float = .3
    nu_xz: float = .25
    g_xz_mpa: float = 400.
    strength_xy_mpa: float = 25.
    strength_z_mpa: float = 12.
    shear_mpa: float = 8.
    stiffness_power: float = 2.
    strength_power: float = 1.5
    safety_factor: float = 2.
    max_displacement_mm: float = .5
    mesh_cells: int = 6
    mesh_tolerance: float = .2
    material_source: str = 'DEMONSTRATION assumptions; not measured or calibrated'
    # x-/x+/y-/y+/z-/z+ shell thickness. Runtime derives these from slicer.
    shell_mm: tuple[float, ...] = (.8,.8,.8,.8,.6,1.)

    def validate(self):
        numbers=[self.minimum_stiffness_ratio,self.force_n,self.e_xy_mpa,self.e_z_mpa,self.nu_xy,self.nu_xz,self.g_xz_mpa,
                 self.strength_xy_mpa,self.strength_z_mpa,self.shear_mpa,self.stiffness_power,
                 self.strength_power,self.safety_factor,self.max_displacement_mm,self.mesh_tolerance,*self.shell_mm]
        if not np.isfinite(numbers).all():raise ValueError('FEA inputs must be finite.')
        if self.assessment not in ('absolute_limits','relative_stiffness') or not 0<self.minimum_stiffness_ratio<=1:
            raise ValueError('Choose absolute_limits or relative_stiffness and a stiffness retention ratio in (0, 1].')
        if self.force_n==0 or self.force_axis not in ('x','y','z'):raise ValueError('FEA requires a nonzero signed force and an X, Y or Z force direction.')
        if min(self.e_xy_mpa,self.e_z_mpa,self.g_xz_mpa,self.strength_xy_mpa,self.strength_z_mpa,self.shear_mpa,self.max_displacement_mm)<=0:
            raise ValueError('FEA moduli, strengths and displacement limit must be positive.')
        if not 0<=self.nu_xy<.49 or not 0<=self.nu_xz<.49:raise ValueError('FEA Poisson ratios must be in [0, 0.49).')
        if self.safety_factor<1 or not 1<=self.stiffness_power<=4 or not 1<=self.strength_power<=4:
            raise ValueError('FEA safety factor must be ≥1 and density powers must be 1–4.')
        if not isinstance(self.mesh_cells,int) or not 4<=self.mesh_cells<=14 or not 0<self.mesh_tolerance<=.5:
            raise ValueError('FEA mesh cells must be 4–14; mesh sensitivity tolerance must be >0 and ≤0.5.')
        if len(self.shell_mm)!=6 or min(self.shell_mm)<0:raise ValueError('Six nonnegative shell thicknesses are required.')
        if not self.material_source.strip():raise ValueError('Describe the source of the FEA material inputs.')
        elasticity(self)


def elasticity(s: FEASettings) -> np.ndarray:
    """Engineering strain order xx, yy, zz, xy, yz, xz; reciprocal compliance."""
    c=np.zeros((6,6));c[0,0]=c[1,1]=1/s.e_xy_mpa;c[2,2]=1/s.e_z_mpa
    c[0,1]=c[1,0]=-s.nu_xy/s.e_xy_mpa
    c[0,2]=c[2,0]=c[1,2]=c[2,1]=-s.nu_xz/s.e_xy_mpa
    c[3,3]=2*(1+s.nu_xy)/s.e_xy_mpa;c[4,4]=c[5,5]=1/s.g_xz_mpa
    if np.linalg.eigvalsh(c).min()<=0:raise ValueError('FEA material compliance is not positive definite; check the directional properties.')
    return np.linalg.inv(c)


SIGNS=np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]])
OFFSETS=(SIGNS+1)//2

def strain_matrix(spacing, point):
    grad=np.empty((8,3))
    for a in range(3):
        others=[b for b in range(3) if b!=a]
        grad[:,a]=SIGNS[:,a]*np.prod(1+SIGNS[:,others]*np.array(point)[others],axis=1)/(4*spacing[a])
    b=np.zeros((6,24))
    for n,(x,y,z) in enumerate(grad):
        i=3*n;b[0,i]=x;b[1,i+1]=y;b[2,i+2]=z
        b[3,i]=y;b[3,i+1]=x;b[4,i+1]=z;b[4,i+2]=y;b[5,i]=z;b[5,i+2]=x
    return b


def element_matrices(spacing,c):
    points=list(product((-1/np.sqrt(3),1/np.sqrt(3)),repeat=3))
    bs=np.array([strain_matrix(spacing,p) for p in points])
    k=sum(b.T@c@b for b in bs)*np.prod(spacing)/8
    return k,bs


def inside_mesh(points,triangles,cancel):
    """Parity rays with non-axis-aligned direction and bounded temporary arrays."""
    direction=np.array([1.,.000031,.000017]);a=triangles[:,0]
    e1=triangles[:,1]-a;e2=triangles[:,2]-a
    h=np.cross(direction,e2);det=np.einsum('ij,ij->i',e1,h);valid=np.abs(det)>1e-12
    a,e1,e2,h,det=a[valid],e1[valid],e2[valid],h[valid],det[valid]
    result=[];chunk=max(1,min(64,500_000//max(1,len(a))))
    for start in range(0,len(points),chunk):
        check_cancel(cancel)
        s=points[start:start+chunk,None,:]-a;u=np.einsum('pij,ij->pi',s,h)/det
        q=np.cross(s,e1);v=np.einsum('pij,j->pi',q,direction)/det;t=np.einsum('pij,ij->pi',q,e2)/det
        result.extend(((u>=0)&(v>=0)&(u+v<=1)&(t>1e-9)).sum(axis=1)%2==1)
    return np.array(result)


def check_cancel(cancel):
    if cancel.is_set():raise Cancelled('FEA search cancelled. No optimized export was produced.')


def shell_fractions(mask,spacing,thickness):
    # Fractional boundary-cell shells instead of rounding a thin wall to a full voxel.
    remaining=np.ones(mask.shape)
    for axis in range(3):
        for reverse in (False,True):
            view=np.flip(mask,axis) if reverse else mask
            distance=np.zeros(mask.shape);run=np.zeros(np.delete(mask.shape,axis))
            for i in range(mask.shape[axis]):
                key=[slice(None)]*3;key[axis]=i;key=tuple(key)
                active=view[key];distance[key]=run*spacing[axis];run=np.where(active,run+1,0)
            if reverse:distance=np.flip(distance,axis)
            fraction=np.clip((thickness[2*axis+int(reverse)]-distance)/spacing[axis],0,1)
            remaining*=1-fraction
    return (1-remaining)[mask]


class VoxelFEA:
    def __init__(self, triangles, settings, region_axis='z', support_face='min', cells=None, cancel=None, grid_shape=None):
        settings.validate();self.settings=settings;self.cancel=cancel or Event();check_cancel(self.cancel)
        if len(triangles)>20_000:raise ValueError('FEA prototype supports at most 20,000 triangles. Simplify the STL before simulation.')
        self.lo=triangles.min(axis=(0,1));self.hi=triangles.max(axis=(0,1));size=self.hi-self.lo
        count=cells or settings.mesh_cells
        shape=np.maximum(2,np.ceil(size/size.max()*count).astype(int))
        if grid_shape is not None:shape=np.maximum(shape,np.array(grid_shape,dtype=int))
        # Align region boundaries with element faces, avoiding mixed-density cells.
        region_index='xyz'.index(region_axis)
        shape[region_index]=max(3,3*int(np.ceil(shape[region_index]/3)))
        self.spacing=size/shape
        indices=np.array(list(np.ndindex(tuple(shape))))
        centers=self.lo+(indices+.5)*self.spacing
        mask=inside_mesh(centers,triangles,self.cancel).reshape(tuple(shape))
        if not mask.any():raise ValueError('FEA mesh contains no material. Refine the mesh or check the STL units.')
        _,components=ndimage.label(mask)
        if components!=1:raise ValueError('FEA voxel mesh is disconnected. Refine the mesh or use a single connected part.')
        self.indices=indices[mask.ravel()];self.centers=centers[mask.ravel()]
        centered=triangles-self.lo
        mesh_volume=abs(float(np.einsum('ij,ij->i',centered[:,0],np.cross(centered[:,1],centered[:,2])).sum()/6))
        if mesh_volume<=0:raise ValueError('STL signed volume is zero; repair face winding before FEA.')
        lattice=self.indices[:,None,:]+OFFSETS
        unique,inverse=np.unique(lattice.reshape(-1,3),axis=0,return_inverse=True)
        self.nodes=self.lo+unique*self.spacing;self.elements=inverse.reshape(-1,8)
        self.dofs=(self.elements[:,:,None]*3+np.arange(3)).reshape(-1,24)
        self.region=np.minimum(2,((self.centers[:,'xyz'.index(region_axis)]-self.lo['xyz'.index(region_axis)])/size['xyz'.index(region_axis)]*3).astype(int))
        self.shell=shell_fractions(mask,self.spacing,settings.shell_mm)
        self.c=elasticity(settings);self.k,self.bs=element_matrices(self.spacing,self.c)
        self.rows=np.repeat(self.dofs,24,axis=1).ravel();self.cols=np.tile(self.dofs,(1,24)).ravel()
        self.ndof=len(unique)*3
        support_axis='xyz'.index(region_axis)
        support_coord=self.lo[support_axis] if support_face=='min' else self.hi[support_axis]
        loaded_coord=self.hi[support_axis] if support_face=='min' else self.lo[support_axis]
        fixed_nodes=np.flatnonzero(np.isclose(self.nodes[:,support_axis],support_coord,atol=1e-8,rtol=0))
        if len(fixed_nodes)<3:raise ValueError('Selected full-face support has no resolved surface. Refine the mesh or change support axis.')
        self.fixed=(fixed_nodes[:,None]*3+np.arange(3)).ravel();self.free=np.setdiff1d(np.arange(self.ndof),self.fixed)
        # Equal traction, weighted by each exposed voxel face's area.
        boundary=np.isclose(self.nodes[self.elements][:,:,support_axis],loaded_coord,atol=1e-8,rtol=0)
        face_elements=boundary.sum(axis=1)==4
        face_area=np.prod(np.delete(self.spacing,support_axis))
        weights=np.zeros(len(unique));np.add.at(weights,self.elements[face_elements][boundary[face_elements]],face_area/4)
        if weights.sum()==0:raise ValueError('No opposite-face surface was resolved for the FEA load. Change support axis or refine the mesh.')
        self.force=np.zeros(self.ndof);self.force['xyz'.index(settings.force_axis)::3]=settings.force_n*weights/weights.sum()
        self.metadata={'nodes':len(unique),'elements':len(self.elements),'grid_shape':shape.tolist(),'spacing_mm':self.spacing.tolist(),
                       'voxel_volume_mm3':float(mask.sum()*np.prod(self.spacing)),'support_nodes':len(fixed_nodes),
                       'loaded_area_mm2':float(weights.sum()),'shell_volume_fraction':float(self.shell.mean()),
                       'stl_volume_mm3':mesh_volume,
                       'relative_stl_volume_error':abs(mask.sum()*np.prod(self.spacing)-mesh_volume)/mesh_volume}

    def solve(self,densities,fields=False):
        check_cancel(self.cancel)
        rho=np.array(densities)[self.region]/100
        if np.any(rho<=0):raise ValueError('FEA requires positive core infill; empty-core shell elements are not supported.')
        factor=self.shell+(1-self.shell)*rho**self.settings.stiffness_power
        data=(factor[:,None,None]*self.k).ravel()
        matrix=coo_matrix((data,(self.rows,self.cols)),shape=(self.ndof,self.ndof)).tocsr()
        u=np.zeros(self.ndof)
        with warnings.catch_warnings():
            warnings.simplefilter('error',MatrixRankWarning)
            try:u[self.free]=spsolve(matrix[self.free][:,self.free],self.force[self.free])
            except MatrixRankWarning as exc:raise ValueError('FEA stiffness is singular; check disconnected geometry and supports.') from exc
        check_cancel(self.cancel)
        if not np.isfinite(u).all():raise ValueError('FEA produced nonfinite displacement.')
        reaction=matrix@u-self.force
        residual=np.linalg.norm(reaction[self.free])/max(np.linalg.norm(self.force),1e-12)
        equilibrium=np.linalg.norm(reaction.reshape(-1,3)[self.fixed[::3]//3].sum(axis=0)+self.force.reshape(-1,3).sum(axis=0))/abs(self.settings.force_n)
        fixed_nodes=self.fixed[::3]//3
        moment=(np.cross(self.nodes[fixed_nodes],reaction.reshape(-1,3)[fixed_nodes]).sum(axis=0)
                +np.cross(self.nodes,self.force.reshape(-1,3)).sum(axis=0))
        moment_error=np.linalg.norm(moment)/(abs(self.settings.force_n)*np.linalg.norm(self.hi-self.lo))
        if max(residual,equilibrium,moment_error)>1e-6:raise ValueError('FEA solve failed residual, force or moment balance verification.')
        strains=np.einsum('gij,ej->egi',self.bs,u[self.dofs])
        stresses=np.einsum('ij,egj->egi',self.c,strains)*factor[:,None,None]
        s=self.settings;allow=np.array([s.strength_xy_mpa,s.strength_xy_mpa,s.strength_z_mpa,s.shear_mpa,s.shear_mpa,s.shear_mpa])
        strength_factor=self.shell+(1-self.shell)*rho**s.strength_power
        utilization=np.max(np.abs(stresses)/(allow[None,None,:]*strength_factor[:,None,None])*s.safety_factor,axis=(1,2))
        von=np.sqrt(.5*((stresses[:,:,0]-stresses[:,:,1])**2+(stresses[:,:,1]-stresses[:,:,2])**2+(stresses[:,:,2]-stresses[:,:,0])**2)+3*np.sum(stresses[:,:,3:]**2,axis=2))
        displacement=np.linalg.norm(u.reshape(-1,3),axis=1)
        result={'max_displacement_mm':float(displacement.max()),'max_von_mises_mpa':float(von.max()),
                'load_compliance_mm_per_n':float(np.dot(self.force,u)/self.settings.force_n**2),
                'max_strain':float(np.max(np.abs(strains))),'screening_utilization':float(utilization.max()),
                'material_volume_proxy_mm3':float(np.sum(self.shell+(1-self.shell)*rho)*np.prod(self.spacing)),
                'relative_residual':float(residual),'relative_force_balance_error':float(equilibrium),
                'relative_moment_balance_error':float(moment_error)}
        result['linear_regime']=result['max_strain']<=.01 and result['max_displacement_mm']<=.02*float(np.min(self.hi-self.lo))
        result['limits_pass']=bool(result['linear_regime'] and result['max_displacement_mm']<=s.max_displacement_mm and result['screening_utilization']<=1)
        if fields:result['fields']={'nodes':self.nodes,'elements':self.elements,'displacements':u.reshape(-1,3),'centers':self.centers,
                                   'von_mises_mpa':von.max(axis=1),'utilization':utilization,'densities':rho,'shell_fraction':self.shell}
        return result


def density_options(settings):
    options=sorted(set([d for d in settings.densities if settings.minimum<=d<=settings.maximum]+[settings.baseline]))
    combos=[v for v in product(options,repeat=3) if all(v[i]>=settings.protected_minima[i] for i in range(3))]
    if len(combos)>1000:raise ValueError('FEA direct search is limited to 1,000 combinations. Reduce the density grid.')
    if not combos:raise ValueError('No density combination meets the regional minima.')
    if any(min(v)<=0 for v in combos):raise ValueError('FEA requires positive regional infill. Set positive minima; zero-core shell modeling is unsupported.')
    return combos


def direct_search(triangles,settings,fea,cancel,progress):
    if fea.assessment=='relative_stiffness':fea=replace(fea,force_n=1.)
    options=density_options(settings)
    progress(f'FEA: meshing {fea.mesh_cells} and {fea.mesh_cells+2} cells along the longest axis…')
    coarse=VoxelFEA(triangles,fea,settings.contact_axis,settings.contact_face,cancel=cancel)
    fine=VoxelFEA(triangles,fea,settings.contact_axis,settings.contact_face,cells=fea.mesh_cells+2,cancel=cancel,
                  grid_shape=np.array(coarse.metadata['grid_shape'])+2)
    baseline=(settings.baseline,)*3
    base_coarse=coarse.solve(baseline);base_fine=fine.solve(baseline)
    base_mesh_change=abs(base_coarse['load_compliance_mm_per_n']-base_fine['load_compliance_mm_per_n'])/base_fine['load_compliance_mm_per_n']
    rows=[]
    for i,densities in enumerate(options):
        check_cancel(cancel);progress(f'FEA: simulating {i+1}/{len(options)} infill combinations…')
        a=coarse.solve(densities);b=fine.solve(densities)
        keys=('max_displacement_mm','load_compliance_mm_per_n') if fea.assessment=='relative_stiffness' else ('max_displacement_mm','screening_utilization')
        changes={key:abs(b[key]-a[key])/max(abs(b[key]),1e-12) for key in keys}
        volume_change=abs(fine.metadata['voxel_volume_mm3']-coarse.metadata['voxel_volume_mm3'])/fine.metadata['voxel_volume_mm3']
        stable=max(*changes.values(),volume_change,coarse.metadata['relative_stl_volume_error'],fine.metadata['relative_stl_volume_error'])<=fea.mesh_tolerance
        a['relative_stiffness_ratio']=base_coarse['load_compliance_mm_per_n']/a['load_compliance_mm_per_n']
        b['relative_stiffness_ratio']=base_fine['load_compliance_mm_per_n']/b['load_compliance_mm_per_n']
        if fea.assessment=='relative_stiffness':
            stable=bool(stable and base_mesh_change<=fea.mesh_tolerance)
            passed=min(a['relative_stiffness_ratio'],b['relative_stiffness_ratio'])>=fea.minimum_stiffness_ratio-1e-10
        else:passed=a['limits_pass'] and b['limits_pass']
        rows.append({'densities':list(densities),'coarse':a,'refined':b,'relative_mesh_changes':changes,
                     'relative_volume_change':volume_change,'mesh_check_pass':stable,
                     'feasible':bool(stable and passed)})
    rows.sort(key=lambda row:(row['refined']['material_volume_proxy_mm3'],row['densities']))
    result={'status':'SIMULATED — UNVALIDATED','method':'Exhaustive discrete search; 8-node trilinear voxel elements with 2×2×2 Gauss integration',
            'inputs':asdict(fea),'support_axis':settings.contact_axis,'support_face':settings.contact_face,
            'coarse_mesh':coarse.metadata,'refined_mesh':fine.metadata,'combinations_tested':len(rows),
            'baseline_response':{'coarse':base_coarse,'refined':base_fine,'relative_compliance_mesh_change':base_mesh_change},
            'feasible_count':sum(r['feasible'] for r in rows),'limitations':LIMITATIONS,'candidates':rows}
    if fea.assessment=='relative_stiffness':
        result['limitations']=LIMITATIONS+['Relative comparison uses a normalized 1 N reference load, not actual thrust. Only stiffness retention relative to the baseline is screened; absolute stress/displacement limits and strength inputs do not control selection.']
    return result,fine


def write_search(folder,result):
    (folder/'fea_search.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    with (folder/'fea_search.csv').open('w',newline='') as stream:
        columns=['region_1_percent','region_2_percent','region_3_percent','feasible','mesh_check_pass','max_displacement_mm','max_von_mises_mpa','screening_utilization','material_volume_proxy_mm3','relative_stiffness_ratio','load_compliance_mm_per_n']
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for row in result['candidates']:
            writer.writerow({**dict(zip(columns[:3],row['densities'])),'feasible':row['feasible'],'mesh_check_pass':row['mesh_check_pass'],
                             **{key:row['refined'][key] for key in columns[5:]}})


def save_fields(folder,mesh,densities):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    result=mesh.solve(densities,fields=True);fields=result.pop('fields')
    np.savez_compressed(folder/'fea_selected_fields.npz',**fields)
    fig=Figure(figsize=(11,5),layout='constrained');FigureCanvasAgg(fig)
    for index,(values,label) in enumerate([(fields['von_mises_mpa'],'Homogenized stress (MPa)'),(np.linalg.norm(fields['displacements'][mesh.elements],axis=2).mean(axis=1),'Displacement (mm)')]):
        ax=fig.add_subplot(1,2,index+1,projection='3d');points=fields['centers']
        plot=ax.scatter(*points.T,c=values,cmap='viridis',s=15);fig.colorbar(plot,ax=ax,shrink=.6,label=label)
        ax.set(xlabel='X (mm)',ylabel='Y (mm)',zlabel='Z (mm)',title=label);ax.set_box_aspect(mesh.hi-mesh.lo)
    caption='1 N reference comparison' if mesh.settings.assessment=='relative_stiffness' else 'linear FEA predictions'
    fig.suptitle('Selected candidate • '+caption+' • physically unvalidated')
    fig.savefig(folder/'fea_selected.png',dpi=150)
    return result


def shell_from_profiles(resolved):
    """Approximate shell widths from actual resolved process/nozzle settings."""
    p=resolved['process'];m=resolved['machine']
    def number(value):
        if isinstance(value,list):value=value[0]
        result=float(value)
        if not np.isfinite(result) or result<0:raise ValueError('Unsupported negative/nonfinite slicer shell setting.')
        return result
    nozzle=number(m['nozzle_diameter']);layer=number(p['layer_height'])
    width=number(p.get('outer_wall_line_width',0)) or number(p.get('line_width',0)) or nozzle
    inner=number(p.get('inner_wall_line_width',0)) or number(p.get('line_width',0)) or nozzle
    loops=number(p.get('wall_loops',2));wall=width+max(0,loops-1)*inner if loops else 0
    bottom=max(number(p.get('bottom_shell_layers',0))*layer,number(p.get('bottom_shell_thickness',0)))
    top=max(number(p.get('top_shell_layers',0))*layer,number(p.get('top_shell_thickness',0)))
    return (wall,wall,wall,wall,bottom,top)
