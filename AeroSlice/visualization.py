"""Charts always label heuristic/estimated/synthetic provenance."""
from pathlib import Path
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import numpy as np


def heatmap_figure(regions, candidates) -> Figure:
    fig=Figure(figsize=(9,4),layout='constrained');ax=fig.add_subplot()
    matrix=np.array([c.densities for c in candidates])
    im=ax.imshow(matrix,aspect='auto',cmap='YlOrRd',vmin=0,vmax=100)
    ax.set_xticks(range(3),[f'Region {r.index+1}\nDemand {r.demand:.2f}' for r in regions])
    ax.set_yticks(range(len(candidates)),[c.id for c in candidates])
    for i,row in enumerate(matrix):
        for j,value in enumerate(row):ax.text(j,i,f'{value:g}%',ha='center',va='center')
    ax.set_title('Candidate density assignments • heuristic, not stress simulation')
    fig.colorbar(im,ax=ax,label='Sparse infill (%)');return fig


def save_heatmap(regions,candidates,path: Path) -> None:
    fig=heatmap_figure(regions,candidates);FigureCanvasAgg(fig).print_png(str(path))


def motion_figure(trial) -> Figure:
    fig=Figure(figsize=(8,5),layout='constrained');a,b=fig.subplots(2,1,sharex=True)
    a.plot(trial.time_s,trial.raw_distance_mm,color='#94a3b8',label='Valid raw readings')
    a.plot(trial.time_s,trial.distance_mm,color='#0284c7',label=trial.processing)
    a.set_ylabel('Top sensor distance (mm)');a.legend(fontsize=7)
    b.plot(trial.time_s,trial.velocity_mm_s,color='#0d9488');b.set_ylabel('Upward velocity (mm/s)');b.set_xlabel('Actual elapsed time (s)')
    fig.suptitle(f'{trial.variant} / trial {trial.trial_id}\n{trial.source}',fontsize=10)
    for ax in (a,b):ax.grid(alpha=.2)
    return fig


def comparison_figure(summary) -> Figure:
    fig=Figure(figsize=(8,4),layout='constrained');ax=fig.add_subplot()
    rows=summary['variants'];ax.bar(range(len(rows)),[r['mean_climb_time_s'] for r in rows],color='#0284c7')
    ax.set_xticks(range(len(rows)),[f"{r['variant']}\nn={r['n']}" for r in rows]);ax.set_ylabel('Mean fixed-interval climb time (s)')
    ax.set_title('Repeated climb trials • '+('SYNTHETIC DEMONSTRATION DATA' if any('SYNTHETIC' in r['source'] for r in rows) else 'Measured inputs'))
    return fig
