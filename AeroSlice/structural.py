"""A declared load-path heuristic, NOT FEA or a stiffness predictor.

Assumes a closed payload with full-face platform contact, distributed self-weight
and axis-aligned inertial loading. Slab demand is proportional to the estimated
occupied volume whose force crosses its center plane. Occupancy is estimated by
ray parity on a deterministic grid (not a material density or measured mass).
Transverse loading uses equal demand; protected regions remain configurable.
No mounting holes, motor thrust, final hardware dimensions, or material strengths
are assumed. Acceleration scales the load proxy, not validated strength limits.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import itertools
import numpy as np

DISCLAIMER = 'Structural-demand analysis is a heuristic and has not been validated as a full stress simulation.'

@dataclass
class Settings:
    baseline: float = 20.
    minimum: float = 10.
    maximum: float = 30.
    candidate_count: int = 8
    densities: tuple[float, ...] = (10., 15., 20., 25., 30.)
    contact_axis: str = 'z'
    contact_face: str = 'min'
    load_axis: str = 'z'
    acceleration: float = 0.
    rotation: tuple[float, float, float] = (0., 0., 0.)
    protected_minima: tuple[float, float, float] = (15., 10., 10.)
    demand_override: tuple[float, float, float] | None = None
    coverage: float = .75
    fixed_hardware_mass_g: float | None = None

    def validate(self) -> None:
        values = [self.baseline,self.minimum,self.maximum,self.acceleration,self.coverage,*self.rotation,*self.protected_minima,*self.densities]
        if not np.isfinite(values).all(): raise ValueError('Settings must contain finite numbers.')
        if not 0 <= self.minimum <= self.baseline <= self.maximum <= 100:
            raise ValueError('Use 0 ≤ minimum ≤ baseline ≤ maximum ≤ 100%.')
        if not 3 <= self.candidate_count <= 20: raise ValueError('Choose 3–20 candidates.')
        if self.contact_axis not in 'xyz' or self.load_axis not in 'xyz' or self.contact_face not in ('min','max'):
            raise ValueError('Invalid contact face or loading axis.')
        if len(self.protected_minima) != 3 or any(not self.minimum <= x <= self.maximum for x in self.protected_minima):
            raise ValueError('Each protected minimum must lie within the density range.')
        if not 0 <= self.coverage <= 1: raise ValueError('Coverage must be between 0 and 1.')
        if not self.densities or any(not 0 <= d <= 100 for d in self.densities): raise ValueError('Density choices must be 0–100%.')
        if self.demand_override is not None and (len(self.demand_override)!=3 or any(not 0 <= d <= 1 for d in self.demand_override)):
            raise ValueError('Three demand scores must be between 0 and 1.')
        if self.fixed_hardware_mass_g is not None and (not np.isfinite(self.fixed_hardware_mass_g) or self.fixed_hardware_mass_g <= 0):
            raise ValueError('Fixed moving hardware mass must be positive or blank.')

@dataclass
class Region:
    index: int
    low: list[float]
    high: list[float]
    demand: float
    minimum: float
    maximum: float
    volume_fraction: float


def occupancy_fractions(triangles: np.ndarray, axis: int) -> np.ndarray:
    """Ray-parity occupancy grid, deterministic jitter avoids mesh-edge coincidences."""
    lo, hi = triangles.min(axis=(0,1)), triangles.max(axis=(0,1))
    grid = [lo[i]+(np.arange(15)+.4137)*(hi[i]-lo[i])/15 for i in range(3)]
    samples = np.array(list(itertools.product(*grid)))
    # Cast rays along a slightly tilted direction to avoid repeated coplanar hits.
    direction = np.array([1.,.000031,.000017])
    a = triangles[:,0]; e1=triangles[:,1]-a; e2=triangles[:,2]-a
    h=np.cross(np.broadcast_to(direction,e2.shape),e2); det=(e1*h).sum(axis=1)
    ok=np.abs(det)>1e-12; a,e1,e2,h,det=a[ok],e1[ok],e2[ok],h[ok],det[ok]
    counts=np.zeros(3)
    # Limit work for detailed meshes. Users can select explicit equal / custom demand.
    if len(triangles)>20_000: return np.ones(3)/3
    for start in range(0,len(samples),64):
        p=samples[start:start+64]; s=p[:,None,:]-a
        u=(s*h).sum(axis=2)/det
        q=np.cross(s,e1); v=(q*direction).sum(axis=2)/det
        t=(q*e2).sum(axis=2)/det
        hit=(u>=0)&(v>=0)&(u+v<=1)&(t>1e-8)
        inside=hit.sum(axis=1)%2==1
        bins=np.minimum(2,((p[:,axis]-lo[axis])/(hi[axis]-lo[axis])*3).astype(int))
        for i in range(3): counts[i]+=np.sum(inside & (bins==i))
    if counts.sum()==0: return np.ones(3)/3
    return counts/counts.sum()


def make_regions(triangles: np.ndarray, settings: Settings) -> tuple[list[Region], list[str]]:
    settings.validate()
    axis='xyz'.index(settings.contact_axis)
    lo,hi=triangles.min(axis=(0,1)),triangles.max(axis=(0,1))
    fractions=occupancy_fractions(triangles,axis)
    notes=[DISCLAIMER, 'Contact assumption: full selected bounding-box face; orientation follows the configured print rotation.',
           'Occupancy-grid volume fractions are heuristic, not measured printed mass.',
           'Expected load magnitude proxy: |9.80665 + acceleration|; no strength threshold is inferred.']
    if len(triangles)>20_000: notes.append('Detailed mesh: equal volume fractions used; configure demand scores for a better model.')
    if settings.demand_override is not None:
        demand=np.array(settings.demand_override);notes.append('Demand scores explicitly provided by the user.')
    elif settings.load_axis != settings.contact_axis:
        demand=np.ones(3);notes.append('Transverse loading: equal demand because no defensible beam/support model was supplied.')
    else:
        demand=np.array([fractions[i]/2+sum(fractions[i+1:]) for i in range(3)])
        if settings.contact_face=='max': demand=np.array([fractions[i]/2+sum(fractions[:i]) for i in range(3)])
        if 9.80665+settings.acceleration<=1e-6:
            demand=np.ones(3);notes.append('Nonpositive effective compressive load: unfastened payload contact may be lost. Equal demand avoids unsupported full-face load-path predictions.')
        else: demand/=demand.max()
        notes.append('Axial distributed-load proxy: occupied volume beyond each slab midplane towards the unsupported face.')
    regions=[]
    for i in range(3):
        low,high=lo.copy(),hi.copy()
        low[axis]=lo[axis]+i*(hi[axis]-lo[axis])/3
        high[axis]=lo[axis]+(i+1)*(hi[axis]-lo[axis])/3
        regions.append(Region(i,low.tolist(),high.tolist(),float(demand[i]),settings.protected_minima[i],settings.maximum,float(fractions[i])))
    notes.append('Slabs share boundaries but have no positive-volume overlaps; arbitrary overlapping modifiers are unsupported.')
    return regions,notes
