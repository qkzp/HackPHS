"""Model-independent motion comparison inside the existing Measurements page."""
from __future__ import annotations
from queue import Queue, Empty
from threading import Thread, Event
from time import monotonic
import gc
import tkinter as tk
from tkinter import ttk
import numpy as np
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from .motion_simulation import MotionSettings, compare_motion, sliced_cases
from .ui_components import ScrollPage, responsive_form


class MotionPanel(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,style='Page.TFrame')
        self.app=app;self.result=None;self.generation=0;self.cancel=Event();self.queue=Queue()
        self.play_timer=None;self.input_timer=None;self.pending=False;self.disposed=False
        self.worker_thread=None
        page=ScrollPage(self);page.pack(fill='both',expand=True);body=page.body
        ttk.Label(body,text='Original vs. optimized motion',style='CardTitle.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(body,text='PHYSICS SIMULATION · Unvalidated apparatus assumptions. Payload masses come from completed OrcaSlicer slices. Both payloads are enclosed in the same carriage.',
                  style='Hint.TLabel',wraplength=1100).pack(fill='x',pady=(0,12))
        self.state=tk.StringVar(value='Waiting: upload a model and complete optimization first.')
        ttk.Label(body,textvariable=self.state,wraplength=1100).pack(fill='x',pady=(0,12))
        controls=ttk.Frame(body,style='Card.TFrame',padding=12);controls.pack(fill='x',pady=(0,12))
        ttk.Label(controls,text='Shared apparatus assumptions',style='CardTitle.TLabel').grid(row=0,column=0,columnspan=6,sticky='w',pady=(0,8))
        labels={'thrust_n':'Motor thrust (N)','fixed_mass_g':'Fixed moving hardware (g)',
                'friction_n':'Rail friction (N)','assembly_area_m2':'Exposed carriage area (m²)',
                'drag_coefficient':'Carriage drag coefficient','air_density_kg_m3':'Air density (kg/m³)',
                'target_height_m':'Target height (m)','duration_s':'Maximum simulated time (s)'}
        defaults=MotionSettings();self.inputs={}
        for i,(key,label) in enumerate(labels.items()):
            row=1+i//3;col=2*(i%3)
            ttk.Label(controls,text=label).grid(row=row,column=col,sticky='w',padx=6,pady=4)
            value=tk.StringVar(value=str(getattr(defaults,key)));self.inputs[key]=value
            ttk.Entry(controls,textvariable=value,width=10).grid(row=row,column=col+1,sticky='ew',padx=6,pady=4)
            value.trace_add('write',self.inputs_changed)
        responsive_form(controls,breakpoint=1100)
        metrics=ttk.Frame(body);metrics.pack(fill='x',pady=(0,12));self.metrics={};cards=[]
        for col,(key,label) in enumerate((('original','Original payload / climb time'),('optimized','Optimized payload / climb time'),('difference','Predicted climb-time reduction'))):
            card=ttk.Frame(metrics,style='MetricCard.TFrame',padding=12);card.grid(row=0,column=col,sticky='nsew',padx=4)
            cards.append(card);metrics.columnconfigure(col,weight=1,uniform='motion_metrics')
            ttk.Label(card,text=label,style='MetricHint.TLabel',wraplength=210).pack(anchor='w')
            value=tk.StringVar(value='—');self.metrics[key]=value
            ttk.Label(card,textvariable=value,style='MetricHint.TLabel',font=('Helvetica',16,'bold')).pack(anchor='w',pady=(8,0))
        layout=[None]
        def arrange_metrics(event):
            columns=3 if event.width>=900 else 1
            if layout[0]==columns:return
            layout[0]=columns
            for col in range(3):metrics.columnconfigure(col,weight=1 if col<columns else 0,uniform='motion_metrics' if col<columns else '')
            for i,card in enumerate(cards):card.grid_configure(row=i//columns,column=i%columns,pady=4)
        metrics.bind('<Configure>',arrange_metrics)
        viewer=ttk.Frame(body,style='Card.TFrame',padding=12);viewer.pack(fill='x',pady=(0,12))
        toolbar=ttk.Frame(viewer);toolbar.pack(fill='x')
        self.play_button=ttk.Button(toolbar,text='Replay motion',command=self.play,state='disabled');self.play_button.pack(side='right')
        self.pause_button=ttk.Button(toolbar,text='Pause',command=self.stop_playback,state='disabled');self.pause_button.pack(side='right',padx=8)
        ttk.Label(toolbar,text='Actual uploaded geometry · visual scale enlarged equally',style='Hint.TLabel',wraplength=450).pack(side='left')
        self.viewer=ttk.Frame(viewer,height=340);self.viewer.pack(fill='x');self.viewer.pack_propagate(False)
        self.waiting_label=ttk.Label(self.viewer,text='Both views will show your uploaded mesh.\nWaiting for completed original and optimized slices.',anchor='center',justify='center',style='Hint.TLabel')
        self.waiting_label.pack(fill='both',expand=True)
        self.chart_container=ttk.Frame(body,height=430);self.chart_container.pack(fill='x');self.chart_container.pack_propagate(False)
        self.notes=tk.StringVar(value='The 1.5 N / 100 g defaults illustrate a small carriage; edit them for your apparatus. Starts at rest on a bottom stop. Constant thrust and opposing rail friction; still air.')
        ttk.Label(body,textvariable=self.notes,wraplength=1100,style='Hint.TLabel').pack(fill='x',pady=12)
        self.poll_timer=self.after(80,self.poll)

    def settings(self):
        try:return MotionSettings(**{key:float(value.get()) for key,value in self.inputs.items()})
        except ValueError as exc:raise ValueError('Enter numeric values for every apparatus assumption.') from exc

    def inputs_changed(self,*_):
        self.invalidate('Updating shared assumptions…')
        if self.input_timer:self.after_cancel(self.input_timer)
        self.input_timer=self.after(400,self.refresh)

    def invalidate(self,message='Waiting: complete optimization for the current model.'):
        self.generation+=1;self.cancel.set();self.stop_playback();self.result=None
        for value in self.metrics.values():value.set('—')
        for key in ('motion_3d','motion_prediction'):
            canvas=self.app.figures.pop(key,None)
            if canvas:self.app.dispose_figure(canvas)
        self.waiting_label.pack(fill='both',expand=True)
        self.state.set(message);self.play_button.configure(state='disabled')
        self.notes.set('Same carriage drag and apparatus assumptions for both cases. Predictions are separate from measured rig data.')
        self.pending=False

    def refresh(self):
        self.input_timer=None
        if self.disposed:return
        self.invalidate()
        model,report=self.app.model,self.app.report
        if self.app.busy:self.state.set('Waiting: current slicing operation has not finished.');return
        if model is None:self.state.set('Waiting: upload your STL model in Optimize.');return
        try:
            sliced_cases(model,report)
            settings=self.settings();settings.validate()
        except ValueError as exc:self.state.set(str(exc));return
        gc.collect()  # Dispose closed Tk/figure cycles on the UI thread.
        self.pending=True;generation=self.generation;self.cancel=cancel=Event()
        self.state.set('Integrating vertical motion using both sliced payload masses…')
        # No Tk objects, Matplotlib canvases or app methods are accessed here.
        queue=self.queue
        def solve():
            try:queue.put((generation,compare_motion(model,report,settings,cancel),None))
            except Exception as exc:queue.put((generation,None,str(exc)))
        self.worker_thread=Thread(target=solve,daemon=True);self.worker_thread.start()

    def poll(self):
        if self.disposed:return
        try:
            while True:
                generation,result,error=self.queue.get_nowait()
                if generation!=self.generation:continue
                self.pending=False
                if error:self.state.set(error)
                else:
                    try:self.display(result)
                    except Exception as exc:self.invalidate('Unable to display this simulation: '+str(exc))
        except Empty:pass
        self.poll_timer=self.after(80,self.poll)

    def display(self,result):
        # The report may have been replaced while the computation was running.
        if self.app.model is None or result['input_sha256']!=self.app.model.sha256:return
        self.result=result
        for key in ('original','optimized'):
            row=result[key];time=row['time_to_target_s']
            self.metrics[key].set(f"{row['payload_mass_g']:.2f} g / {time:.3f} s" if time is not None else f"{row['payload_mass_g']:.2f} g / not reached")
        difference=result['climb_time_reduction_percent']
        self.metrics['difference'].set(f'{difference:.2f}%' if difference is not None else 'Not comparable')
        self.state.set(f"{self.app.model.path.name} · Physics simulation ready. Original: {result['original']['status']}; optimized: {result['optimized']['status']}.")
        projection=result['payload_projection'];area=projection['area_mm2']
        projected=f'Payload projection along Z: {area:.2f} mm² (numerical approximation).' if area is not None else projection['method']
        dims=' × '.join(f'{v:.2f}' for v in result['dimensions_mm'])
        self.notes.set(f"Oriented dimensions: {dims} mm; rotation X,Y,Z: {result['orientation_degrees']}°. {projected}\n"
                       f"Total moving mass: {result['original']['total_mass_g']:.2f} g original; {result['optimized']['total_mass_g']:.2f} g optimized. "
                       f"Original infill: {result['cases'][0]['densities']}%; optimized infill: {result['cases'][1]['densities']}%.\n"
                       +result['drag_scope']+' Time reduction is (original − optimized) / original; unavailable if either target is not reached.')
        self.waiting_label.pack_forget()
        self.draw_models(result);self.draw_charts(result)
        self.play_button.configure(state='normal');self.play()

    def draw_models(self,result):
        triangles=result['triangles'];extent=np.ptp(triangles,axis=(0,1)).max()
        # Shared affine display transform only: retains the uploaded facets, not
        # shape templates. Dynamics never see this visual enlargement.
        visual=triangles-triangles.min(axis=(0,1));visual=visual*(result['settings']['target_height_m']*.28/extent)
        visual[:,:,:2]-=np.ptp(visual[:,:,:2],axis=(0,1))/2
        self.visual_triangles=visual
        fig=Figure(figsize=(9,3.4),facecolor='#f8fafc');self.axes=[];self.artists=[]
        target=result['settings']['target_height_m'];span=max(np.ptp(visual,axis=(0,1)).max(),target*.28)
        # Draw the actual mesh. Very dense rendering can be expensive, but no
        # geometry substitution, bounding-box proxy or shape template is used.
        for i,(key,title,color) in enumerate((('original','Original','#38bdf8'),('optimized','Optimized','#0d9488'))):
            ax=fig.add_subplot(1,2,i+1,projection='3d',facecolor='#f8fafc')
            artist=Poly3DCollection(visual,facecolor=color,edgecolor='none',alpha=.9);ax.add_collection3d(artist)
            ax.set(xlim=(-span*.7,span*.7),ylim=(-span*.7,span*.7),zlim=(0,target+span*1.3))
            ax.set_box_aspect((span*1.4,span*1.4,target+span*1.3));ax.set_proj_type('ortho');ax.view_init(elev=20,azim=45)
            ax.set_xticks([]);ax.set_yticks([]);ax.set_zlabel('Height (m)',fontsize=9,labelpad=12)
            ax.set_title(title,fontsize=12,color='#075985');ax.plot([-span*.6,span*.6],[0,0],[target,target],color='#94a3b8',linestyle='--')
            self.axes.append(ax);self.artists.append(artist)
        fig.subplots_adjust(left=0,right=1,bottom=.03,top=.9,wspace=.04)
        self.app.show_figure('motion_3d',self.viewer,fig)
        widget=self.app.figures['motion_3d'].get_tk_widget();widget._viewport=True;widget.configure(width=1,height=1)

    def draw_charts(self,result):
        fig=Figure(figsize=(9,4.3),layout='constrained',facecolor='white');axes=fig.subplots(3,1,sharex=True)
        for key,label,color in (('original','Original','#0284c7'),('optimized','Optimized','#0d9488')):
            row=result[key]
            for ax,value in zip(axes,('height_m','velocity_m_s','acceleration_m_s2')):
                ax.plot(row['time_s'],row[value],label=label,color=color);ax.grid(alpha=.2)
        axes[0].set_ylabel('Height (m)');axes[0].legend(fontsize=9)
        axes[1].set_ylabel('Velocity (m/s)');axes[2].set_ylabel('Accel. (m/s²)');axes[2].set_xlabel('Simulated time (s)')
        fig.suptitle('Physics predictions · same apparatus assumptions',fontsize=11)
        self.app.show_figure('motion_prediction',self.chart_container,fig)
        self.app.figures['motion_prediction'].get_tk_widget().configure(width=1,height=1)

    def set_playback_time(self,time_s):
        if not self.result:return
        for key,artist in zip(('original','optimized'),self.artists):
            row=self.result[key]
            height=float(np.interp(time_s,row['time_s'],row['height_m']))
            displaced=self.visual_triangles.copy();displaced[:,:,2]+=height;artist.set_verts(displaced)
        self.app.figures['motion_3d'].draw_idle()

    def play(self):
        if not self.result:return
        self.stop_playback();self.play_start=monotonic();self.pause_button.configure(state='normal')
        self.animate()

    def animate(self):
        if not self.result or self.disposed:return
        duration=max(row['time_s'][-1] for row in (self.result['original'],self.result['optimized']))
        time=(monotonic()-self.play_start)*max(1,duration/12)
        self.set_playback_time(min(time,duration))
        if time<duration:self.play_timer=self.after(60,self.animate)
        else:self.play_timer=None;self.pause_button.configure(state='disabled')

    def stop_playback(self):
        if self.play_timer:self.after_cancel(self.play_timer);self.play_timer=None
        if hasattr(self,'pause_button'):self.pause_button.configure(state='disabled')

    def close(self):
        self.disposed=True;self.cancel.set();self.stop_playback()
        if self.input_timer:self.after_cancel(self.input_timer)
        self.after_cancel(self.poll_timer)
        if self.worker_thread and self.worker_thread.is_alive():self.worker_thread.join(timeout=.2)
        self.inputs.clear();self.metrics.clear();self.state=None;self.notes=None
