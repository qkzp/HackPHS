"""Three-tab Tkinter interface. Workers communicate through a main-thread queue."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import gc
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
from .ui_components import ScrollPage, responsive_form, Tooltip
from .motion_ui import MotionPanel
from .file_import import load_model
from .structural import Settings, weight_saving_settings, make_regions, DISCLAIMER
from .optimizer import generate_candidates, optimize, write_reports
from .slicer import OrcaSlicer, Profiles, DEFAULT_EXECUTABLE
from .analysis import read_csv, save_csv, process_trials, summarize, stiffness, synthetic_readings
from .sensor import Recorder, ports
from .validation import inspect_gcode
from .visualization import heatmap_figure, motion_figure, comparison_figure

class Application(tk.Tk):
    def __init__(self, examples: Path):
        super().__init__()
        self.title('AeroSlice • Make lighter prints');self.geometry('1440x900');self.minsize(760,640);self.configure(background='#f1f5f9')
        self.examples=examples;self.model=None;self.report=None;self.trials=[];self.readings=[];self.summary=None
        self.print_export_folder=None
        self.busy=False;self.task_description='Comparing designs';self.queue=Queue();self.cancel=Event();self.recorder=Recorder();self.figures={}
        self.config_path=Path.home()/'Library/Application Support/AeroSlice/config.json'
        self.config_data=json.loads((Path(__file__).parent/'config.json').read_text())
        if self.config_path.exists():
            try:
                saved=json.loads(self.config_path.read_text())
                if isinstance(saved,dict):
                    for key,value in saved.items():
                        default=self.config_data.get(key)
                        if key in self.config_data and default is not None:
                            if isinstance(default,str) and not isinstance(value,str):continue
                            if isinstance(default,(int,float)) and (not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0):continue
                        self.config_data[key]=value
            except (OSError,ValueError,TypeError): pass
        modes=('Weight saving','Weight saving + FEA comparison','FEA direct search (experimental)')
        if self.config_data.get('simulation_mode') not in modes:
            self.config_data['simulation_mode']='Weight saving + FEA comparison'
        baseline=15.
        try:
            baseline=OrcaSlicer(self.config_data['orca_executable'],self.config_data['profile_root']).starting_infill(self.config_data['process_profile'])
        except (OSError,ValueError,KeyError): pass
        self.goal_defaults=weight_saving_settings(baseline)
        count=self.config_data.get('candidate_count',self.goal_defaults.candidate_count)
        self.goal_defaults.candidate_count=count if isinstance(count,int) and 3<=count<=20 else self.goal_defaults.candidate_count
        self.config_data['density_set']=list(self.goal_defaults.densities)
        self.v={};self.sections={};self.details={};self.configure_styles()
        header=ttk.Frame(self,padding=(28,12));header.pack(fill='x')
        ttk.Label(header,text='◈  AeroSlice',foreground='#0369a1',font=('Helvetica',24,'bold')).pack(side='left')
        self.navigation=[]
        nav=ttk.Frame(header);nav.pack(side='right')
        for index,title in enumerate(('Optimize','Measurements','Results')):
            button=ttk.Button(nav,text=title,style='Nav.TButton',command=lambda i=index:self.book.select(i))
            button.pack(side='left',padx=4);self.navigation.append(button)
        self.phase=self.variable('phase','01  Add a model')
        self.status=tk.StringVar(value='Start by adding a 3D model, or try the example to see how it works.')
        ttk.Label(self,textvariable=self.status,style='PageHint.TLabel',padding=(28,0,28,8),wraplength=1180).pack(fill='x')
        self.book=book=ttk.Notebook(self,style='Workspace.TNotebook');book.pack(fill='both',expand=True,padx=24,pady=(0,18))
        self.optimize_tab=ttk.Frame(book,padding=4,style='Page.TFrame');self.sensor_tab=ttk.Frame(book,padding=16);self.dashboard_tab=ttk.Frame(book,padding=16)
        for title,tab in [('Make a lighter print',self.optimize_tab),('Test & measure (optional)',self.sensor_tab),('Your results',self.dashboard_tab)]:book.add(tab,text=title)
        self._optimize_ui()
        self.measurement_book=ttk.Notebook(self.sensor_tab);self.measurement_book.pack(fill='both',expand=True)
        simulation_page=ttk.Frame(self.measurement_book,padding=12)
        measurement_page=ttk.Frame(self.measurement_book,padding=12)
        self.measurement_book.add(simulation_page,text='Simulation')
        self.measurement_book.add(measurement_page,text='Measurements & sensors')
        sensor_page=ScrollPage(measurement_page);sensor_page.pack(fill='both',expand=True)
        self.sensor_tab=sensor_page.body
        dashboard_page=ScrollPage(self.dashboard_tab);dashboard_page.pack(fill='both',expand=True)
        self.dashboard_page=self.dashboard_tab;self.dashboard_tab=dashboard_page.body
        self._sensor_ui();self._dashboard_ui()
        self.motion_panel=MotionPanel(simulation_page,self)
        self.motion_panel.pack(fill='both',expand=True)
        for section in self.sections.values():
            if section['body'].grid_slaves():responsive_form(section['body'])
        self.book.bind('<<NotebookTabChanged>>',self.refresh_navigation);self.refresh_navigation()
        self.bind('<Command-o>',lambda _:self.invoke_shortcut(self.upload_button))
        self.bind('<Command-Return>',lambda _:self.invoke_shortcut(self.optimize_button))
        self.bind('<Command-s>',lambda _:self.invoke_shortcut(self.export_button))
        for key in ('orca','printer_profile','process_profile','filament_profile'):
            self.v[key].trace_add('write',lambda *_:self.update_actions())
        self.v['candidate_count'].trace_add('write',lambda *_:self.refresh_search_summary())
        self.refresh_search_summary()
        self.adapt_labels(self)
        self.protocol('WM_DELETE_WINDOW',self.close);self.after(100,self.poll);self.update_actions()

    def configure_styles(self):
        style=ttk.Style(self);style.theme_use('clam')
        self.option_add('*Font',('Helvetica',11))
        style.configure('.',font=('Helvetica',12),background='white',foreground='#0f172a')
        style.configure('TFrame',background='white');style.configure('Page.TFrame',background='#f1f5f9')
        style.configure('TLabel',background='white',foreground='#0f172a')
        style.configure('TLabelframe',background='white',bordercolor='#e2e8f0',relief='solid',borderwidth=1)
        style.configure('TLabelframe.Label',background='white',foreground='#0369a1',font=('Helvetica',12,'bold'))
        style.configure('Card.TFrame',background='white',bordercolor='#e2e8f0',relief='solid',borderwidth=1)
        style.configure('MetricCard.TFrame',background='#e0f2fe',borderwidth=0)
        style.configure('Hint.TLabel',background='white',foreground='#64748b',font=('Helvetica',10))
        style.configure('PageHint.TLabel',background='#f1f5f9',foreground='#64748b',font=('Helvetica',12))
        style.configure('Eyebrow.TLabel',background='#f1f5f9',foreground='#64748b',font=('Helvetica',10,'bold'))
        style.configure('Badge.TLabel',background='#e0f2fe',foreground='#0369a1',font=('Helvetica',10,'bold'))
        style.configure('CardTitle.TLabel',font=('Helvetica',15,'bold'))
        style.configure('Metric.TLabel',background='#e0f2fe',foreground='#0369a1',font=('Helvetica',24,'bold'))
        style.configure('MetricHint.TLabel',background='#e0f2fe',foreground='#075985',font=('Helvetica',10))
        style.configure('Count.TLabel',background='white',foreground='#0f172a',font=('Helvetica',23,'bold'))
        style.configure('TButton',background='white',foreground='#334155',bordercolor='#cbd5e1',lightcolor='white',darkcolor='white',padding=(14,10),font=('Helvetica',12),relief='flat',focusthickness=1,focuscolor='#0284c7')
        style.map('TButton',background=[('disabled','#f1f5f9'),('active','#e0f2fe')],foreground=[('disabled','#94a3b8')])
        style.configure('Accent.TButton',background='#0369a1',foreground='white',bordercolor='#0369a1',font=('Helvetica',12,'bold'))
        style.map('Accent.TButton',background=[('disabled','#cbd5e1'),('active','#075985')],foreground=[('disabled','#64748b'),('!disabled','white')])
        style.configure('Nav.TButton',padding=(18,10),borderwidth=0,font=('Helvetica',12))
        style.configure('ActiveNav.TButton',padding=(18,10),borderwidth=0,background='#e0f2fe',foreground='#0369a1',font=('Helvetica',12,'bold'))
        style.map('ActiveNav.TButton',background=[('active','#bae6fd')])
        style.configure('Section.TButton',anchor='w',padding=(14,12),bordercolor='#e2e8f0',font=('Helvetica',11,'bold'))
        style.configure('TEntry',padding=8,fieldbackground='#f8fafc',bordercolor='#e2e8f0',lightcolor='#e2e8f0',darkcolor='#e2e8f0')
        style.configure('TCombobox',padding=7,fieldbackground='#f8fafc',background='#f8fafc',bordercolor='#e2e8f0',arrowsize=12)
        style.map('TCombobox',fieldbackground=[('readonly','#f8fafc')],selectbackground=[('readonly','#f8fafc')],selectforeground=[('readonly','#0f172a')])
        style.configure('Horizontal.TProgressbar',background='#0284c7',troughcolor='#cbd5e1',borderwidth=0,thickness=4)
        style.configure('Treeview',background='white',fieldbackground='white',rowheight=34,borderwidth=0)
        style.configure('Treeview.Heading',background='#f1f5f9',foreground='#475569',font=('Helvetica',10,'bold'),padding=8,relief='flat')
        style.map('Treeview',background=[('selected','#e0f2fe')],foreground=[('selected','#0369a1')])
        style.configure('Workspace.TNotebook',background='#f1f5f9',borderwidth=0,tabmargins=0)
        style.layout('Workspace.TNotebook.Tab',[])

    def refresh_navigation(self,event=None):
        selected=self.book.index(self.book.select())
        for index,button in enumerate(self.navigation):button.configure(style='ActiveNav.TButton' if index==selected else 'Nav.TButton')

    @staticmethod
    def invoke_shortcut(button):
        if str(button['state'])!='disabled':button.invoke()
        return 'break'

    def card(self,parent,title,padding=16):
        card=ttk.Frame(parent,style='Card.TFrame',padding=padding)
        ttk.Label(card,text=title,style='CardTitle.TLabel').pack(anchor='w',pady=(0,8))
        return card

    def variable(self,key,value):
        self.v[key]=tk.StringVar(value=str(value));return self.v[key]

    def apply_retention_slider(self,value):
        if not self.syncing_stiffness:self.v['fea_retention'].set(str(round(float(value))))

    def refresh_retention_target(self):
        try:value=float(self.v['fea_retention'].get())
        except ValueError:value=float('nan')
        if math.isfinite(value) and 0<value<=100:
            self.syncing_stiffness=True
            try:self.retention_scale.set(value)
            finally:self.syncing_stiffness=False
            self.v['target_summary'].set(f'Allow up to {100-value:g}% predicted stiffness loss. Applies to FEA comparison.')
        else:self.v['target_summary'].set('Enter a stiffness target greater than 0 and up to 100%.')

    def set_preview_view(self,elevation,azimuth):
        canvas=self.figures.get('preview')
        if canvas:
            canvas.figure.axes[0].view_init(elev=elevation,azim=azimuth);canvas.draw_idle()

    def build_metrics(self,parent):
        metrics=ttk.Frame(parent);metrics.pack(fill='x',pady=(8,4))
        cards=[]
        for key,label in (('before','Baseline mass'),('after','Optimized mass'),('saving','Material saved'),('stiffness','Stiffness retained')):
            card=ttk.Frame(metrics,style='MetricCard.TFrame',padding=12);cards.append(card)
            ttk.Label(card,text=label,style='MetricHint.TLabel',wraplength=110).pack(anchor='w')
            value_label=ttk.Label(card,textvariable=self.metric_vars[key],style='Metric.TLabel')
            value_label.pack(anchor='w',pady=(8,4))
            self.metric_vars[key].trace_add('write',lambda *_,label=value_label,var=self.metric_vars[key]:label.configure(font=('Helvetica',12 if var.get()=='Not assessed' else 24,'bold')))
            ttk.Label(card,text='FEA prediction' if key=='stiffness' else 'Slicer estimate',style='MetricHint.TLabel').pack(anchor='w')
        state=[None]
        def arrange(event):
            columns=4 if event.width>=560 else 2
            if state[0]==columns:return
            state[0]=columns
            for col in range(4):metrics.columnconfigure(col,weight=1 if col<columns else 0,uniform='metrics' if col<columns else '')
            for i,card in enumerate(cards):card.grid(row=i//columns,column=i%columns,sticky='nsew',padx=4,pady=4)
        metrics.bind('<Configure>',arrange)
        return cards

    def arrange_workspace(self,event):
        narrow=event.width<1060
        if getattr(self,'stacked_workspace',None)!=narrow:
            self.stacked_workspace=narrow
            self.workspace.columnconfigure(0,weight=1 if narrow else 44,uniform='' if narrow else 'workspace')
            self.workspace.columnconfigure(1,weight=0 if narrow else 56,uniform='' if narrow else 'workspace')
            self.settings_column.grid_configure(row=0,column=0,padx=(0,0) if narrow else (0,16))
            self.visual_column.grid_configure(row=1 if narrow else 0,column=0 if narrow else 1,pady=(16,0) if narrow else 0)
        self.optimize_page.fixed_viewport=not narrow
        self.settings_scroll.expand_content=narrow
        self.settings_scroll.refresh()
        self.optimize_page.refresh()

    def fit_preview(self):
        canvas=self.figures.get('preview')
        if not canvas or self.model is None:return
        ax=canvas.figure.axes[0]
        centers=self.model.bounds.mean(axis=0);spans=self.model.dimensions*1.12
        ax.set(xlim=(centers[0]-spans[0]/2,centers[0]+spans[0]/2),
               ylim=(centers[1]-spans[1]/2,centers[1]+spans[1]/2),
               zlim=(centers[2]-spans[2]/2,centers[2]+spans[2]/2))
        ax.set_box_aspect(self.model.dimensions,zoom=1.12)
        ax.view_init(elev=25,azim=-40);canvas.draw_idle()

    def adapt_labels(self,parent):
        for child in parent.winfo_children():
            if isinstance(child,ttk.Label) and float(child.cget('wraplength') or 0)>0:
                def wrap(event,label=child,container=parent):
                    # Toplevel bindings also receive descendant events via bindtags.
                    if event.widget is not container:return
                    width=max(100,event.width-(56 if container is self else 32))
                    if int(float(label.cget('wraplength')))!=width:label.configure(wraplength=width)
                parent.bind('<Configure>',wrap,add='+')
            self.adapt_labels(child)

    def zoom_preview(self,event):
        canvas=self.figures.get('preview')
        if canvas and event.inaxes is canvas.figure.axes[0]:
            axis=canvas.figure.axes[0];factor=.9 if event.button=='up' else 1/.9
            current=np.array([axis.get_xlim(),axis.get_ylim(),axis.get_zlim()])
            centers=current.mean(axis=1);spans=np.diff(current,axis=1).ravel()*factor
            if self.model is not None and np.all(spans>=self.model.dimensions*.15) and np.all(spans<=self.model.dimensions*8):
                axis.set(xlim=(centers[0]-spans[0]/2,centers[0]+spans[0]/2),ylim=(centers[1]-spans[1]/2,centers[1]+spans[1]/2),zlim=(centers[2]-spans[2]/2,centers[2]+spans[2]/2));canvas.draw_idle()

    def field(self,parent,key,label,default,row,column=0,width=13,choices=None):
        ttk.Label(parent,text=label).grid(row=row,column=column*2,sticky='w',padx=5,pady=4)
        var=self.variable(key,default)
        widget=ttk.Combobox(parent,textvariable=var,values=choices,state='readonly',width=width) if choices is not None else ttk.Entry(parent,textvariable=var,width=width)
        widget.grid(row=row,column=column*2+1,sticky='ew',padx=5,pady=4);return widget

    def _optimize_ui(self):
        self.action_footer=ttk.Frame(self.optimize_tab,padding=(16,10))
        self.action_footer.pack(side='bottom',fill='x',pady=(8,0))
        self.action_footer.columnconfigure(0,weight=44,uniform='footer')
        self.action_footer.columnconfigure(1,weight=56,uniform='footer')
        action_area=ttk.Frame(self.action_footer);action_area.grid(row=0,column=0,sticky='nsew',padx=(0,16))
        file_footer=ttk.Frame(self.action_footer);file_footer.grid(row=0,column=1,sticky='nsew')
        self.optimize_page=ScrollPage(self.optimize_tab)
        self.optimize_page.pack(fill='both',expand=True)
        self.controls_canvas=self.optimize_page.canvas
        self.workspace=self.optimize_page.body
        self.settings_scroll=ScrollPage(self.workspace)
        self.settings_column=self.settings_scroll
        left=self.settings_scroll.body
        self.visual_column=right=ttk.Frame(self.workspace,style='Page.TFrame')
        self.settings_column.grid(row=0,column=0,sticky='nsew',padx=(0,16))
        right.grid(row=0,column=1,sticky='nsew')
        self.workspace.columnconfigure(0,weight=44,uniform='workspace')
        self.workspace.columnconfigure(1,weight=56,uniform='workspace')
        self.workspace.rowconfigure(0,weight=1)
        self.workspace.bind('<Configure>',self.arrange_workspace)
        upload=self.card(left,'01  Your model');upload.pack(fill='x',pady=(0,14))
        ttk.Label(upload,text='Start with your STL. We optimize its internal fill and keep its shape.',wraplength=490,style='Hint.TLabel').pack(anchor='w',pady=(0,14))
        row=ttk.Frame(upload);row.pack(fill='x')
        self.upload_button=ttk.Button(row,text='Upload Model…',style='Accent.TButton',command=self.upload_model);self.upload_button.pack(side='left')
        ttk.Button(row,text='Try an example',command=self.demo_model).pack(side='left',padx=8)
        self.model_info=self.variable('model_info','STL and 3MF files supported. No model added yet.')
        self.model_label=ttk.Label(upload,textvariable=self.model_info,wraplength=490)
        self.model_label.pack(anchor='w',pady=(12,0))
        Tooltip(self.model_label,lambda:str(self.model.path) if self.model else 'Upload an STL or compatible 3MF model.')
        setup=self.card(left,'02  Print setup & goals');setup.pack(fill='x',pady=(0,14))
        self.setup_summary=self.variable('setup_summary','')
        ttk.Label(setup,textvariable=self.setup_summary,wraplength=490,font=('Helvetica',11,'bold')).pack(anchor='w')
        material_row=ttk.Frame(setup);material_row.pack(fill='x',pady=(0,12))
        ttk.Label(material_row,text='Material on your spool').pack(side='left',padx=(0,12))
        self.material_choice=self.variable('material_choice','')
        self.material_box=ttk.Combobox(material_row,textvariable=self.material_choice,state='readonly',width=25)
        self.material_box.pack(side='left',fill='x',expand=True);self.material_box.bind('<<ComboboxSelected>>',self.apply_material_choice)
        self.material_profiles={}
        Tooltip(self.material_box,lambda:self.v['filament_profile'].get())
        search=ttk.Frame(setup);search.pack(fill='x')
        ttk.Label(search,text='How many designs?').pack(side='left',padx=(0,12))
        self.search_choice=self.variable('search_choice','Standard · 8 designs')
        self.search_box=ttk.Combobox(search,textvariable=self.search_choice,state='readonly',width=25,values=['Quick · 3 designs','Standard · 8 designs','Detailed · 12 designs'])
        self.search_box.pack(side='left',fill='x',expand=True);self.search_box.bind('<<ComboboxSelected>>',self.apply_search_choice)
        self.search_help=self.variable('search_help','More designs give a wider comparison and take longer. This does not certify strength.')
        search_help_label=ttk.Label(setup,textvariable=self.search_help,wraplength=490,style='Hint.TLabel');search_help_label.pack(anchor='w',pady=(8,12))
        method=ttk.Frame(setup);method.pack(fill='x',pady=(0,10))
        ttk.Label(method,text='Optimization method').pack(anchor='w',pady=(0,6))
        self.variable('simulation_mode',self.config_data.get('simulation_mode','Weight saving + FEA comparison'))
        self.simulation_box=ttk.Combobox(method,textvariable=self.v['simulation_mode'],state='readonly',width=36,
            values=['Weight saving','Weight saving + FEA comparison','FEA direct search (experimental)'])
        self.simulation_box.pack(fill='x',expand=True)
        self.simulation_box.bind('<<ComboboxSelected>>',lambda event:self.update_actions())
        target=ttk.Frame(setup);target.pack(fill='x',pady=(0,10))
        self.stiffness_target_entry=self.field(target,'fea_retention','Minimum stiffness retained (%)',self.config_data.get('stiffness_retention_percent',95),0,width=6)
        target.columnconfigure(0,weight=1)
        extra_target=self.disclosure(setup,'retention_options','Stiffness target presets & slider')
        self.retention_scale=ttk.Scale(extra_target,from_=50,to=100,command=self.apply_retention_slider)
        self.retention_scale.pack(fill='x',padx=5,pady=(4,8))
        self.variable('target_summary','')
        ttk.Label(target,textvariable=self.v['target_summary'],wraplength=490,style='Hint.TLabel').grid(row=2,column=0,columnspan=2,sticky='w',padx=5,pady=4)
        presets=ttk.Frame(extra_target);presets.pack(fill='x',pady=(4,0))
        self.retention_presets=[]
        for value,label in ((95,'95% Balanced'),(98,'98% Strict'),(100,'100% Full')):
            button=ttk.Button(presets,text=label,command=lambda v=value:self.v['fea_retention'].set(str(v)))
            button.pack(side='left',fill='x',expand=True,padx=(0,4));self.retention_presets.append(button)
        self.syncing_stiffness=False
        self.v['fea_retention'].trace_add('write',lambda *_:self.refresh_retention_target())
        self.refresh_retention_target()
        search.pack_forget();search_help_label.pack_forget()
        search.pack(fill='x',pady=(10,0));search_help_label.pack(anchor='w',pady=(8,12))
        settings=self.disclosure(left,'optimization','Advanced: infill & engineering settings')
        ttk.Label(settings,text='Defaults assume the object rests on its lower face. Change these for a different orientation or support arrangement. These are engineering assumptions, not a strength guarantee.',wraplength=490,style='Hint.TLabel').grid(row=0,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        defaults=self.goal_defaults
        fields=[('baseline','Starting infill (%)',defaults.baseline),('minimum','Lowest allowed (%)',defaults.minimum),('maximum','Highest allowed (%)',defaults.maximum),('candidate_count','Designs to compare',defaults.candidate_count),('coverage','Demand coverage (0–1)',defaults.coverage),('acceleration','Acceleration (m/s²)',0)]
        for i,(key,label,val) in enumerate(fields):self.field(settings,key,label,val,1+i//2,i%2)
        self.field(settings,'contact_axis','Support / region axis',self.config_data.get('contact_axis','z'),4,0,choices=['x','y','z'])
        self.field(settings,'contact_face','Support face',self.config_data.get('contact_face','min'),4,1,choices=['min','max'])
        self.field(settings,'load_axis','Load direction','z',5,0,choices=['x','y','z'])
        self.field(settings,'protected','Region minimums (%)',','.join(f'{d:g}' for d in defaults.protected_minima),5,1)
        self.field(settings,'rotation','Rotation X,Y,Z (°)','0,0,0',6,0)
        self.field(settings,'demand','Custom demand (0–1)','',6,1)
        self.field(settings,'fixed_mass','Other moving mass (g)','',7,0)
        ttk.Label(settings,text=DISCLAIMER+' Blank custom demand uses the load-path heuristic; unknown hardware mass can stay blank.',wraplength=490,style='Hint.TLabel').grid(row=8,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        ttk.Label(settings,text='Weight-saving preset: starting infill follows the selected profile at launch; search up to 12 designs with lower infill. Match your Orca settings here when comparing a custom print.',wraplength=490,style='Hint.TLabel').grid(row=9,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        simulation=self.disclosure(left,'simulation','Simulation: material, loads & limits')
        ttk.Label(simulation,text='Weight saving + FEA comparison uses a normalized reference load to compare stiffness with your original design; no thrust value is needed. The full support face is clamped. Material defaults are demonstration assumptions. Actual force and strength limits apply only to the advanced FEA direct search mode.',wraplength=490,style='Hint.TLabel').grid(row=0,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        fields=[('fea_force_axis','Stiffness test / force axis',self.config_data.get('fea_force_axis','z')),
                ('fea_e_xy','XY modulus (MPa)',2000),('fea_e_z','Z modulus (MPa)',1000),
                ('fea_g_xz','XZ/YZ shear modulus',400),('fea_nu_xy','XY Poisson ratio',.3),
                ('fea_nu_xz','XZ Poisson ratio',.25),('fea_e_power','Stiffness density power',2),
                ('fea_mesh','Coarse mesh cells',9),('fea_tolerance','Mesh tolerance (0–0.5)',.2)]
        for i,(key,label,value) in enumerate(fields):
            self.field(simulation,key,label,value,1+i//2,i%2,choices=['x','y','z'] if key=='fea_force_axis' else None)
        self.field(simulation,'fea_source','Material data source','DEMONSTRATION assumptions; uncalibrated',6,width=31)
        ttk.Label(simulation,text='The default 95% target allows up to 5% predicted stiffness loss; it is not a 5% simulation-error guarantee or a validated strength threshold. All density combinations within regional bounds are simulated on two meshes; design count controls the slicing shortlist. Optimization preserves the original outer shape. No buckling, fatigue or impact analysis.',wraplength=490,style='Hint.TLabel').grid(row=7,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        actual_container=ttk.Frame(simulation);actual_container.grid(row=8,column=0,columnspan=4,sticky='ew')
        actual=self.disclosure(actual_container,'simulation_absolute','Actual-load assessment (advanced)')
        absolute_fields=[('fea_force','Total signed force (N)',''),('fea_strength_xy','XY strength input (MPa)',25),
                         ('fea_strength_z','Z strength input (MPa)',12),('fea_shear','Shear strength (MPa)',8),
                         ('fea_s_power','Strength density power',1.5),('fea_factor','Screening safety factor',2),
                         ('fea_deflection','Max displacement (mm)',.5)]
        for i,(key,label,value) in enumerate(absolute_fields):self.field(actual,key,label,value,i//2,i%2)
        ttk.Label(actual,text='These inputs apply only to the advanced FEA direct search mode. Weight saving and relative-stiffness comparison do not require actual force or these strength limits.',wraplength=490,style='Hint.TLabel').grid(row=4,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        integration=self.disclosure(left,'printer','Printer setup & troubleshooting')
        ttk.Label(integration,text='OrcaSlicer turns a model into printer instructions. The installed Adventurer 5M setup is preselected. Check that its nozzle and material match your printer.',wraplength=490,style='Hint.TLabel').grid(row=0,column=0,columnspan=3,sticky='w',padx=5,pady=8)
        self.field(integration,'orca','OrcaSlicer program',self.config_data['orca_executable'],1,width=34)
        ttk.Button(integration,text='Browse…',command=self.choose_executable).grid(row=1,column=2)
        self.field(integration,'profile_root','Printer profiles folder',self.config_data['profile_root'],2,width=34)
        ttk.Button(integration,text='Browse…',command=self.choose_profiles_root).grid(row=2,column=2)
        for row,key,title in [(3,'printer_profile','Printer / nozzle'),(4,'process_profile','Print quality profile'),(5,'filament_profile','Material profile')]:
            self.field(integration,key,title,self.config_data[key],row,width=34)
            ttk.Button(integration,text='Choose file…',command=lambda k=key:self.choose_profile(k)).grid(row=row,column=2)
        self.manual=tk.BooleanVar(value=False)
        ttk.Checkbutton(integration,text='Prepare suggestions for manual slicing instead',variable=self.manual,command=self.update_actions).grid(row=6,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Label(integration,text='Manual mode provides instructions; it does not produce ready-to-print files.',wraplength=490,style='Hint.TLabel').grid(row=7,column=0,columnspan=3,sticky='w',padx=5)
        tools=ttk.Frame(integration);tools.grid(row=8,column=0,columnspan=3,sticky='ew',pady=8)
        self.check_button=ttk.Button(tools,text='Check my printer setup',command=self.check_compatibility);self.check_button.pack(side='left')
        self.generate_button=ttk.Button(tools,text='Preview design options',command=self.generate);self.generate_button.pack(side='left',padx=5)
        save=ttk.Frame(integration);save.grid(row=9,column=0,columnspan=3,sticky='ew',pady=8)
        ttk.Label(save,text='Save working files in').pack(anchor='w')
        self.variable('output',self.config_data.get('output_folder',str(Path.cwd()/'outputs')))
        ttk.Entry(save,textvariable=self.v['output'],width=42).pack(side='left',fill='x',expand=True,pady=5)
        ttk.Button(save,text='Choose folder…',command=self.choose_output).pack(side='left',padx=(8,0))
        actions=ttk.Frame(action_area);actions.pack(fill='x',pady=4)
        self.next_step=self.variable('next_step','Next: add a model to unlock the comparison.')
        ttk.Label(actions,textvariable=self.next_step,wraplength=500,style='Hint.TLabel').grid(row=0,column=0,columnspan=2,sticky='w',pady=(2,8))
        self.optimize_button=ttk.Button(actions,text='Optimize Model',style='Accent.TButton',command=self.start_optimization);self.optimize_button.grid(row=1,column=0,padx=(0,5),sticky='ew')
        self.cancel_button=ttk.Button(actions,text='Stop',command=self.cancel.set);self.cancel_button.grid(row=1,column=1,sticky='ew')
        actions.columnconfigure(0,weight=1)
        self.progress_bar=ttk.Progressbar(action_area,mode='indeterminate');self.progress_bar.pack(fill='x',pady=5);self.progress_bar.pack_forget()
        Tooltip(self.optimize_button,'Optimize Model · ⌘Return\nUpload model · ⌘O   Save G-code · ⌘S')
        results=ttk.Frame(right,style='Card.TFrame',padding=12);results.pack(fill='x',pady=(0,14))
        self.metric_vars={key:self.variable('metric_'+key,'—') for key in ('before','after','saving','stiffness')}
        self.metric_cards=self.build_metrics(results)
        self.result_hint=self.variable('result_hint','Estimates appear here after optimization.')
        ttk.Label(results,textvariable=self.result_hint,wraplength=510,style='Hint.TLabel').pack(anchor='w',pady=(8,0))
        preview=self.card(right,'Model preview',padding=14);preview.pack(fill='both',expand=True)
        preview.configure(height=360)
        preview_toolbar=ttk.Frame(preview);preview_toolbar.pack(fill='x')
        ttk.Label(preview_toolbar,text='Drag to rotate · scroll to zoom',style='Hint.TLabel').pack(side='left')
        self.view_buttons=[]
        for name,angles in (('3D',(25,-40)),('Top',(90,-90)),('Front',(0,-90)),('Fit',(25,-40))):
            button=ttk.Button(preview_toolbar,text=name,padding=(10,5),command=self.fit_preview if name=='Fit' else lambda a=angles:self.set_preview_view(*a))
            button.pack(side='right',padx=3);self.view_buttons.append(button)
        self.preview_container=ttk.Frame(preview,height=280);self.preview_container.pack(fill='both',expand=True)
        self.preview_container.pack_propagate(False)
        self.preview_empty=ttk.Label(self.preview_container,text='YOUR MODEL WILL APPEAR HERE\n\nAdd an STL or try the example to explore.',anchor='center',justify='center',style='Hint.TLabel');self.preview_empty.pack(fill='both',expand=True,pady=25)
        detail=self.disclosure(left,'designs','Details: compare all designs')
        self.tree=ttk.Treeview(detail,columns=('design','mass','time','status'),show='headings',height=5)
        for key,label,width in [('design','Design',180),('mass','Material (g)',90),('time','Print time',100),('status','File checks',100)]:self.tree.heading(key,text=label);self.tree.column(key,width=width)
        self.tree.tag_configure('selected',background='#e0f2fe');self.tree.tag_configure('failed',foreground='#9f1239')
        self.tree.pack(fill='both',expand=True)
        self.tree.bind('<Configure>',lambda e:[self.tree.column(key,width=max(50,int((e.width-4)*fraction)),minwidth=50) for key,fraction in zip(('design','mass','time','status'),(.36,.19,.22,.23))])
        ttk.Label(detail,text='Mass and time come from OrcaSlicer estimates. Passing file checks does not prove strength.',wraplength=510,style='Hint.TLabel').pack(anchor='w',pady=5)
        ttk.Button(detail,text='Import a manually sliced comparison…',command=self.import_slices).pack(anchor='w',pady=3)
        ttk.Label(file_footer,text='Print files',style='Hint.TLabel').pack(anchor='w',pady=(2,8))
        export=ttk.Frame(file_footer);export.pack(fill='x')
        self.export_button=ttk.Button(export,text='Save Optimized G-code…',style='Accent.TButton',command=self.export_files);self.export_button.pack(side='left',fill='x',expand=True)
        self.open_button=ttk.Button(export,text='Open working folder',command=self.open_output);self.open_button.pack(side='left',padx=(8,0))
        self.export_status=self.variable('export_status','No optimized G-code generated yet.')
        ttk.Label(file_footer,textvariable=self.export_status,style='Hint.TLabel').pack(anchor='w',pady=(8,0))

    def disclosure(self,parent,key,title):
        outer=ttk.Frame(parent);outer.pack(fill='x',pady=4)
        button=ttk.Button(outer,text='▸  '+title,style='Section.TButton',command=lambda:self.toggle_section(key))
        button.pack(fill='x')
        body=ttk.Frame(outer,padding=(8,6))
        self.sections[key]={'button':button,'body':body,'title':title,'open':False}
        return body

    def toggle_section(self,key):
        section=self.sections[key];section['open']=not section['open']
        if section['open']:section['body'].pack(fill='x')
        else:section['body'].pack_forget()
        section['button'].configure(text=('▾  ' if section['open'] else '▸  ')+section['title'])

    def apply_search_choice(self,event=None):
        count={'Quick · 3 designs':3,'Standard · 8 designs':8,'Detailed · 12 designs':12}[self.search_choice.get()]
        self.v['candidate_count'].set(str(count))

    def refresh_search_summary(self):
        count=self.v['candidate_count'].get()
        labels={'3':'Quick · 3 designs','8':'Standard · 8 designs','12':'Detailed · 12 designs'}
        self.search_choice.set(labels.get(count,f'Custom · {count} designs'))
        self.search_help.set(f'Compare up to {count} designs. More designs take longer; this choice does not certify strength.')

    def refresh_setup_summary(self):
        printer=self.v['printer_profile'].get();material=self.v['filament_profile'].get()
        self.refresh_material_choices()
        display=Path(printer).stem if printer.endswith('.json') else printer
        self.setup_summary.set(display+' · '+self.material_choice.get().split(' · ')[0])


    def refresh_material_choices(self):
        executable=Path(self.v['orca'].get())
        root=Path(self.v['profile_root'].get()) if self.v['profile_root'].get().strip() else executable.parent.parent/'Resources/profiles'
        choices={}
        # These are available installed templates, not physical-material verification.
        # Orca profile compatibility is checked by the optimizer before slicing.
        for material in ('PLA','PETG'):
            path=root/'Flashforge/filament'/f'Flashforge Generic {material}.json'
            try:
                profile=json.loads(path.read_text())
                if profile.get('type')=='filament' and profile.get('name'):
                    choices[material+' · standard profile']=profile['name']
            except (OSError,ValueError):continue
        current=self.v['filament_profile'].get()
        label=next((label for label,name in choices.items() if name==current),None)
        if label is None:
            label='Custom · '+(Path(current).stem if current.endswith('.json') else current)
            choices[label]=current
        self.material_profiles=choices;self.material_box.configure(values=list(choices))
        self.material_choice.set(label)

    def apply_material_choice(self,event=None):
        selected=self.material_profiles.get(self.material_choice.get())
        if selected:self.v['filament_profile'].set(selected)

    def _sensor_ui(self):
        ttk.Label(self.sensor_tab,text='Does your lighter print climb faster?',font=('Helvetica',18,'bold')).pack(anchor='w',pady=(0,8))
        ttk.Label(self.sensor_tab,text='Optional: compare readings from the vertical rig. You can import a CSV without connecting hardware, or explore a clearly labeled simulation.',wraplength=1080,style='Hint.TLabel').pack(anchor='w')
        actions=ttk.Frame(self.sensor_tab);actions.pack(fill='x',pady=10)
        ttk.Button(actions,text='Import test readings (CSV)…',style='Accent.TButton',command=self.upload_sensor).pack(side='left')
        ttk.Button(actions,text='Try simulated readings',command=self.demo_sensor).pack(side='left',padx=8)
        self.trial_box=ttk.Combobox(actions,state='readonly',width=24);self.trial_box.pack(side='right');self.trial_box.bind('<<ComboboxSelected>>',lambda _:self.show_trial())
        ttk.Label(actions,text='View test:').pack(side='right',padx=8)
        controls=ttk.LabelFrame(self.sensor_tab,text='Compare the same climb distance',padding=8);controls.pack(fill='x')
        ttk.Label(controls,text='The sensor is above the carriage, so its distance gets smaller as the carriage rises. Enter the same start and finish distances for every design.',wraplength=1040,style='Hint.TLabel').grid(row=0,column=0,columnspan=6,sticky='w',pady=5)
        self.field(controls,'start_mm','Start distance (mm)','',1,0)
        self.field(controls,'end_mm','Finish distance (mm)','',1,1)
        self.baseline_variant_box=self.field(controls,'baseline_variant','Original print label','baseline',1,2,choices=['baseline'])
        ttk.Button(controls,text='Compare climb times',command=self.analyze_sensor).grid(row=1,column=6,padx=8)
        advanced=self.disclosure(self.sensor_tab,'analysis','More analysis settings')
        self.field(advanced,'max_gap','Largest allowed gap (s)',.5,0,0)
        self.field(advanced,'sensor_min','Verified sensor min (mm)','',0,1)
        self.field(advanced,'sensor_max','Verified sensor max (mm)','',0,2)
        self.smooth=tk.BooleanVar(value=False);self.paired=tk.BooleanVar(value=False)
        ttk.Checkbutton(advanced,text='Smooth readings with a five-sample median (raw data retained)',variable=self.smooth).grid(row=1,column=0,columnspan=3,sticky='w')
        ttk.Checkbutton(advanced,text='Pair tests with matching trial IDs',variable=self.paired).grid(row=1,column=3,columnspan=3,sticky='w')
        live=self.disclosure(self.sensor_tab,'live','Connect an ESP32 for live recording')
        self.port_box=self.field(live,'port','Serial port','',0,0,width=25,choices=ports())
        ttk.Button(live,text='Refresh ports',command=self.refresh_ports).grid(row=0,column=2)
        ttk.Button(live,text='Connect',command=self.connect_serial).grid(row=0,column=3)
        ttk.Button(live,text='Disconnect',command=self.recorder.disconnect).grid(row=0,column=4)
        self.field(live,'trial_id','Trial ID','1',1,0)
        self.field(live,'variant','Design label','baseline',1,1)
        ttk.Button(live,text='Start recording',command=self.start_recording).grid(row=1,column=4)
        ttk.Button(live,text='Stop & view readings',command=self.stop_recording).grid(row=1,column=5)
        self.field(live,'trigger_pin','Example trigger GPIO',25,2,0)
        self.field(live,'echo_pin','Example echo GPIO',26,2,1)
        self.field(live,'interval_ms','Interval (ms)',self.config_data['sensor_interval_ms'],2,2)
        ttk.Button(live,text='Configure sensor',command=self.configure_sensor).grid(row=2,column=6)
        ttk.Label(live,text='Verify the actual sensor, wiring and GPIOs first. This app records measurements only; motor activation is separate.',wraplength=1050,style='Hint.TLabel').grid(row=3,column=0,columnspan=7,sticky='w',pady=5)
        responsive_form(controls,breakpoint=1200)
        self.sensor_label=self.variable('sensor_label','No readings yet. Start with a CSV or the simulated example.')
        ttk.Label(self.sensor_tab,textvariable=self.sensor_label,wraplength=1100,style='Hint.TLabel').pack(fill='x',pady=6)
        self.motion_container=ttk.Frame(self.sensor_tab,height=330);self.motion_container.pack(fill='both',expand=True,pady=4)
        footer=ttk.Frame(self.sensor_tab);footer.pack(fill='x')
        ttk.Button(footer,text='Save original readings…',command=self.save_sensor).pack(side='left')
        ttk.Button(footer,text='Save report & charts…',command=self.export_sensor).pack(side='left',padx=8)

    def _dashboard_ui(self):
        top=ttk.Frame(self.dashboard_tab);top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='Your results, explained',font=('Helvetica',22,'bold')).pack(side='left')
        ttk.Button(top,text='View technical details…',command=self.view_details).pack(side='right')
        ttk.Label(self.dashboard_tab,text='Print estimates, real measurements and simulated examples are labeled separately.',style='Hint.TLabel').pack(anchor='w')
        self.build_metrics(self.dashboard_tab)
        self.dashboard_text=tk.Text(self.dashboard_tab,height=13,wrap='word',font=('Helvetica',12),bg='#f8fafc',fg='#334155',padx=24,pady=20,relief='flat',spacing1=3,spacing3=3,highlightthickness=1,highlightbackground='#e2e8f0')
        self.dashboard_text.tag_configure('heading',font=('Helvetica',12,'bold'),foreground='#0369a1',spacing1=12,spacing3=8)
        self.dashboard_text.pack(fill='both',expand=True,pady=10)
        self.comparison_container=ttk.Frame(self.dashboard_tab);self.comparison_container.pack(fill='both',expand=True)
        box=self.disclosure(self.dashboard_tab,'measurements','Add measurements from your real print')
        self.field(box,'measured_baseline','Original object (g)','',0,0)
        self.field(box,'measured_adaptive','New object (g)','',0,1)
        ttk.Button(box,text='Compare weighed objects',command=self.measured_dashboard).grid(row=0,column=4)
        ttk.Label(box,text='Use actual scale readings. Estimated print weight can differ from measured weight.',style='Hint.TLabel').grid(row=1,column=0,columnspan=5,sticky='w',padx=5,pady=6)
        stiffness_box=self.disclosure(self.dashboard_tab,'stiffness','Add a separate stiffness test (advanced)')
        self.field(stiffness_box,'force','Measured force (N)','',0,0)
        self.field(stiffness_box,'deflection','Measured bending (mm)','',0,1)
        self.field(stiffness_box,'stiff_mass','Specimen mass (g)','',1,0)
        self.field(stiffness_box,'conditions','How it was supported / loaded','',1,1,width=28)
        ttk.Button(stiffness_box,text='Calculate stiffness',command=self.calculate_stiffness).grid(row=1,column=4)
        self.dashboard_write('Start with a model in “Optimize”.\n\nAeroSlice will compare estimated material use and print time, then prepare the selected files for OrcaSlicer.\n\nYou can also import rig measurements in “Measurements” to compare climb times. A faster climb does not prove a stronger print.\n\nStrength has not been independently verified. Hardware measurements are optional and start empty.')

    def view_details(self):
        window=tk.Toplevel(self);window.title('AeroSlice • Technical details');window.geometry('850x600')
        text=tk.Text(window,wrap='word',font=('Menlo',10));text.pack(fill='both',expand=True)
        data={'optimization':self.report,'other_results':self.details}
        text.insert('end',json.dumps(data,indent=2,default=str) if self.report or self.details else 'No results yet. Run a comparison or import measurements first.')
        text.configure(state='disabled')
        ttk.Button(window,text='Close',command=window.destroy).pack(pady=8)

    def dashboard_write(self,text,append=False,section=None):
        self.dashboard_text.configure(state='normal')
        if not append:self.dashboard_text.delete('1.0','end')
        for line in text.splitlines():
            tags=('heading',) if line.isupper() and len(line)<100 else ()
            if section:tags+= (section,)
            self.dashboard_text.insert('end',line+'\n',tags)
        self.dashboard_text.configure(state='disabled')

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
        label={'rotation':'Rotation X,Y,Z','protected':'Region minimums','demand':'Custom demand'}.get(key,key)
        try:values=tuple(float(v.strip()) for v in self.v[key].get().split(','))
        except ValueError as exc:raise ValueError(f'{label}: enter three numbers separated by commas in Advanced settings.') from exc
        if len(values)!=3:raise ValueError(f'{label}: enter three numbers separated by commas in Advanced settings.')
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

    def fea_settings(self):
        mode=self.v['simulation_mode'].get()
        if mode=='Weight saving':return None
        relative=mode=='Weight saving + FEA comparison'
        from .fea import FEASettings
        if not relative and not self.v['fea_force'].get().strip():raise ValueError('Enter total force for advanced FEA, or choose “Weight saving + FEA comparison” to compare designs without a thrust value.')
        values={name:float(self.v[key].get()) for name,key in [
            ('e_xy_mpa','fea_e_xy'),('e_z_mpa','fea_e_z'),('g_xz_mpa','fea_g_xz'),
            ('nu_xy','fea_nu_xy'),('nu_xz','fea_nu_xz'),('strength_xy_mpa','fea_strength_xy'),
            ('strength_z_mpa','fea_strength_z'),('shear_mpa','fea_shear'),('stiffness_power','fea_e_power'),
            ('strength_power','fea_s_power'),('safety_factor','fea_factor'),('max_displacement_mm','fea_deflection'),('mesh_tolerance','fea_tolerance')]}
        s=FEASettings(**values,force_n=1. if relative else float(self.v['fea_force'].get()),
                      assessment='relative_stiffness' if relative else 'absolute_limits',minimum_stiffness_ratio=float(self.v['fea_retention'].get())/100,
                      force_axis=self.v['fea_force_axis'].get(),mesh_cells=int(self.v['fea_mesh'].get()),material_source=self.v['fea_source'].get())
        s.validate();return s
    def slicer(self):return OrcaSlicer(self.v['orca'].get(),self.v['profile_root'].get(),self.config_data['slicer_timeout_s'],self.cancel)

    def update_actions(self):
        loaded=self.model is not None and not self.busy
        self.upload_button.configure(state='disabled' if self.busy else 'normal',text='Replace model…' if self.model else 'Upload Model…')
        self.search_box.configure(state='disabled' if self.busy else 'readonly')
        self.material_box.configure(state='disabled' if self.busy else 'readonly')
        self.simulation_box.configure(state='disabled' if self.busy else 'readonly')
        self.stiffness_target_entry.configure(state='normal' if not self.busy and self.v['simulation_mode'].get()=='Weight saving + FEA comparison' else 'disabled')
        target_enabled=not self.busy and self.v['simulation_mode'].get()=='Weight saving + FEA comparison'
        self.retention_scale.configure(state='normal' if target_enabled else 'disabled')
        for button in self.retention_presets:button.configure(state='normal' if target_enabled else 'disabled')
        for button in self.view_buttons:button.configure(state='normal' if self.model else 'disabled')
        if self.v['simulation_mode'].get()!='Weight saving':
            self.search_help.set('FEA simulates every allowed infill combination. This design count controls how many candidates are then compared in OrcaSlicer.')
        else:
            self.search_help.set('More designs give a wider comparison and take longer. This does not certify strength.')
        if self.v['simulation_mode'].get()=='FEA direct search (experimental)' and not self.sections['simulation']['open']:
            self.toggle_section('simulation')
        if self.v['simulation_mode'].get()=='FEA direct search (experimental)' and not self.sections['simulation_absolute']['open']:
            self.toggle_section('simulation_absolute')
        self.generate_button.configure(state='normal' if loaded else 'disabled')
        available=Path(self.v['orca'].get()).is_file() and all(self.v[k].get().strip() for k in ('printer_profile','process_profile','filament_profile'))
        self.optimize_button.configure(state='normal' if loaded and (available or self.manual.get()) else 'disabled',text='Prepare manual instructions' if self.manual.get() else 'Optimize Model')
        self.check_button.configure(state='disabled' if self.busy else 'normal');self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        successful=self.report is not None and self.report.get('status')=='AUTOMATED SUCCESS' and not self.busy
        if successful:
            try:
                files=[Path(self.report.get('exports',{}).get(name,'')) for name in ('optimized.gcode','baseline.gcode')]
                successful=all(path.is_file() and path.stat().st_size>0 for path in files)
            except OSError:successful=False
        self.export_status.set('Validated G-code ready to save.' if successful else 'Optimization running…' if self.busy else 'No optimized G-code ready to save.')
        self.phase.set(self.task_description if self.busy else '04  Print files ready' if successful else '02  Set your goals' if self.model else '01  Add a model')
        self.export_button.configure(state='normal' if successful else 'disabled',text='Save Optimized G-code…')
        self.open_button.configure(state='normal' if self.report and not self.busy else 'disabled')
        self.refresh_setup_summary()
        if self.busy:self.next_step.set(self.task_description+'. You can stop at any time.')
        elif not self.model:self.next_step.set('Next: add a model or try the example to unlock the comparison.')
        elif not available and not self.manual.get():self.next_step.set('Next: open “Printer setup & troubleshooting” to select OrcaSlicer, or choose manual mode.')
        elif successful:self.next_step.set('Your comparison is ready. Save the print files, or replace the model to try another object.')
        elif self.manual.get():self.next_step.set('Manual mode: prepare suggestions, then apply them in OrcaSlicer. No printable files will be generated here.')
        else:self.next_step.set('Ready: compare designs using the selected printer and material. Strength still needs a physical test.')

    def error(self,exc):self.status.set(str(exc));messagebox.showerror('AeroSlice',str(exc),parent=self)

    def set_model(self,path,loaded_model=None):
        if self.busy:return
        try:
            model=loaded_model if loaded_model is not None else load_model(path)
            self.motion_panel.invalidate('Waiting: optimize this newly uploaded model to retrieve both sliced masses.')
            self.model=model;self.report=None;self.print_export_folder=None
            self.clear_sensor_results(clear_readings=True)
            self.details={key:value for key,value in self.details.items() if key=='printer_check'}
            for key in ('measured_baseline','measured_adaptive','force','deflection','stiff_mass','conditions'):
                self.v[key].set('')
            for key in list(self.figures):
                if key.startswith('heatmap_'):
                    canvas=self.figures.pop(key);window=canvas.get_tk_widget().winfo_toplevel()
                    self.dispose_figure(canvas);window.destroy()
            self.result_hint.set('Model ready. Choose your setup and select “Optimize Model”.')
            for var in self.metric_vars.values():var.set('—')
            self.export_status.set('No optimized G-code generated yet.')
            self.dashboard_write('A new model is ready: '+model.path.name+'\n\nRun a new comparison to see its estimates and save its print files. Previous results remain saved in their original folders.')
            if self.preview_empty.winfo_exists():self.preview_empty.destroy()
            dims=' × '.join(f'{x:.2f}' for x in model.dimensions)
            name=model.path.name if len(model.path.name)<=52 else model.path.name[:24]+'…'+model.path.name[-24:]
            self.model_info.set(f'{name}\n{dims} mm · {model.kind} · Ready\n'+ '\n'.join(model.warnings))
            fig=Figure(figsize=(6,4),facecolor='#f8fafc');ax=fig.add_axes((0,0,1,1),projection='3d',facecolor='#f8fafc')
            # Preview decimation is visual only; optimization uses the full mesh.
            triangles=model.triangles[::max(1,len(model.triangles)//3000)]
            ax.add_collection3d(Poly3DCollection(triangles,facecolor='#38bdf8',edgecolor='#0369a1',linewidth=.15,alpha=.75))
            lo,hi=model.bounds
            ax.set(xlim=(lo[0],hi[0]),ylim=(lo[1],hi[1]),zlim=(lo[2],hi[2]),xlabel='X (mm)',ylabel='Y (mm)',zlabel='Z (mm)');ax.set_box_aspect(model.dimensions)
            for index,label in enumerate('XYZ'):
                direction=np.zeros(3);direction[index]=model.dimensions[index]*.25
                color=('#0369a1','#0d9488','#64748b')[index]
                ax.quiver(*lo,*direction,color=color,linewidth=2,arrow_length_ratio=.2)
                ax.text(*(lo+direction*1.15),label,color=color,fontsize=9,weight='bold')
            ax.set_axis_off();ax.set_proj_type('ortho');ax.view_init(elev=25,azim=-40)
            self.show_figure('preview',self.preview_container,fig)
            self.figures['preview'].get_tk_widget()._viewport=True
            self.figures['preview'].get_tk_widget().configure(width=1,height=1)
            self.fit_preview()
            self.figures['preview'].mpl_connect('scroll_event',self.zoom_preview)
            self.status.set('Model ready. Choose your setup, then find a lighter print.');self.tree.delete(*self.tree.get_children());self.update_actions()
        except Exception as exc:self.error(exc)

    def upload_model(self):
        if self.busy:return
        path=filedialog.askopenfilename(title='Choose the object you want to print',filetypes=[('3D models','*.stl *.STL *.3mf *.3MF')])
        if path:
            self.status.set('Loading and checking the uploaded mesh…')
            self.worker(lambda:{'kind':'model','model':load_model(path)},'Loading and checking model')
    def demo_model(self):self.set_model(self.examples/'demo_payload.stl')
    def choose_executable(self):
        p=filedialog.askopenfilename(title='Choose OrcaSlicer executable',initialdir='/Applications/OrcaSlicer.app/Contents/MacOS')
        if p:self.v['orca'].set(p);self.update_actions()
    def choose_profiles_root(self):
        p=filedialog.askdirectory(title='Choose OrcaSlicer profiles folder')
        if p:self.v['profile_root'].set(p);self.update_actions()
    def choose_profile(self,key):
        p=filedialog.askopenfilename(title='Select profile JSON',filetypes=[('Profile JSON','*.json')])
        if p:self.v[key].set(p);self.update_actions()
    def choose_output(self):
        p=filedialog.askdirectory(title='Select output folder')
        if p:self.v['output'].set(p)

    def save_preferences(self):
        for key in ('printer_profile','process_profile','filament_profile','profile_root'):self.config_data[key]=self.v[key].get()
        for key in ('simulation_mode','contact_axis','contact_face','fea_force_axis'):self.config_data[key]=self.v[key].get()
        self.config_data['output_folder']=self.v['output'].get()
        self.config_data['candidate_count']=int(self.v['candidate_count'].get())
        try:retention=float(self.v['fea_retention'].get())
        except ValueError:retention=95.
        self.config_data['stiffness_retention_percent']=retention if math.isfinite(retention) and 0<retention<=100 else 95.
        self.config_data['orca_executable']=self.v['orca'].get();self.config_path.parent.mkdir(parents=True,exist_ok=True)
        self.config_path.write_text(json.dumps(self.config_data,indent=2))

    def worker(self,operation,description='Comparing designs and checking print files'):
        if self.busy:return
        # Closed Tk windows can leave Python reference cycles. Reclaim them
        # on the Tk thread before allocation-heavy background work can trigger
        # collection and finalize a Tcl interpreter on the wrong thread.
        gc.collect()
        self.busy=True;self.task_description=description;self.cancel.clear();self.progress_bar.pack(fill='x',pady=5);self.progress_bar.start();self.update_actions()
        def run():
            try:
                value=operation()
                if isinstance(value,dict) and value.get('kind') in ('model','candidates') and self.cancel.is_set():
                    raise ValueError('Operation cancelled. The previous model was preserved.')
                self.queue.put(('complete',value))
            except Exception as exc:self.queue.put(('error',str(exc)))
        # Keep the worker alive long enough to terminate Orca and write its report
        # if the window closes during slicing. No Tk calls occur in this thread.
        self.worker_thread=Thread(target=run,daemon=False)
        self.worker_thread.start()

    def generate(self):
        if not self.model or self.busy:return
        try:
            settings=self.settings();model=self.model;simulation=self.v['simulation_mode'].get()!='Weight saving'
            def operation():
                regions,notes=make_regions(model.oriented(settings.rotation),settings)
                if simulation:
                    from .fea import density_options
                    from .optimizer import Candidate
                    candidates=[Candidate(f'fea_{i:03d}',values,'Awaiting FEA evaluation.',False) for i,values in enumerate(density_options(settings))]
                    notes=['Direct-search density grid preview only. No FEA or slicing has been performed.']
                else:candidates=generate_candidates(regions,settings)
                return {'kind':'candidates','regions':regions,'candidates':candidates,'notes':notes}
            self.worker(operation,'Preparing engineering recommendations')
        except Exception as exc:self.error(exc)

    def start_optimization(self):
        if not self.model or self.busy:return
        try:
            settings=self.settings();manual=self.manual.get();fea=None if manual else self.fea_settings()
            model=self.model;slicer=self.slicer();profiles=self.profiles();output=Path(self.v['output'].get())
            self.save_preferences()
            self.motion_panel.invalidate('Waiting: slicing original and optimized designs for this model…')
            self.report=None;self.print_export_folder=None;self.tree.delete(*self.tree.get_children())
            for var in self.metric_vars.values():var.set('—')
            self.details.pop('candidate_preview',None);self.details.pop('manual_comparison',None)
            self.result_hint.set('Optimization in progress; estimates will appear after completed slicing.')
            self.dashboard_write('COMPARISON IN PROGRESS\nWaiting for completed slices for '+model.path.name+'.')
            progress=lambda msg:self.queue.put(('progress',msg))
            self.worker(lambda:optimize(model,settings,output,slicer,profiles,progress,manual,fea))
        except Exception as exc:self.error(exc)

    def check_compatibility(self):
        if self.busy:return
        try:
            slicer=self.slicer();profiles=self.profiles();output=Path(self.v['output'].get());model=load_model(self.examples/'demo_payload.stl')
            self.worker(lambda:{'kind':'compatibility','result':optimize(model,Settings(candidate_count=3),output,slicer,profiles,lambda msg:self.queue.put(('progress','Compatibility: '+msg)))},'Checking printer setup')
        except Exception as exc:self.error(exc)

    def poll(self):
        try:
            while True:
                kind,value=self.queue.get_nowait()
                if kind=='progress':self.status.set(self.friendly_progress(value))
                elif kind=='error':
                    self.busy=False;self.progress_bar.stop();self.progress_bar.pack_forget();self.update_actions();self.motion_panel.refresh();self.error(value)
                elif kind=='complete':
                    self.busy=False;self.progress_bar.stop();self.progress_bar.pack_forget()
                    if value.get('kind')=='model':
                        self.set_model(value['model'].path,value['model'])
                    elif value.get('kind')=='candidates':
                        self.tree.delete(*self.tree.get_children())
                        for i,c in enumerate(value['candidates']):self.tree.insert('', 'end',values=(self.design_name(c.id,i),'—','—','Not sliced'))
                        self.details['candidate_preview']={'candidates':[asdict(c) for c in value['candidates']],'assumptions':value['notes']}
                        self.open_heatmap(value['regions'],value['candidates'])
                        self.status.set('Design options previewed. They have not been sliced yet; run the comparison to get real estimates.')
                    elif value.get('kind')=='compatibility':
                        result=value['result'];self.details['printer_check']=result
                        passed=result['status']=='AUTOMATED SUCCESS'
                        self.status.set('Printer setup check passed on the example model.' if passed else 'Printer setup needs attention. See Your results for next steps.')
                        self.dashboard_write('PRINTER SETUP CHECK\n'+('The example model was sliced and its files checked successfully. You can now compare your own model.' if passed else result['message'])+'\nSaved check: '+result['run_directory'],append=True)
                        if not passed and not self.sections['printer']['open']:self.toggle_section('printer')
                    else:self.show_report(value)
                    self.update_actions()
        except Empty:pass
        try:
            while True:
                kind,value=self.recorder.messages.get_nowait()
                if kind=='reading':self.sensor_label.set(f'Live sensor: {value.timestamp_ms:g} ms • {value.distance_mm} mm • {value.error or "valid"}')
                elif kind=='error':self.sensor_label.set('Sensor error: '+str(value))
                elif kind=='info':self.sensor_label.set('Sensor: '+str(value))
        except Empty:pass
        self.after(100,self.poll)

    @staticmethod
    def design_name(candidate_id,index):
        if candidate_id=='baseline':return 'Baseline design'
        if candidate_id=='uniform_lightweight':return 'Uniform lighter fill'
        return f'Design {index}'

    @staticmethod
    def print_time(seconds):
        minutes=round(seconds/60)
        return f'{minutes//60} h {minutes%60:02d} min' if minutes>=60 else f'{max(1,minutes)} min'

    @staticmethod
    def friendly_progress(message):
        if message.startswith('Slicing '):return 'Preparing print instructions: '+message.split(':',1)[0].replace('Slicing ','design ')
        if message.startswith('Analyzing'):return 'Checking your model and planning its internal fill…'
        if message.startswith('Loading'):return 'Preparing your selected printer and material setup…'
        if message.startswith('Reopening'):return 'Checking that the saved project produces the same print instructions…'
        return message

    def open_heatmap(self,regions,candidates):
        window=tk.Toplevel(self);window.title('Design options • internal fill preview');window.geometry('850x480')
        ttk.Label(window,text='This shows planned internal fill percentages, not a stress simulation or a sliced print.',style='Hint.TLabel',wraplength=800).pack(pady=8)
        key='heatmap_'+str(window)
        self.show_figure(key,window,heatmap_figure(regions,candidates))
        def dismiss():
            canvas=self.figures.pop(key,None)
            if canvas:self.dispose_figure(canvas)
            window.destroy()
        window.protocol('WM_DELETE_WINDOW',dismiss)

    def show_report(self,report):
        if self.model is not None and report.get('input_sha256')!=self.model.sha256:
            self.status.set('Ignored results from a different model. Optimize the current uploaded model.')
            return
        self.print_export_folder=None;self.open_button.configure(text='Open working folder')
        for var in self.metric_vars.values():var.set('—')
        self.export_status.set('No optimized G-code generated yet.')
        self.report=report;self.tree.delete(*self.tree.get_children())
        for i,c in enumerate(report['candidates']):
            m=c.get('metrics',{});selected=c['id']==report.get('selected')
            name=self.design_name(c['id'],i)+(' · selected' if selected else '')
            state={'PASS':'Passed','FAIL':'Needs attention','NOT TESTED':'Not tested'}.get(c.get('status'),'Not tested')
            self.tree.insert('', 'end',values=(name,f"{m['mass_g']:.2f}" if m else '—',self.print_time(m['time_s']) if m else '—',state),tags=('selected',) if selected else ('failed',) if state=='Needs attention' else ())
        status=report['status']
        lines=[]
        if status=='AUTOMATED SUCCESS':
            selected=next(c for c in report['candidates'] if c['id']==report['selected']);baseline=report['candidates'][0]
            before,after=baseline['metrics'],selected['metrics'];saving=report['savings_percent_estimated']
            self.metric_vars['before'].set(f"{before['mass_g']:.2f} g")
            self.metric_vars['after'].set(f"{after['mass_g']:.2f} g")
            self.metric_vars['saving'].set(f'{saving:.1f}%')
            prediction=report.get('fea_selected')
            if prediction:
                row=selected.get('fea') or {}
                ratios=[row.get(mesh,{}).get('relative_stiffness_ratio') for mesh in ('coarse','refined')]
                ratios=[ratio for ratio in ratios if ratio is not None]
                retention=min(ratios) if ratios else prediction.get('relative_stiffness_ratio')
                self.metric_vars['stiffness'].set(f'{100*retention:.1f}%' if retention is not None else 'Not assessed')

            else:self.metric_vars['stiffness'].set('Not assessed')
            self.status.set('Comparison complete. Your print files are ready to save.')
            hint=f"Estimated print time: {self.print_time(before['time_s'])} originally → {self.print_time(after['time_s'])} for the selected design."
            if saving<=0:hint+=' This search did not reduce material use.'
            self.result_hint.set(hint)
            lines=['PRINT COMPARISON · ESTIMATES',f"Original: {before['mass_g']:.2f} g of material · {self.print_time(before['time_s'])}",f"Selected: {after['mass_g']:.2f} g of material · {self.print_time(after['time_s'])}",f'Material saved: {saving:.1f}%',
                   '',report['message'],'','WHAT TO DO NEXT','Save G-code and review optimized.gcode in your slicer’s G-code preview before printing. Confirm the selected printer and material match your setup.',
                   'optimized.gcode contains the optimized printer instructions. baseline.gcode is included for comparison. Your original model is unchanged.','No file has been sent to your printer.']
            process=report.get('resolved_profiles',{}).get('process',{})
            lines += ['','BASELINE PRINT SETTINGS',
                      f"Starting infill: {report['settings']['baseline']:g}%. Walls: {process.get('wall_loops','unknown')}. Layer height: {process.get('layer_height','unknown')} mm.",
                      f"Top / bottom solid layers: {process.get('top_shell_layers','unknown')} / {process.get('bottom_shell_layers','unknown')}. Infill pattern: {process.get('sparse_infill_pattern','unknown')}.",
                      'Material profile: '+report['profiles']['filament'],
                      'Weight estimates come directly from OrcaSlicer. Match these settings, model dimensions and material density when comparing with a separately sliced STL. The baseline uses AeroSlice’s selected settings, not your current OrcaSlicer window settings.']
        elif status=='SEMIAUTOMATED':
            self.status.set('Manual steps remain. Suggestions are ready; print files have not been generated.')
            self.result_hint.set('Manual mode: no verified weight estimates or printable files yet. Follow the steps in Your results.')
            lines=['MANUAL WORKFLOW · NOT READY TO PRINT',*report.get('manual_steps',[])]
        else:
            self.status.set('The comparison could not finish. Your original model is unchanged.')
            self.result_hint.set('No optimized export is available. Open Your results for the exact issue and manual next steps.')
            lines=['COMPARISON NEEDS ATTENTION',report['message'],'','You can adjust the advanced settings or prepare manual instructions in Printer setup & troubleshooting.',*report.get('manual_steps',[])]
        lines+=['','Saved working folder: '+report['run_directory'],'','Strength has not been independently verified. Climb results and material savings do not prove stiffness.']
        if report.get('fea'):
            search=report['fea'];prediction=report.get('fea_selected')
            lines+=['','FEA DIRECT SEARCH · PREDICTIONS, PHYSICALLY UNVALIDATED',
                    f"Tested {search['combinations_tested']} combinations; {search['feasible_count']} passed both mesh resolutions.",
                    f"Mesh accuracy checks: {search.get('mesh_pass_count', 'unknown')} passed. Assessment target: {search.get('assessment_pass_count', 'unknown')} passed (before mesh accuracy checks).",
                    ('Normalized reference load for comparison; actual thrust is not an input.' if search['inputs']['assessment']=='relative_stiffness' else f"Load: {search['inputs']['force_n']:g} N along {search['inputs']['force_axis'].upper()}."),
                    f"Support assumption: full {search['support_axis'].upper()} {search['support_face']} face clamped; comparison direction {search['inputs']['force_axis'].upper()}.",
                    'Material input source: '+search['inputs']['material_source']]
            if prediction:
                if search['inputs']['assessment']=='relative_stiffness':
                    lines += [f"Selected predicted relative stiffness: {self.metric_vars['stiffness'].get()} of baseline (target {100*search['inputs']['minimum_stiffness_ratio']:.1f}%).",
                              'Absolute strength and thrust performance were not assessed. fea_selected.png shows illustrative reference-load fields.']
                else:
                    lines += [f"Selected predicted displacement: {prediction['max_displacement_mm']:.5g} mm.",
                              f"Selected screening stress utilization: {prediction['screening_utilization']:.3f} (limit 1).",
                              'See fea_selected.png for stress and displacement maps. No physical strength or failure load was verified.']
        self.dashboard_write('\n'.join(lines))
        self.motion_panel.refresh()
        if status=='FAILED':
            self.book.select(self.dashboard_page)
            messagebox.showerror('Comparison needs attention',report['message'],parent=self)

    def export_files(self):
        if self.busy or not self.report or self.report['status']!='AUTOMATED SUCCESS':return
        destination=filedialog.askdirectory(title='Save optimized G-code and comparison reports')
        if not destination:return
        created=None
        try:
            if self.model is None or self.report.get('input_sha256')!=self.model.sha256:
                raise ValueError('Optimize the current uploaded model before exporting its G-code.')
            self.model.verify_original()
            for name in ('baseline.gcode','optimized.gcode'):
                path=Path(self.report.get('exports',{}).get(name,''))
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError('Completed G-code is missing or empty. Run optimization again before exporting.')
            source=Path(self.report['run_directory']);target=Path(destination)/source.name
            if target.resolve()==source.resolve():
                raise ValueError('Choose a different folder for the G-code export. This folder holds internal slicer working files.')
            target.mkdir()
            created=target
            # Save only the validated G-code and readable reports. Internal
            # slicer projects, candidate folders and setup files stay in work.
            for name in ('baseline.gcode','optimized.gcode'):
                shutil.copy2(self.report['exports'][name],target/name)
            for name in ('report.json','comparison.csv','compatibility_report.json',
                         'validation_report.txt','infill_heatmap.png','README.md','NOTES.md',
                         'fea_search.json','fea_search.csv','fea_selected.png'):
                if (source/name).is_file():shutil.copy2(source/name,target/name)
            self.print_export_folder=target;self.open_button.configure(text='Open saved folder')
            self.status.set(f'G-code saved to {target}. Review optimized.gcode in a G-code preview before printing.')
        except Exception as exc:
            if created is not None:shutil.rmtree(created)
            self.update_actions();self.error(exc)
    def open_output(self):
        if self.report:subprocess.Popen(['open',str(self.print_export_folder or self.report['run_directory'])])

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
            self.details['manual_comparison']=result
            self.dashboard_write(f"MANUALLY IMPORTED PRINT ESTIMATES\nOriginal: {a.mass_g:.2f} g · {self.print_time(a.time_s)}\nComparison: {b.mass_g:.2f} g · {self.print_time(b.time_s)}\nEstimated material saving: {result['savings_percent_estimated']:.1f}%\n\n"+result['note'])
            self.status.set('Imported estimates are ready in Your results. Model and regional settings have not been verified.')
        except Exception as exc:self.error(exc)

    def refresh_ports(self):
        try:
            available=ports();self.port_box.configure(values=available)
            if self.v['port'].get() not in available:self.v['port'].set('')
            if not available:self.sensor_label.set('No serial ports detected. CSV import and physics simulation remain available.')
        except Exception as exc:self.error(exc)

    def clear_sensor_results(self,clear_readings=False):
        self.trials=[];self.summary=None;self.details.pop('climb_analysis',None)
        ranges=self.dashboard_text.tag_ranges('climb_analysis')
        if ranges:
            self.dashboard_text.configure(state='normal')
            for i in range(len(ranges)-2,-1,-2):self.dashboard_text.delete(ranges[i],ranges[i+1])
            self.dashboard_text.configure(state='disabled')
        self.trial_box.set('');self.trial_box.configure(values=[])
        for key in ('motion','comparison'):
            canvas=self.figures.pop(key,None)
            if canvas:self.dispose_figure(canvas)
        if clear_readings:
            try:self.recorder.stop()
            except Exception as exc:self.sensor_label.set('Sensor disconnected: '+str(exc))
            self.recorder.readings.clear();self.readings=[]
            self.v['start_mm'].set('');self.v['end_mm'].set('')
            self.sensor_label.set('No readings for this model. Import CSV or record a new trial.')

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
        self.clear_sensor_results();self.readings=[]
        try:self.readings=read_csv(path);self.process_sensor()
        except Exception as exc:self.error(exc)
    def demo_sensor(self):
        self.readings=synthetic_readings();self.v['start_mm'].set('750');self.v['end_mm'].set('300');self.process_sensor();self.analyze_sensor()
    def process_sensor(self):
        self.clear_sensor_results()
        try:
            low=float(self.v['sensor_min'].get()) if self.v['sensor_min'].get().strip() else None
            high=float(self.v['sensor_max'].get()) if self.v['sensor_max'].get().strip() else None
            self.trials=process_trials(self.readings,low,high,self.smooth.get());self.summary=None
            variants=sorted({t.variant for t in self.trials});self.baseline_variant_box.configure(values=variants)
            if self.v['baseline_variant'].get() not in variants:self.v['baseline_variant'].set('')
            self.trial_box.configure(values=[f'{t.variant} / {t.trial_id}' for t in self.trials]);self.trial_box.current(0);self.show_trial()
            return True
        except Exception as exc:self.error(exc);return False
    def show_trial(self):
        if not self.trials:return
        trial=self.trials[min(len(self.trials)-1,max(0,self.trial_box.current()))]
        self.show_figure('motion',self.motion_container,motion_figure(trial));self.sensor_label.set(trial.source+' • '+trial.processing+' • '+' '.join(trial.warnings))
    def analyze_sensor(self):
        try:
            if not self.readings:raise ValueError('Upload CSV or record readings first.')
            if not self.process_sensor():return
            if not self.v['baseline_variant'].get():raise ValueError('Choose which CSV design label represents your original print, then compare again.')
            self.summary=summarize(self.trials,float(self.v['start_mm'].get()),float(self.v['end_mm'].get()),float(self.v['max_gap'].get()),self.v['baseline_variant'].get(),self.paired.get())
            self.details['climb_analysis']=self.summary
            lines=['CLIMB TEST COMPARISON']
            for row in self.summary['variants']:
                lines.append(f"{row['variant']}: {row['mean_climb_time_s']:.2f} s average across {row['n']} tests · {row['source']}")
            for row in self.summary['comparisons']:
                lines.append(f"{row['variant']}: {row['improvement_percent']:.1f}% change in climb time. {row['conclusion']}")
            lines+=self.summary['warnings']
            self.dashboard_write('\n'.join(lines),append=True,section='climb_analysis')
            self.show_figure('comparison',self.comparison_container,comparison_figure(self.summary));self.sensor_label.set(('SYNTHETIC DEMONSTRATION DATA · ' if any('SYNTHETIC' in t.source for t in self.trials) else 'User-supplied measurements · ')+'Comparison ready in Your results. '+' '.join(self.summary['warnings']))
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
            self.details['weighed_objects']=result
            self.dashboard_write(f"WEIGHED OBJECTS · USER-SUPPLIED MEASUREMENTS\nOriginal: {a:.2f} g · New object: {b:.2f} g\nMeasured material saving: {result['measured_payload_savings_percent']:.1f}%",append=True)
            if self.report:self.report['measured_masses']=result;write_reports(Path(self.report['run_directory']),self.report)
        except Exception as exc:self.error(exc)
    def calculate_stiffness(self):
        try:
            result=stiffness(float(self.v['force'].get()),float(self.v['deflection'].get()),float(self.v['stiff_mass'].get()),self.v['conditions'].get())
            self.details['stiffness_test']=result
            self.dashboard_write(f"SEPARATE STIFFNESS TEST · USER-SUPPLIED MEASUREMENTS\nStiffness: {result['stiffness_n_per_mm']:.2f} N/mm\nStiffness per gram: {result['stiffness_to_mass_n_per_mm_per_g']:.2f} N/mm/g\nConditions: {result['conditions']}\nCompare specimens only under equivalent support and loading conditions.",append=True)
            if self.report:self.report['stiffness']=result;write_reports(Path(self.report['run_directory']),self.report)
        except Exception as exc:self.error(exc)
    def close(self):
        self.motion_panel.close()
        self.cancel.set();self.recorder.disconnect()
        worker=getattr(self,'worker_thread',None)
        if worker and worker.is_alive():worker.join(timeout=.5)
        for canvas in self.figures.values():self.dispose_figure(canvas)
        self.figures.clear()
        self.destroy()
        self.v.clear();self.metric_vars.clear()
        for name in ('status','model_info','sensor_label','manual','smooth','paired','setup_summary','search_choice','search_help','next_step','result_hint','material_choice','phase'):
            setattr(self,name,None)
