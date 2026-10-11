"""Three-tab Tkinter interface. Workers communicate through a main-thread queue."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from threading import Event, Thread
import json
import math
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
from .file_import import load_model
from .structural import Settings, make_regions, DISCLAIMER
from .optimizer import generate_candidates, optimize, write_reports
from .slicer import OrcaSlicer, Profiles, DEFAULT_EXECUTABLE
from .analysis import read_csv, save_csv, process_trials, summarize, stiffness, synthetic_readings
from .sensor import Recorder, ports
from .validation import inspect_gcode
from .visualization import heatmap_figure, motion_figure, comparison_figure

class Application(tk.Tk):
    def __init__(self, examples: Path):
        super().__init__()
        self.title('AeroSlice • Into the Skies');self.geometry('1250x900');self.minsize(1000,720)
        self.examples=examples;self.model=None;self.report=None;self.trials=[];self.readings=[];self.summary=None
        self.busy=False;self.queue=Queue();self.cancel=Event();self.recorder=Recorder();self.figures={}
        self.config_path=Path.home()/'Library/Application Support/AeroSlice/config.json'
        self.config_data=json.loads((Path(__file__).parent/'config.json').read_text())
        if self.config_path.exists():
            try: self.config_data.update(json.loads(self.config_path.read_text()))
            except (OSError,ValueError): pass
        self.v={};style=ttk.Style(self);style.theme_use('clam')
        style.configure('TFrame',background='#f1f5f9');style.configure('TLabel',background='#f1f5f9',foreground='#0f172a')
        style.configure('TLabelframe',background='#f1f5f9');style.configure('TLabelframe.Label',background='#f1f5f9',foreground='#0369a1',font=('Helvetica',12,'bold'))
        style.configure('TButton',padding=(10,6));style.configure('Accent.TButton',background='#0284c7',foreground='white')
        style.configure('Treeview',rowheight=27);style.configure('TNotebook.Tab',padding=(20,10))
        header=ttk.Frame(self,padding=(20,12));header.pack(fill='x')
        ttk.Label(header,text='AeroSlice',font=('Helvetica',25,'bold')).pack(side='left')
        ttk.Label(header,text='  Physics-informed infill search • Into the Skies',font=('Helvetica',12)).pack(side='left',padx=10)
        self.status=tk.StringVar(value='Upload a removable payload to begin. Stiffness: NOT YET VERIFIED.')
        ttk.Label(self,textvariable=self.status,padding=(20,8),wraplength=1180).pack(fill='x')
        book=ttk.Notebook(self);book.pack(fill='both',expand=True,padx=15,pady=5)
        self.optimize_tab=ttk.Frame(book,padding=10);self.sensor_tab=ttk.Frame(book,padding=10);self.dashboard_tab=ttk.Frame(book,padding=10)
        for title,tab in [('Optimize Payload',self.optimize_tab),('Vertical Rig Testing',self.sensor_tab),('Results Dashboard',self.dashboard_tab)]:book.add(tab,text=title)
        self._optimize_ui();self._sensor_ui();self._dashboard_ui()
        for key in ('orca','printer_profile','process_profile','filament_profile'):
            self.v[key].trace_add('write',lambda *_:self.update_actions())
        self.protocol('WM_DELETE_WINDOW',self.close);self.after(100,self.poll);self.update_actions()

    def variable(self,key,value):
        self.v[key]=tk.StringVar(value=str(value));return self.v[key]

    def field(self,parent,key,label,default,row,column=0,width=13,choices=None):
        ttk.Label(parent,text=label).grid(row=row,column=column*2,sticky='w',padx=5,pady=4)
        var=self.variable(key,default)
        widget=ttk.Combobox(parent,textvariable=var,values=choices,state='readonly',width=width) if choices else ttk.Entry(parent,textvariable=var,width=width)
        widget.grid(row=row,column=column*2+1,sticky='ew',padx=5,pady=4);return widget

    def _optimize_ui(self):
        panel=ttk.Frame(self.optimize_tab);panel.pack(side='left',fill='both',expand=True,padx=(0,10))
        self.action_footer=ttk.Frame(panel);self.action_footer.pack(side='bottom',fill='x',pady=(6,0))
        scroll=ttk.Frame(panel);scroll.pack(fill='both',expand=True)
        self.controls_canvas=tk.Canvas(scroll,highlightthickness=0,background='#f1f5f9',width=575)
        scrollbar=ttk.Scrollbar(scroll,orient='vertical',command=self.controls_canvas.yview)
        scrollbar.pack(side='right',fill='y');self.controls_canvas.pack(side='left',fill='both',expand=True)
        self.controls_canvas.configure(yscrollcommand=scrollbar.set)
        left=ttk.Frame(self.controls_canvas)
        window=self.controls_canvas.create_window((0,0),window=left,anchor='nw')
        left.bind('<Configure>',lambda _:self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox('all')))
        self.controls_canvas.bind('<Configure>',lambda e:self.controls_canvas.itemconfigure(window,width=e.width))
        self.bind('<MouseWheel>',self.scroll_controls)
        right=ttk.Frame(self.optimize_tab);right.pack(side='right',fill='both',expand=True)
        upload=ttk.LabelFrame(left,text='1  Upload & inspect',padding=10);upload.pack(fill='x')
        self.upload_button=ttk.Button(upload,text='Upload 3D Model / Replace Model',style='Accent.TButton',command=self.upload_model);self.upload_button.pack(side='left')
        ttk.Button(upload,text='Example payload',command=self.demo_model).pack(side='left',padx=8)
        self.model_info=tk.StringVar(value='No model selected • STL or compatible single-object 3MF')
        ttk.Label(left,textvariable=self.model_info,wraplength=540,padding=8).pack(fill='x')
        settings=ttk.LabelFrame(left,text='2  Optimization & load assumptions',padding=5);settings.pack(fill='x',pady=4)
        fields=[('baseline','Baseline (%)',20),('minimum','Minimum (%)',10),('maximum','Maximum (%)',30),('candidate_count','Candidates',8),
                ('coverage','Heuristic coverage',.75),('acceleration','Axial acceleration (m/s²)',0)]
        for i,(key,label,val) in enumerate(fields): self.field(settings,key,label,val,i//2,i%2)
        self.field(settings,'contact_axis','Contact / slab axis','z',3,0,choices=['x','y','z'])
        self.field(settings,'contact_face','Platform face','min',3,1,choices=['min','max'])
        self.field(settings,'load_axis','Loading axis','z',4,0,choices=['x','y','z'])
        self.field(settings,'protected','Protected minima (%)','15,10,10',4,1)
        self.field(settings,'rotation','Rotation X,Y,Z (°)','0,0,0',5,0)
        self.field(settings,'demand','Custom demand (0–1)','',5,1)
        self.field(settings,'fixed_mass','Fixed hardware mass (g)','',6,0)
        ttk.Label(settings,text='Blank demand uses the declared full-face load-path heuristic. Blank hardware mass stays unverified.',wraplength=540).grid(row=7,column=0,columnspan=4,sticky='w',padx=5,pady=5)
        integration=ttk.LabelFrame(left,text='3  OrcaSlicer & export',padding=5);integration.pack(fill='x',pady=5)
        self.field(integration,'orca','Executable',self.config_data['orca_executable'],0,width=42)
        ttk.Button(integration,text='Browse',command=self.choose_executable).grid(row=0,column=2)
        self.field(integration,'profile_root','Profiles folder',self.config_data['profile_root'],1,width=42)
        ttk.Button(integration,text='Browse',command=self.choose_profiles_root).grid(row=1,column=2)
        for row,key,title in [(2,'printer_profile','Printer profile'),(3,'process_profile','Process profile'),(4,'filament_profile','Filament profile')]:
            self.field(integration,key,title,self.config_data[key],row,width=42)
            ttk.Button(integration,text='JSON…',command=lambda k=key:self.choose_profile(k)).grid(row=row,column=2)
        self.field(integration,'output','Output folder',str(Path.cwd()/'outputs'),5,width=42)
        ttk.Button(integration,text='Choose',command=self.choose_output).grid(row=5,column=2)
        self.manual=tk.BooleanVar(value=False)
        ttk.Checkbutton(integration,text='Mode: Semiautomated (recommendations and manual instructions)',variable=self.manual,command=self.update_actions).grid(row=6,column=0,columnspan=3,sticky='w',pady=5)
        actions=ttk.Frame(self.action_footer);actions.pack(fill='x',pady=4)
        self.check_button=ttk.Button(actions,text='Check Compatibility',command=self.check_compatibility);self.check_button.grid(row=0,column=0,padx=2,pady=2,sticky='ew')
        self.generate_button=ttk.Button(actions,text='Generate Candidates',command=self.generate);self.generate_button.grid(row=0,column=1,padx=2,pady=2,sticky='ew')
        self.optimize_button=ttk.Button(actions,text='Optimize Model',style='Accent.TButton',command=self.start_optimization);self.optimize_button.grid(row=1,column=0,padx=2,pady=2,sticky='ew')
        self.cancel_button=ttk.Button(actions,text='Cancel',command=self.cancel.set);self.cancel_button.grid(row=1,column=1,padx=2,pady=2,sticky='ew')
        actions.columnconfigure((0,1),weight=1)
        self.progress_bar=ttk.Progressbar(self.action_footer,mode='indeterminate');self.progress_bar.pack(fill='x')
        preview=ttk.LabelFrame(right,text='Geometry preview • rotate with the mouse',padding=4);preview.pack(fill='both',expand=True)
        self.preview_container=preview
        results=ttk.LabelFrame(right,text='Slicer estimates & candidates',padding=8);results.pack(fill='both',expand=True,pady=8)
        self.tree=ttk.Treeview(results,columns=('densities','mass','time','status'),show='headings',height=8)
        for key,label,width in [('densities','Region densities (%)',170),('mass','Mass (g)',80),('time','Time (s)',80),('status','Validation',100)]:self.tree.heading(key,text=label);self.tree.column(key,width=width)
        self.tree.pack(fill='both',expand=True)
        bar=ttk.Frame(results);bar.pack(fill='x',pady=5)
        self.export_button=ttk.Button(bar,text='Export Optimized Files',command=self.export_files);self.export_button.pack(side='left')
        self.open_button=ttk.Button(bar,text='Open Output Folder',command=self.open_output);self.open_button.pack(side='left',padx=5)
        ttk.Button(bar,text='Import authentic G-code comparison',command=self.import_slices).pack(side='left')
        ttk.Label(right,text=DISCLAIMER,wraplength=560,foreground='#92400e').pack(fill='x',pady=4)

    def scroll_controls(self,event):
        canvas=self.controls_canvas
        if canvas.winfo_ismapped() and canvas.winfo_rootx()<=event.x_root<canvas.winfo_rootx()+canvas.winfo_width() and canvas.winfo_rooty()<=event.y_root<canvas.winfo_rooty()+canvas.winfo_height():
            steps=int(-event.delta/120) if abs(event.delta)>=120 else int(-event.delta)
            canvas.yview_scroll(steps,'units')

    def _sensor_ui(self):
        controls=ttk.LabelFrame(self.sensor_tab,text='Sensor-only recording & CSV analysis',padding=8);controls.pack(fill='x')
        self.port_box=self.field(controls,'port','ESP32 port','',0,0,width=25,choices=ports())
        ttk.Button(controls,text='Refresh ports',command=lambda:self.port_box.configure(values=ports())).grid(row=0,column=2)
        ttk.Button(controls,text='Connect',command=self.connect_serial).grid(row=0,column=3)
        ttk.Button(controls,text='Disconnect',command=self.recorder.disconnect).grid(row=0,column=4)
        self.field(controls,'trial_id','Record trial ID','1',1,0)
        self.field(controls,'variant','Record variant','baseline',1,1)
        ttk.Button(controls,text='Start recording',command=self.start_recording).grid(row=1,column=4)
        ttk.Button(controls,text='Stop & analyze',command=self.stop_recording).grid(row=1,column=5)
        self.field(controls,'start_mm','Start distance (mm)','',2,0)
        self.field(controls,'end_mm','End distance (mm)','',2,1)
        self.field(controls,'max_gap','Max gap (s)',.5,2,2)
        self.field(controls,'sensor_min','Reliable min (mm)','',3,0)
        self.field(controls,'sensor_max','Reliable max (mm)','',3,1)
        self.field(controls,'baseline_variant','Baseline variant','baseline',3,2)
        self.smooth=tk.BooleanVar(value=False);self.paired=tk.BooleanVar(value=False)
        ttk.Checkbutton(controls,text='Median smoothing (processed data)',variable=self.smooth).grid(row=4,column=0,columnspan=2,sticky='w')
        ttk.Checkbutton(controls,text='Paired by matched trial ID',variable=self.paired).grid(row=4,column=2,columnspan=2,sticky='w')
        self.field(controls,'trigger_pin','Example trigger GPIO',25,5,0)
        self.field(controls,'echo_pin','Example echo GPIO',26,5,1)
        self.field(controls,'interval_ms','Interval (ms)',self.config_data['sensor_interval_ms'],5,2)
        ttk.Button(controls,text='Configure sensor',command=self.configure_sensor).grid(row=5,column=6)
        actions=ttk.Frame(self.sensor_tab);actions.pack(fill='x',pady=8)
        for text,command in [('Upload Sensor CSV',self.upload_sensor),('Synthetic demo',self.demo_sensor),('Analyze thresholds',self.analyze_sensor),('Save raw CSV',self.save_sensor),('Export sensor report & graphs',self.export_sensor)]:ttk.Button(actions,text=text,command=command).pack(side='left',padx=3)
        self.trial_box=ttk.Combobox(actions,state='readonly',width=23);self.trial_box.pack(side='left',padx=4);self.trial_box.bind('<<ComboboxSelected>>',lambda _:self.show_trial())
        self.sensor_label=tk.StringVar(value='Upload measurements or use the clearly labeled synthetic demo. Motor activation is separate.')
        ttk.Label(self.sensor_tab,textvariable=self.sensor_label,wraplength=1150).pack(fill='x')
        self.motion_container=ttk.Frame(self.sensor_tab);self.motion_container.pack(fill='both',expand=True,pady=6)

    def _dashboard_ui(self):
        box=ttk.LabelFrame(self.dashboard_tab,text='Estimated, measured & unverified results',padding=8);box.pack(fill='x')
        self.field(box,'measured_baseline','Measured baseline payload (g)','',0,0)
        self.field(box,'measured_adaptive','Measured adaptive payload (g)','',0,1)
        ttk.Button(box,text='Update measured comparison',command=self.measured_dashboard).grid(row=0,column=4)
        self.field(box,'force','Measured force (N)','',1,0)
        self.field(box,'deflection','Measured deflection (mm)','',1,1)
        self.field(box,'stiff_mass','Specimen mass (g)','',2,0)
        self.field(box,'conditions','Loading/support conditions','',2,1,width=30)
        ttk.Button(box,text='Calculate measured stiffness',command=self.calculate_stiffness).grid(row=2,column=4)
        self.dashboard_text=tk.Text(self.dashboard_tab,height=17,wrap='word',font=('Helvetica',12),bg='white',fg='#0f172a',padx=15,pady=12)
        self.dashboard_text.pack(fill='both',expand=True,pady=10)
        self.comparison_container=ttk.Frame(self.dashboard_tab);self.comparison_container.pack(fill='both',expand=True)
        self.dashboard_write('No optimization results yet.\nStructural stiffness has not been independently verified.\nTotal moving mass, thrust and hardware geometry: NOT YET VERIFIED.\n\na = (T − mg − friction − drag) / m\nLower payload mass may improve acceleration when forces remain comparable. Guided vertical travel does not reproduce free flight.\n')

    def dashboard_write(self,text,append=False):
        self.dashboard_text.configure(state='normal')
        if not append:self.dashboard_text.delete('1.0','end')
        self.dashboard_text.insert('end',text+'\n');self.dashboard_text.configure(state='disabled')

    def show_figure(self,key,container,figure):
        if key in self.figures:
            self.dispose_figure(self.figures.pop(key))
        canvas=FigureCanvasTkAgg(figure,master=container);canvas.draw();canvas.get_tk_widget().pack(fill='both',expand=True);self.figures[key]=canvas

    def dispose_figure(self,canvas):
        # Matplotlib retains Tk PhotoImages in a figure/canvas reference cycle.
        # Release them here on the Tk thread, before a worker can trigger GC.
        widget=canvas.get_tk_widget()
        idle=getattr(canvas,'_idle_draw_id',None)
        if idle is not None:
            widget.after_cancel(idle)
            canvas._idle_draw_id=None
        widget.destroy()
        canvas.figure.clear()
        canvas.figure.set_canvas(None)
        canvas._tkphoto=None

    def triple(self,key):
        values=tuple(float(v.strip()) for v in self.v[key].get().split(','))
        if len(values)!=3:raise ValueError(f'{key}: enter three comma-separated values.')
        return values

    def settings(self):
        s=Settings(baseline=float(self.v['baseline'].get()),minimum=float(self.v['minimum'].get()),maximum=float(self.v['maximum'].get()),
                   candidate_count=int(self.v['candidate_count'].get()),densities=tuple(self.config_data['density_set']),
                   contact_axis=self.v['contact_axis'].get(),contact_face=self.v['contact_face'].get(),load_axis=self.v['load_axis'].get(),
                   acceleration=float(self.v['acceleration'].get()),rotation=self.triple('rotation'),protected_minima=self.triple('protected'),
                   demand_override=self.triple('demand') if self.v['demand'].get().strip() else None,coverage=float(self.v['coverage'].get()),
                   fixed_hardware_mass_g=float(self.v['fixed_mass'].get()) if self.v['fixed_mass'].get().strip() else None)
        s.validate();return s

    def profiles(self):return Profiles(self.v['printer_profile'].get(),self.v['process_profile'].get(),self.v['filament_profile'].get())
    def slicer(self):return OrcaSlicer(self.v['orca'].get(),self.v['profile_root'].get(),self.config_data['slicer_timeout_s'],self.cancel)

    def update_actions(self):
        loaded=self.model is not None and not self.busy
        self.upload_button.configure(state='disabled' if self.busy else 'normal')
        self.generate_button.configure(state='normal' if loaded else 'disabled')
        available=Path(self.v['orca'].get()).is_file() and all(self.v[k].get().strip() for k in ('printer_profile','process_profile','filament_profile'))
        self.optimize_button.configure(state='normal' if loaded and (available or self.manual.get()) else 'disabled',text='Prepare Manual Workflow' if self.manual.get() else 'Optimize Model')
        self.check_button.configure(state='disabled' if self.busy else 'normal');self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        successful=self.report is not None and self.report.get('status')=='AUTOMATED SUCCESS' and not self.busy
        self.export_button.configure(state='normal' if successful else 'disabled')
        self.open_button.configure(state='normal' if self.report and not self.busy else 'disabled')

    def error(self,exc):self.status.set(str(exc));messagebox.showerror('AeroSlice',str(exc),parent=self)

    def set_model(self,path):
        if self.busy:return
        try:
            model=load_model(path)
            self.model=model;self.report=None
            dims=' × '.join(f'{x:.2f}' for x in model.dimensions)
            self.model_info.set(f'{model.path.name} • {model.path.suffix} • {model.kind}\nDimensions: {dims} mm • {len(model.triangles):,} triangles\nOrientation: imported, with configured X/Y/Z rotation applied on optimization.\n'+ '\n'.join(model.warnings))
            fig=Figure(figsize=(5,3),layout='constrained');ax=fig.add_subplot(projection='3d')
            # Preview decimation is visual only; optimization uses the full mesh.
            triangles=model.triangles[::max(1,len(model.triangles)//3000)]
            ax.add_collection3d(Poly3DCollection(triangles,facecolor='#38bdf8',edgecolor='#0369a1',linewidth=.15,alpha=.75))
            lo,hi=model.bounds
            ax.set(xlim=(lo[0],hi[0]),ylim=(lo[1],hi[1]),zlim=(lo[2],hi[2]),xlabel='X (mm)',ylabel='Y (mm)',zlabel='Z (mm)');ax.set_box_aspect(model.dimensions)
            self.show_figure('preview',self.preview_container,fig)
            self.status.set('Model validated. Original file preserved.');self.tree.delete(*self.tree.get_children());self.update_actions()
        except Exception as exc:self.error(exc)

    def upload_model(self):
        path=filedialog.askopenfilename(title='Upload removable payload',filetypes=[('3D models','*.stl *.STL *.3mf *.3MF')])
        if path:self.set_model(path)
    def demo_model(self):self.set_model(self.examples/'demo_payload.stl')
    def choose_executable(self):
        p=filedialog.askopenfilename(title='Choose OrcaSlicer executable',initialdir='/Applications/OrcaSlicer.app/Contents/MacOS')
        if p:self.v['orca'].set(p);self.update_actions()
    def choose_profiles_root(self):
        p=filedialog.askdirectory(title='Choose OrcaSlicer profiles folder')
        if p:self.v['profile_root'].set(p)
    def choose_profile(self,key):
        p=filedialog.askopenfilename(title='Select profile JSON',filetypes=[('Profile JSON','*.json')])
        if p:self.v[key].set(p);self.update_actions()
    def choose_output(self):
        p=filedialog.askdirectory(title='Select output folder')
        if p:self.v['output'].set(p)

    def save_preferences(self):
        for key in ('printer_profile','process_profile','filament_profile','profile_root'):self.config_data[key]=self.v[key].get()
        self.config_data['orca_executable']=self.v['orca'].get();self.config_path.parent.mkdir(parents=True,exist_ok=True)
        self.config_path.write_text(json.dumps(self.config_data,indent=2))

    def worker(self,operation):
        self.busy=True;self.cancel.clear();self.progress_bar.start();self.update_actions()
        def run():
            try:self.queue.put(('complete',operation()))
            except Exception as exc:self.queue.put(('error',str(exc)))
        # Keep the worker alive long enough to terminate Orca and write its report
        # if the window closes during slicing. No Tk calls occur in this thread.
        self.worker_thread=Thread(target=run,daemon=False)
        self.worker_thread.start()

    def generate(self):
        if not self.model:return
        try:
            settings=self.settings();model=self.model
            def operation():
                regions,notes=make_regions(model.oriented(settings.rotation),settings)
                return {'kind':'candidates','regions':regions,'candidates':generate_candidates(regions,settings),'notes':notes}
            self.worker(operation)
        except Exception as exc:self.error(exc)

    def start_optimization(self):
        if not self.model:return
        try:
            settings=self.settings();model=self.model;slicer=self.slicer();profiles=self.profiles();output=Path(self.v['output'].get());manual=self.manual.get()
            self.save_preferences()
            self.worker(lambda:optimize(model,settings,output,slicer,profiles,lambda msg:self.queue.put(('progress',msg)),manual))
        except Exception as exc:self.error(exc)

    def check_compatibility(self):
        try:
            slicer=self.slicer();profiles=self.profiles();output=Path(self.v['output'].get());model=load_model(self.examples/'demo_payload.stl')
            self.worker(lambda:{'kind':'compatibility','result':optimize(model,Settings(candidate_count=3),output,slicer,profiles,lambda msg:self.queue.put(('progress','Compatibility: '+msg)))})
        except Exception as exc:self.error(exc)

    def poll(self):
        try:
            while True:
                kind,value=self.queue.get_nowait()
                if kind=='progress':self.status.set(value)
                elif kind=='error':
                    self.busy=False;self.progress_bar.stop();self.update_actions();self.error(value)
                elif kind=='complete':
                    self.busy=False;self.progress_bar.stop()
                    if value.get('kind')=='candidates':
                        self.tree.delete(*self.tree.get_children())
                        for c in value['candidates']:self.tree.insert('', 'end',values=(str(c.densities),'—','—','Not sliced'))
                        self.show_figure('preview',self.preview_container,heatmap_figure(value['regions'],value['candidates']))
                        self.status.set('Candidate settings generated. These are conceptual assignments; no slicing results yet.')
                    elif value.get('kind')=='compatibility':
                        result=value['result'];self.status.set('Compatibility test on example payload: '+result['status']+' • '+result['run_directory'])
                        self.dashboard_write('Example-payload compatibility test\n'+json.dumps(result['compatibility'],indent=2),append=True)
                    else:self.show_report(value)
                    self.update_actions()
        except Empty:pass
        try:
            while True:
                kind,value=self.recorder.messages.get_nowait()
                if kind=='reading':self.sensor_label.set(f'Live sensor: {value.timestamp_ms:g} ms • {value.distance_mm} mm • {value.error or "valid"}')
                elif kind=='error':self.sensor_label.set('Sensor error: '+str(value))
        except Empty:pass
        self.after(100,self.poll)

    def show_report(self,report):
        self.report=report;self.tree.delete(*self.tree.get_children())
        for c in report['candidates']:
            m=c.get('metrics',{});self.tree.insert('', 'end',values=(', '.join(f'{d:g}' for d in c['densities']),m.get('mass_g','—'),m.get('time_s','—'),c.get('status','NOT TESTED')))
        self.status.set(report['status']+' • '+report['message'])
        lines=[report['status'],report['message'],f"Saved run: {report['run_directory']}",f"OrcaSlicer: {report.get('orca_version','NOT TESTED')}"]
        if 'selected' in report:
            selected=next(c for c in report['candidates'] if c['id']==report['selected']);baseline=report['candidates'][0]
            lines.extend([f"Baseline: {baseline['metrics']['mass_g']:.2f} g, {baseline['metrics']['filament_mm']:.1f} mm filament, {baseline['metrics']['time_s']:.0f} s (estimated)",
                          f"Selected: {selected['metrics']['mass_g']:.2f} g, {selected['metrics']['filament_mm']:.1f} mm filament, {selected['metrics']['time_s']:.0f} s (estimated)",
                          f"Material savings: {report['savings_percent_estimated']:.2f}% (estimated)",f"Region densities: {selected['densities']}"])
        lines.extend(['Total moving mass: '+str(report.get('total_moving_mass_g_estimated','NOT YET VERIFIED')),'Center of mass: unavailable without resolved material distribution.','Stiffness: NOT YET VERIFIED.',*report.get('manual_steps',[]),'Files:',*report.get('exports',{}).values()])
        self.dashboard_write('\n'.join(lines))
        if report['status']=='FAILED':messagebox.showerror('Optimization failed',report['message'],parent=self)

    def export_files(self):
        if not self.report or self.report['status']!='AUTOMATED SUCCESS':return
        destination=filedialog.askdirectory(title='Export validated files and reports')
        if not destination:return
        try:
            source=Path(self.report['run_directory']);target=Path(destination)/source.name
            if target.resolve()==source.resolve():self.status.set('Validated files are already saved in this folder.');return
            shutil.copytree(source,target)
            self.status.set(f'Validated project, sliced files and reports exported to {target}')
        except Exception as exc:self.error(exc)
    def open_output(self):
        if self.report:subprocess.Popen(['open',self.report['run_directory']])

    def import_slices(self):
        base=filedialog.askopenfilename(title='Import authentic baseline G-code',filetypes=[('G-code','*.gcode')])
        if not base:return
        candidate=filedialog.askopenfilename(title='Import authentic candidate G-code',filetypes=[('G-code','*.gcode')])
        if not candidate:return
        try:
            a,b=inspect_gcode(Path(base)),inspect_gcode(Path(candidate))
            result={'status':'SEMIAUTOMATED','baseline':asdict(a),'candidate':asdict(b),'savings_percent_estimated':(a.mass_g-b.mass_g)/a.mass_g*100,
                    'source_files':[base,candidate],'note':'Authentic slicer estimates imported. Regional settings and original geometry correspondence have not been independently verified.'}
            folder=filedialog.askdirectory(title='Save authentic imported comparison report')
            if folder:
                p=Path(folder)/f'manual_comparison_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json';p.write_text(json.dumps(result,indent=2))
            self.dashboard_write(json.dumps(result,indent=2));self.status.set('Mode: Semiautomated • authentic sliced estimates imported; see dashboard.')
        except Exception as exc:self.error(exc)

    def configure_sensor(self):
        try:
            self.recorder.configure(int(self.v['trigger_pin'].get()),int(self.v['echo_pin'].get()),int(self.v['interval_ms'].get()))
            self.sensor_label.set('Configuration sent to sensor firmware. Verify board, pins, echo voltage and reliable range before recording.')
        except Exception as exc:self.error(exc)

    def connect_serial(self):
        try:self.recorder.connect(self.v['port'].get(),self.config_data['sensor_baud']);self.sensor_label.set('Serial connected. Sensor model and reliable range: NOT YET VERIFIED.')
        except Exception as exc:self.error(exc)
    def start_recording(self):
        try:self.recorder.start(self.v['trial_id'].get(),self.v['variant'].get());self.sensor_label.set('Recording sensor measurements. Motor activation is separate.')
        except Exception as exc:self.error(exc)
    def stop_recording(self):
        try:self.recorder.stop();self.readings=list(self.recorder.readings);self.process_sensor()
        except Exception as exc:self.error(exc)
    def upload_sensor(self):
        path=filedialog.askopenfilename(title='Upload sensor readings',filetypes=[('Sensor CSV','*.csv')])
        if not path:return
        try:self.readings=read_csv(path);self.process_sensor()
        except Exception as exc:self.error(exc)
    def demo_sensor(self):
        self.readings=synthetic_readings();self.v['start_mm'].set('750');self.v['end_mm'].set('300');self.process_sensor();self.analyze_sensor()
    def process_sensor(self):
        try:
            low=float(self.v['sensor_min'].get()) if self.v['sensor_min'].get().strip() else None
            high=float(self.v['sensor_max'].get()) if self.v['sensor_max'].get().strip() else None
            self.trials=process_trials(self.readings,low,high,self.smooth.get());self.summary=None
            self.trial_box.configure(values=[f'{t.variant} / {t.trial_id}' for t in self.trials]);self.trial_box.current(0);self.show_trial()
        except Exception as exc:self.error(exc)
    def show_trial(self):
        if not self.trials:return
        trial=self.trials[max(0,self.trial_box.current())]
        self.show_figure('motion',self.motion_container,motion_figure(trial));self.sensor_label.set(trial.source+' • '+trial.processing+' • '+' '.join(trial.warnings))
    def analyze_sensor(self):
        try:
            if not self.readings:raise ValueError('Upload CSV or record readings first.')
            self.process_sensor()
            self.summary=summarize(self.trials,float(self.v['start_mm'].get()),float(self.v['end_mm'].get()),float(self.v['max_gap'].get()),self.v['baseline_variant'].get(),self.paired.get())
            self.dashboard_write('Climb analysis\n'+json.dumps(self.summary,indent=2),append=True)
            self.show_figure('comparison',self.comparison_container,comparison_figure(self.summary));self.sensor_label.set('Analysis complete. '+ ' '.join(self.summary['warnings']))
        except Exception as exc:self.error(exc)
    def save_sensor(self):
        if not self.readings:self.error('No raw readings to save.');return
        path=filedialog.asksaveasfilename(title='Save preserved raw sensor data',defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if path:
            try:save_csv(self.readings,Path(path))
            except Exception as exc:self.error(exc)
    def export_sensor(self):
        if not self.trials:self.error('Analyze sensor readings first.');return
        directory=filedialog.askdirectory(title='Export sensor report and charts')
        if not directory:return
        try:
            folder=Path(directory)/f'AeroSlice-sensor-{datetime.now().strftime("%Y%m%d_%H%M%S")}';folder.mkdir()
            save_csv(self.readings,folder/'sensor_data.csv')
            for i,trial in enumerate(self.trials):motion_figure(trial).savefig(folder/f'trial_{i+1}.png')
            if self.summary:
                (folder/'sensor_report.json').write_text(json.dumps(self.summary,indent=2));comparison_figure(self.summary).savefig(folder/'climb_comparison.png')
            self.sensor_label.set(f'Sensor reports exported to {folder}')
        except Exception as exc:self.error(exc)
    def measured_dashboard(self):
        try:
            a,b=float(self.v['measured_baseline'].get()),float(self.v['measured_adaptive'].get())
            if not all(math.isfinite(v) and v>0 for v in (a,b)):raise ValueError('Measured masses must be positive.')
            result={'baseline_payload_g_measured':a,'adaptive_payload_g_measured':b,'measured_payload_savings_percent':(a-b)/a*100}
            if self.v['fixed_mass'].get().strip():
                fixed=float(self.v['fixed_mass'].get())
                if not math.isfinite(fixed) or fixed<=0:raise ValueError('Fixed hardware mass must be positive.')
                result.update(baseline_total_moving_mass_g_measured=a+fixed,adaptive_total_moving_mass_g_measured=b+fixed)
            self.dashboard_write('User-supplied physical measurements\n'+json.dumps(result,indent=2),append=True)
            if self.report:self.report['measured_masses']=result;write_reports(Path(self.report['run_directory']),self.report)
        except Exception as exc:self.error(exc)
    def calculate_stiffness(self):
        try:
            result=stiffness(float(self.v['force'].get()),float(self.v['deflection'].get()),float(self.v['stiff_mass'].get()),self.v['conditions'].get())
            self.dashboard_write('Independent stiffness measurement\n'+json.dumps(result,indent=2),append=True)
            if self.report:self.report['stiffness']=result;write_reports(Path(self.report['run_directory']),self.report)
        except Exception as exc:self.error(exc)
    def close(self):
        self.cancel.set();self.recorder.disconnect()
        for canvas in self.figures.values():self.dispose_figure(canvas)
        self.figures.clear()
        self.destroy()
        self.v.clear()
        for name in ('status','model_info','sensor_label','manual','smooth','paired'):
            setattr(self,name,None)
