"""Measured or explicitly synthetic motion analysis using actual timestamps."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import csv
import math
from collections import defaultdict
import numpy as np
from scipy.stats import t as student_t

@dataclass
class Reading:
    timestamp_ms: float
    distance_mm: float | None
    trial_id: str
    variant: str
    source: str = 'MEASURED (user-supplied; provenance not independently verified)'
    error: str = ''
    raw_distance: str = ''

@dataclass
class Trial:
    trial_id: str
    variant: str
    time_s: np.ndarray
    raw_distance_mm: np.ndarray
    distance_mm: np.ndarray
    displacement_mm: np.ndarray
    velocity_mm_s: np.ndarray
    warnings: list[str]
    source: str
    processing: str


def read_csv(path: str | Path) -> list[Reading]:
    readings=[]
    with Path(path).open(newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        required={'timestamp_ms','distance_mm','trial_id','variant'}
        if not required<=set(reader.fieldnames or []): raise ValueError('CSV needs timestamp_ms,distance_mm,trial_id,variant columns.')
        for index,row in enumerate(reader,2):
            try:
                ts=float(row['timestamp_ms'])
                if not math.isfinite(ts) or ts<0: raise ValueError('timestamp must be finite and nonnegative')
                distance=float(row['distance_mm']) if row['distance_mm'].strip() else None
                error=row.get('error','')
                if distance is not None and (not math.isfinite(distance) or distance<=0):
                    error='Invalid distance';distance=None
                if not row['trial_id'].strip() or not row['variant'].strip(): raise ValueError('trial_id and variant cannot be empty')
                readings.append(Reading(ts,distance,row['trial_id'],row['variant'],row.get('source') or Reading.__dataclass_fields__['source'].default,error, row.get('raw_distance',row['distance_mm'])))
            except (ValueError,KeyError,TypeError) as exc: raise ValueError(f'CSV row {index}: {exc}') from exc
    if not readings: raise ValueError('Sensor CSV has no readings.')
    return readings


def save_csv(readings: list[Reading], path: Path) -> None:
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(Reading.__dataclass_fields__))
        writer.writeheader();writer.writerows(asdict(r) for r in readings)


def process_trials(readings: list[Reading], minimum: float | None = None, maximum: float | None = None,
                   smooth: bool = False) -> list[Trial]:
    if minimum is not None and (not math.isfinite(minimum) or minimum<=0): raise ValueError('Sensor minimum must be positive.')
    if maximum is not None and (not math.isfinite(maximum) or maximum<=0): raise ValueError('Sensor maximum must be positive.')
    if minimum is not None and maximum is not None and minimum>=maximum: raise ValueError('Sensor minimum must be below maximum.')
    grouped=defaultdict(list)
    for r in readings: grouped[(r.variant,r.trial_id)].append(r)
    results=[]
    for (variant,tid),rows in sorted(grouped.items()):
        warnings=[];valid=[];seen=set()
        for r in sorted(rows,key=lambda r:r.timestamp_ms):
            if r.timestamp_ms in seen: warnings.append('Duplicate timestamp omitted from analysis; raw data retained.');continue
            seen.add(r.timestamp_ms)
            d=r.distance_mm
            if r.error or d is None or not math.isfinite(d) or d<=0 or (minimum is not None and d<minimum) or (maximum is not None and d>maximum):
                warnings.append('Missing/error/out-of-range reading omitted; raw data retained.');continue
            valid.append(r)
        if len(valid)<2: continue
        t=np.array([r.timestamp_ms for r in valid])/1000
        t-=t[0];raw=np.array([r.distance_mm for r in valid]);d=raw.copy()
        if np.ptp(np.diff(t))>.1*np.median(np.diff(t)): warnings.append('Irregular sampling: derivatives use actual timestamps.')
        if minimum is None or maximum is None: warnings.append('Sensor reliable range: NOT YET VERIFIED. Configure it for the actual sensor.')
        if len(d)>=5:
            med=np.array([np.median(d[max(0,i-2):min(len(d),i+3)]) for i in range(len(d))])
            residual=np.abs(d-med);mad=np.median(np.abs(residual-np.median(residual)))
            suspect=residual>max(3*1.4826*mad,10)
            if suspect.any(): warnings.append(f'{int(suspect.sum())} potential outliers flagged by a five-sample median residual; inspect the graph.')
            if smooth: d=med
        elif smooth: warnings.append('Insufficient samples for five-sample median smoothing.')
        source='SYNTHETIC DEMONSTRATION DATA' if any('SYNTHETIC' in r.source.upper() for r in rows) else 'MEASURED (user-supplied; provenance not independently verified)'
        if source.startswith('SYNTHETIC') and not all('SYNTHETIC' in r.source.upper() for r in rows):
            warnings.append('Mixed measured/synthetic provenance in this trial; excluded from measured comparisons.')
        results.append(Trial(tid,variant,t,raw,d,d[0]-d,-np.gradient(d,t,edge_order=1),list(dict.fromkeys(warnings)),source,
                             'Five-sample median (processed); raw retained' if smooth else 'Unsmoothed valid readings; potential outliers flagged only'))
    if not results: raise ValueError('No trial has at least two valid, unique timestamped distances.')
    return results


def climb_time(trial: Trial, start_mm: float, end_mm: float, max_gap_s: float = .5) -> float | None:
    if not all(math.isfinite(v) for v in (start_mm,end_mm,max_gap_s)) or start_mm<=end_mm or end_mm<=0 or max_gap_s<=0:
        raise ValueError('Set start distance > end distance > 0 and a positive interpolation gap limit.')
    t,d=trial.time_s,trial.distance_mm
    beginning=None
    for i in range(len(t)-1):
        if t[i+1]-t[i]>max_gap_s:
            # Never bridge sensor dropouts when comparing climb times.
            beginning=None;continue
        def crossing(threshold):
            if d[i]>=threshold>=d[i+1] and d[i]>d[i+1]:
                return float(t[i]+(d[i]-threshold)/(d[i]-d[i+1])*(t[i+1]-t[i]))
            return None
        if beginning is None: beginning=crossing(start_mm)
        if beginning is not None:
            end=crossing(end_mm)
            if end is not None and end>beginning: return end-beginning
    return None


def summarize(trials: list[Trial], start_mm: float, end_mm: float, max_gap_s: float = .5,
              baseline_variant: str = 'baseline', paired: bool = False) -> dict:
    grouped=defaultdict(list);warnings=[]
    for trial in trials:
        elapsed=climb_time(trial,start_mm,end_mm,max_gap_s)
        if elapsed is None: warnings.append(f'{trial.variant}/{trial.trial_id}: no complete valid climb interval.');continue
        grouped[(trial.source,trial.variant)].append((trial.trial_id,elapsed))
    results=[]
    for (source,variant),rows in sorted(grouped.items()):
        values=np.array([v for _,v in rows]);n=len(values)
        mean=float(values.mean());std=float(values.std(ddof=1)) if n>1 else None
        ci=None
        if n>=3:
            critical=float(student_t.ppf(.975,n-1))
            delta=critical*std/math.sqrt(n);ci=[mean-delta,mean+delta]
        if n<3: warnings.append(f'{variant}: fewer than three trials; evidence is insufficient.')
        elif n<5: warnings.append(f'{variant}: five or more trials are recommended.')
        results.append({'variant':variant,'source':source,'n':n,'mean_climb_time_s':mean,'std_climb_time_s':std,
                        'mean_speed_mm_s':float(np.mean((start_mm-end_mm)/values)),
                        'mean_time_95_ci_s':ci,'ci_method':'Student t, assumes independent approximately normal trial times' if ci else 'Unavailable'})
    comparisons=[];rng=np.random.default_rng(0)
    for row in results:
        if row['variant']==baseline_variant: continue
        base=next((r for r in results if r['variant']==baseline_variant and r['source']==row['source']),None)
        if not base: continue
        arows=grouped[(base['source'],baseline_variant)];brows=grouped[(row['source'],row['variant'])]
        a=np.array([t for _,t in arows]);b=np.array([t for _,t in brows])
        ci=None;method='Independent resampling bootstrap (exploratory)';enough=len(a)>=5 and len(b)>=5
        if paired:
            ad=dict(arows);bd=dict(brows);ids=sorted(ad.keys()&bd.keys())
            a=np.array([ad[k] for k in ids]);b=np.array([bd[k] for k in ids]);enough=len(ids)>=5
            method='Paired bootstrap by matched trial_id (exploratory)'
        if len(a)==0 or len(b)==0:
            warnings.append('No matching paired trial IDs.');continue
        improvement=float((a.mean()-b.mean())/a.mean()*100)
        if enough:
            if paired:
                indexes=rng.integers(0,len(a),size=(5000,len(a)));am=a[indexes].mean(axis=1);bm=b[indexes].mean(axis=1)
            else:
                am=rng.choice(a,(5000,len(a)),replace=True).mean(axis=1);bm=rng.choice(b,(5000,len(b)),replace=True).mean(axis=1)
            ci=np.percentile((am-bm)/am*100,[2.5,97.5]).tolist()
        conclusion='Inconclusive: insufficient trials or interval includes no improvement.'
        if ci and ci[0]>0: conclusion='Exploratory interval suggests faster climbing; confirm equivalent loading, power, support and independent repeats.'
        if row['source'].startswith('SYNTHETIC'): conclusion='Synthetic demonstration only; no experimental conclusion.'
        comparisons.append({'variant':row['variant'],'source':row['source'],'improvement_percent':improvement,'95_ci_percent':ci,'method':method,'conclusion':conclusion})
    return {'interval_mm':[start_mm,end_mm],'max_interpolation_gap_s':max_gap_s,'variants':results,'comparisons':comparisons,'warnings':warnings}


def stiffness(force_n: float, deflection_mm: float, mass_g: float, conditions: str) -> dict:
    if not all(math.isfinite(v) and v>0 for v in (force_n,deflection_mm,mass_g)) or not conditions.strip():
        raise ValueError('Enter positive measured force, deflection, mass, and loading/support conditions.')
    k=force_n/deflection_mm
    return {'stiffness_n_per_mm':k,'stiffness_to_mass_n_per_mm_per_g':k/mass_g,
            'conditions':conditions,'source':'Measured inputs supplied by user; compare only under equivalent conditions.'}


def synthetic_readings() -> list[Reading]:
    rng=np.random.default_rng(42);rows=[]
    for variant,duration in [('baseline',4.5),('adaptive',4.1)]:
        for tid in range(1,6):
            speed=500/(duration+rng.normal(0,.12))
            for ms in range(0,6001,60):
                rows.append(Reading(ms,max(100,800-speed*ms/1000+rng.normal(0,1.2)),str(tid),variant,'SYNTHETIC DEMONSTRATION DATA'))
    return rows
