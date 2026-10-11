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
        self.title('AeroSlice • Make lighter prints');self.geometry('1250x850');self.minsize(1050,700)
        self.examples=examples;self.model=None;self.report=None;self.trials=[];self.readings=[];self.summary=None
        self.busy=False;self.queue=Queue();self.cancel=Event();self.recorder=Recorder();self.figures={}
        self.config_path=Path.home()/'Library/Application Support/AeroSlice/config.json'
        self.config_data=json.loads((Path(__file__).parent/'config.json').read_text())
        if self.config_path.exists():
            try: self.config_data.update(json.loads(self.config_path.read_text()))
            except (OSError,ValueError): pass
        self.v={};self.sections={};self.details={};style=ttk.Style(self);style.theme_use('clam')
        style.configure('TFrame',background='#f1f5f9');style.configure('TLabel',background='#f1f5f9',foreground='#0f172a')
        style.configure('TLabelframe',background='#f1f5f9');style.configure('TLabelframe.Label',background='#f1f5f9',foreground='#0369a1',font=('Helvetica',12,'bold'))
        style.configure('TButton',padding=(12,8),font=('Helvetica',11));style.configure('Accent.TButton',background='#0369a1',foreground='white',font=('Helvetica',12,'bold'))
        style.map('Accent.TButton',background=[('disabled','#cbd5e1'),('active','#075985')],foreground=[('disabled','#475569')])
        style.configure('Hint.TLabel',foreground='#475569',font=('Helvetica',10))
        style.configure('Metric.TLabel',foreground='#0369a1',font=('Helvetica',21,'bold'))
        style.configure('Section.TButton',anchor='w',padding=(8,8))
        style.configure('Treeview',rowheight=27);style.configure('TNotebook.Tab',padding=(20,10))
        header=ttk.Frame(self,padding=(20,12));header.pack(fill='x')
        ttk.Label(header,text='AeroSlice',font=('Helvetica',25,'bold')).pack(side='left')
        ttk.Label(header,text='  Make lighter prints. Compare the results. Save your files.',font=('Helvetica',12)).pack(side='left',padx=10)
        self.status=tk.StringVar(value='Start by adding a 3D model, or try the example to see how it works.')
        ttk.Label(self,textvariable=self.status,padding=(20,8),wraplength=1180).pack(fill='x')
        self.book=book=ttk.Notebook(self);book.pack(fill='both',expand=True,padx=15,pady=5)
        self.optimize_tab=ttk.Frame(book,padding=10);self.sensor_tab=ttk.Frame(book,padding=10);self.dashboard_tab=ttk.Frame(book,padding=10)
        for title,tab in [('Make a lighter print',self.optimize_tab),('Test & measure (optional)',self.sensor_tab),('Your results',self.dashboard_tab)]:book.add(tab,text=title)
        self._optimize_ui();self._sensor_ui();self._dashboard_ui()
        for key in ('orca','printer_profile','process_profile','filament_profile'):
            self.v[key].trace_add('write',lambda *_:self.update_actions())
        self.v['candidate_count'].trace_add('write',lambda *_:self.refresh_search_summary())
        self.refresh_search_summary()
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
        upload=ttk.LabelFrame(left,text='1  Add your model',padding=14);upload.pack(fill='x',pady=(0,10))
        ttk.Label(upload,text='Choose the object you want to print. Your original file stays unchanged.',wraplength=490,style='Hint.TLabel').pack(anchor='w',pady=(0,10))
        row=ttk.Frame(upload);row.pack(fill='x')
        self.upload_button=ttk.Button(row,text='Add 3D model…',style='Accent.TButton',command=self.upload_model);self.upload_button.pack(side='left')
        ttk.Button(row,text='Try an example',command=self.demo_model).pack(side='left',padx=8)
        self.model_info=self.variable('model_info','STL and 3MF files supported. No model added yet.')
        ttk.Label(upload,textvariable=self.model_info,wraplength=490).pack(anchor='w',pady=(12,0))
        setup=ttk.LabelFrame(left,text='2  Choose your setup',padding=14);setup.pack(fill='x',pady=(0,10))
        self.setup_summary=self.variable('setup_summary','')
        ttk.Label(setup,textvariable=self.setup_summary,wraplength=490,font=('Helvetica',11,'bold')).pack(anchor='w')
        ttk.Label(setup,text='Infill is the structure inside your print. AeroSlice compares layouts that use less material. Defaults assume your object rests on its lower face; change this in Advanced settings if needed.',wraplength=490,style='Hint.TLabel').pack(anchor='w',pady=(8,12))
        material_row=ttk.Frame(setup);material_row.pack(fill='x',pady=(0,12))
        ttk.Label(material_row,text='Material on your spool').pack(side='left',padx=(0,12))
        self.material_choice=self.variable('material_choice','')
        self.material_box=ttk.Combobox(material_row,textvariable=self.material_choice,state='readonly',width=25)
        self.material_box.pack(side='left');self.material_box.bind('<<ComboboxSelected>>',self.apply_material_choice)
        self.material_profiles={}
        search=ttk.Frame(setup);search.pack(fill='x')
        ttk.Label(search,text='How many designs?').pack(side='left',padx=(0,12))
        self.search_choice=self.variable('search_choice','Standard · 8 designs')
        self.search_box=ttk.Combobox(search,textvariable=self.search_choice,state='readonly',width=25,values=['Quick · 3 designs','Standard · 8 designs','Detailed · 12 designs'])
        self.search_box.pack(side='left');self.search_box.bind('<<ComboboxSelected>>',self.apply_search_choice)
        self.search_help=self.variable('search_help','More designs give a wider comparison and take longer. This does not certify strength.')
        ttk.Label(setup,textvariable=self.search_help,wraplength=490,style='Hint.TLabel').pack(anchor='w',pady=(8,12))
        save=ttk.Frame(setup);save.pack(fill='x')
        ttk.Label(save,text='Save working files in').pack(anchor='w')
        self.variable('output',self.config_data.get('output_folder',str(Path.cwd()/'outputs')))
        ttk.Entry(save,textvariable=self.v['output'],width=42).pack(side='left',fill='x',expand=True,pady=5)
        ttk.Button(save,text='Choose folder…',command=self.choose_output).pack(side='left',padx=(8,0))
        settings=self.disclosure(left,'optimization','Advanced: infill & engineering settings')
        ttk.Label(settings,text='Defaults assume the object rests on its lower face. Change these for a different orientation or support arrangement. These are engineering assumptions, not a strength guarantee.',wraplength=490,style='Hint.TLabel').grid(row=0,column=0,columnspan=4,sticky='w',padx=5,pady=8)
        fields=[('baseline','Starting infill (%)',20),('minimum','Lowest allowed (%)',10),('maximum','Highest allowed (%)',30),('candidate_count','Designs to compare',self.config_data.get('candidate_count',8)),('coverage','Demand coverage (0–1)',.75),('acceleration','Acceleration (m/s²)',0)]
        for i,(key,label,val) in enumerate(fields):self.field(settings,key,label,val,1+i//2,i%2)
        self.field(settings,'contact_axis','Support / region axis','z',4,0,choices=['x','y','z'])
        self.field(settings,'contact_face','Support face','min',4,1,choices=['min','max'])
        self.field(settings,'load_axis','Load direction','z',5,0,choices=['x','y','z'])
        self.field(settings,'protected','Region minimums (%)','15,10,10',5,1)
        self.field(settings,'rotation','Rotation X,Y,Z (°)','0,0,0',6,0)
        self.field(settings,'demand','Custom demand (0–1)','',6,1)
        self.field(settings,'fixed_mass','Other moving mass (g)','',7,0)
        ttk.Label(settings,text=DISCLAIMER+' Blank custom demand uses the load-path heuristic; unknown hardware mass can stay blank.',wraplength=490,style='Hint.TLabel').grid(row=8,column=0,columnspan=4,sticky='w',padx=5,pady=8)
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
        actions=ttk.Frame(self.action_footer);actions.pack(fill='x',pady=4)
        self.next_step=self.variable('next_step','Next: add a model to unlock the comparison.')
        ttk.Label(actions,textvariable=self.next_step,wraplength=500,style='Hint.TLabel').grid(row=0,column=0,columnspan=2,sticky='w',pady=(2,8))
        self.optimize_button=ttk.Button(actions,text='Find a lighter print',style='Accent.TButton',command=self.start_optimization);self.optimize_button.grid(row=1,column=0,padx=(0,5),sticky='ew')
        self.cancel_button=ttk.Button(actions,text='Stop',command=self.cancel.set);self.cancel_button.grid(row=1,column=1,sticky='ew')
        actions.columnconfigure(0,weight=1)
        self.progress_bar=ttk.Progressbar(self.action_footer,mode='indeterminate');self.progress_bar.pack(fill='x',pady=5)
        results=ttk.LabelFrame(right,text='3  Compare your results',padding=12);results.pack(fill='x',pady=(0,10))
        metrics=ttk.Frame(results);metrics.pack(fill='x')
        self.metric_vars={}
        for i,(key,label) in enumerate([('before','Original estimate'),('after','Best design estimate'),('saving','Material saved')]):
            card=ttk.Frame(metrics);card.grid(row=0,column=i,sticky='nsew',padx=6);metrics.columnconfigure(i,weight=1)
            ttk.Label(card,text=label,style='Hint.TLabel').pack(anchor='w')
            var=self.variable('metric_'+key,'—');self.metric_vars[key]=var
            ttk.Label(card,textvariable=var,style='Metric.TLabel').pack(anchor='w',pady=4)
        self.result_hint=self.variable('result_hint','Your estimates will appear here after the comparison. No printing knowledge needed to get started.')
        ttk.Label(results,textvariable=self.result_hint,wraplength=510,style='Hint.TLabel').pack(anchor='w',pady=(8,0))
        preview=ttk.LabelFrame(right,text='Your model • drag to rotate',padding=5);preview.pack(fill='both',expand=True)
        self.preview_container=preview
        self.preview_empty=ttk.Label(preview,text='Add a model to see it here.\n\nNew to 3D printing? Try the example first.',anchor='center',justify='center',style='Hint.TLabel');self.preview_empty.pack(fill='both',expand=True,pady=25)
        detail=self.disclosure(right,'designs','Details: compare all designs')
        self.tree=ttk.Treeview(detail,columns=('design','mass','time','status'),show='headings',height=5)
        for key,label,width in [('design','Design',180),('mass','Material (g)',90),('time','Print time',100),('status','File checks',100)]:self.tree.heading(key,text=label);self.tree.column(key,width=width)
        self.tree.tag_configure('selected',background='#d1fae5');self.tree.tag_configure('failed',foreground='#9f1239')
        self.tree.pack(fill='both',expand=True)
        ttk.Label(detail,text='Mass and time come from OrcaSlicer estimates. Passing file checks does not prove strength.',wraplength=510,style='Hint.TLabel').pack(anchor='w',pady=5)
        ttk.Button(detail,text='Import a manually sliced comparison…',command=self.import_slices).pack(anchor='w',pady=3)
        export=ttk.LabelFrame(right,text='4  Save your print files',padding=10);export.pack(fill='x',pady=(8,0))
        self.export_button=ttk.Button(export,text='Save print files…',style='Accent.TButton',command=self.export_files);self.export_button.pack(side='left')
        self.open_button=ttk.Button(export,text='Open saved folder',command=self.open_output);self.open_button.pack(side='left',padx=8)
        ttk.Label(right,text='Material and time are estimates. Strength needs a physical test. Review the files in OrcaSlicer before printing.',wraplength=540,style='Hint.TLabel').pack(fill='x',pady=8)

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
        self.setup_summary.set(f"Printer: {Path(printer).stem if printer.endswith('.json') else printer}\nMaterial: {Path(material).stem if material.endswith('.json') else material}")


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

    def scroll_controls(self,event):
        canvas=self.controls_canvas
        if canvas.winfo_ismapped() and canvas.winfo_rootx()<=event.x_root<canvas.winfo_rootx()+canvas.winfo_width() and canvas.winfo_rooty()<=event.y_root<canvas.winfo_rooty()+canvas.winfo_height():
            steps=int(-event.delta/120) if abs(event.delta)>=120 else int(-event.delta)
            canvas.yview_scroll(steps,'units')

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
        ttk.Button(live,text='Refresh ports',command=lambda:self.port_box.configure(values=ports())).grid(row=0,column=2)
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
        self.sensor_label=self.variable('sensor_label','No readings yet. Start with a CSV or the simulated example.')
        ttk.Label(self.sensor_tab,textvariable=self.sensor_label,wraplength=1100,style='Hint.TLabel').pack(fill='x',pady=6)
        self.motion_container=ttk.Frame(self.sensor_tab);self.motion_container.pack(fill='both',expand=True,pady=4)
        footer=ttk.Frame(self.sensor_tab);footer.pack(fill='x')
        ttk.Button(footer,text='Save original readings…',command=self.save_sensor).pack(side='left')
        ttk.Button(footer,text='Save report & charts…',command=self.export_sensor).pack(side='left',padx=8)

    def _dashboard_ui(self):
        top=ttk.Frame(self.dashboard_tab);top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='Your results, explained',font=('Helvetica',18,'bold')).pack(side='left')
        ttk.Button(top,text='View technical details…',command=self.view_details).pack(side='right')
        ttk.Label(self.dashboard_tab,text='Print estimates, real measurements and simulated examples are labeled separately.',style='Hint.TLabel').pack(anchor='w')
        self.dashboard_text=tk.Text(self.dashboard_tab,height=13,wrap='word',font=('Helvetica',12),bg='white',fg='#0f172a',padx=18,pady=14,relief='flat')
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
        self.dashboard_write('Start with a model in “Make a lighter print”.\n\nAeroSlice will compare estimated material use and print time, then prepare the selected files for OrcaSlicer.\n\nYou can also import rig measurements in “Test & measure” to compare climb times. A faster climb does not prove a stronger print.\n\nStrength has not been independently verified. Hardware measurements are optional and start empty.')

    def view_details(self):
        window=tk.Toplevel(self);window.title('AeroSlice • Technical details');window.geometry('850x600')
        text=tk.Text(window,wrap='word',font=('Menlo',10));text.pack(fill='both',expand=True)
        data={'optimization':self.report,'other_results':self.details}
        text.insert('end',json.dumps(data,indent=2,default=str) if self.report or self.details else 'No results yet. Run a comparison or import measurements first.')
        text.configure(state='disabled')
        ttk.Button(window,text='Close',command=window.destroy).pack(pady=8)

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
    def slicer(self):return OrcaSlicer(self.v['orca'].get(),self.v['profile_root'].get(),self.config_data['slicer_timeout_s'],self.cancel)

    def update_actions(self):
        loaded=self.model is not None and not self.busy
        self.upload_button.configure(state='disabled' if self.busy else 'normal',text='Replace model…' if self.model else 'Add 3D model…')
        self.search_box.configure(state='disabled' if self.busy else 'readonly')
        self.material_box.configure(state='disabled' if self.busy else 'readonly')
        self.generate_button.configure(state='normal' if loaded else 'disabled')
        available=Path(self.v['orca'].get()).is_file() and all(self.v[k].get().strip() for k in ('printer_profile','process_profile','filament_profile'))
        self.optimize_button.configure(state='normal' if loaded and (available or self.manual.get()) else 'disabled',text='Prepare manual instructions' if self.manual.get() else 'Find a lighter print')
        self.check_button.configure(state='disabled' if self.busy else 'normal');self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        successful=self.report is not None and self.report.get('status')=='AUTOMATED SUCCESS' and not self.busy
        self.export_button.configure(state='normal' if successful else 'disabled')
        self.open_button.configure(state='normal' if self.report and not self.busy else 'disabled')
        self.refresh_setup_summary()
        if self.busy:self.next_step.set('Comparing designs and checking the generated print files. You can stop at any time.')
        elif not self.model:self.next_step.set('Next: add a model or try the example to unlock the comparison.')
        elif not available and not self.manual.get():self.next_step.set('Next: open “Printer setup & troubleshooting” to select OrcaSlicer, or choose manual mode.')
        elif successful:self.next_step.set('Your comparison is ready. Save the print files, or replace the model to try another object.')
        elif self.manual.get():self.next_step.set('Manual mode: prepare suggestions, then apply them in OrcaSlicer. No printable files will be generated here.')
        else:self.next_step.set('Ready: compare designs using the selected printer and material. Strength still needs a physical test.')

    def error(self,exc):self.status.set(str(exc));messagebox.showerror('AeroSlice',str(exc),parent=self)

    def set_model(self,path):
        if self.busy:return
        try:
            model=load_model(path)
            self.model=model;self.report=None
            self.result_hint.set('Model ready. Choose your setup and select “Find a lighter print”.')
            for var in self.metric_vars.values():var.set('—')
            self.dashboard_write('A new model is ready: '+model.path.name+'\n\nRun a new comparison to see its estimates and save its print files. Previous results remain saved in their original folders.')
            if self.preview_empty.winfo_exists():self.preview_empty.destroy()
            dims=' × '.join(f'{x:.2f}' for x in model.dimensions)
            self.model_info.set(f'{model.path.name}\nSize: {dims} mm · {model.kind}\n'+ '\n'.join(model.warnings))
            fig=Figure(figsize=(5,3),layout='constrained');ax=fig.add_subplot(projection='3d')
            # Preview decimation is visual only; optimization uses the full mesh.
            triangles=model.triangles[::max(1,len(model.triangles)//3000)]
            ax.add_collection3d(Poly3DCollection(triangles,facecolor='#38bdf8',edgecolor='#0369a1',linewidth=.15,alpha=.75))
            lo,hi=model.bounds
            ax.set(xlim=(lo[0],hi[0]),ylim=(lo[1],hi[1]),zlim=(lo[2],hi[2]),xlabel='X (mm)',ylabel='Y (mm)',zlabel='Z (mm)');ax.set_box_aspect(model.dimensions)
            self.show_figure('preview',self.preview_container,fig)
            self.status.set('Model ready. Choose your setup, then find a lighter print.');self.tree.delete(*self.tree.get_children());self.update_actions()
        except Exception as exc:self.error(exc)

    def upload_model(self):
        path=filedialog.askopenfilename(title='Choose the object you want to print',filetypes=[('3D models','*.stl *.STL *.3mf *.3MF')])
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
        self.config_data['output_folder']=self.v['output'].get()
        self.config_data['candidate_count']=int(self.v['candidate_count'].get())
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
                if kind=='progress':self.status.set(self.friendly_progress(value))
                elif kind=='error':
                    self.busy=False;self.progress_bar.stop();self.update_actions();self.error(value)
                elif kind=='complete':
                    self.busy=False;self.progress_bar.stop()
                    if value.get('kind')=='candidates':
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
        except Empty:pass
        self.after(100,self.poll)

    @staticmethod
    def design_name(candidate_id,index):
        if candidate_id=='baseline':return 'Original design'
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
            self.status.set('Comparison complete. Your print files are ready to save.')
            hint=f"Estimated print time: {self.print_time(before['time_s'])} originally → {self.print_time(after['time_s'])} for the selected design."
            if saving<=0:hint+=' This search did not reduce material use.'
            self.result_hint.set(hint)
            lines=['PRINT COMPARISON · ESTIMATES',f"Original: {before['mass_g']:.2f} g of material · {self.print_time(before['time_s'])}",f"Selected: {after['mass_g']:.2f} g of material · {self.print_time(after['time_s'])}",f'Material saved: {saving:.1f}%',
                   '',report['message'],'','WHAT TO DO NEXT','Save print files, open the selected project in OrcaSlicer, and review the preview and printer/material settings before printing.',
                   'The project (.3mf) can be edited in OrcaSlicer. The printer instructions (.gcode) were generated by the slicer.','No file has been sent to your printer.']
        elif status=='SEMIAUTOMATED':
            self.status.set('Manual steps remain. Suggestions are ready; print files have not been generated.')
            self.result_hint.set('Manual mode: no verified weight estimates or printable files yet. Follow the steps in Your results.')
            lines=['MANUAL WORKFLOW · NOT READY TO PRINT',*report.get('manual_steps',[])]
        else:
            self.status.set('The comparison could not finish. Your original model is unchanged.')
            self.result_hint.set('No optimized export is available. Open Your results for the exact issue and manual next steps.')
            lines=['COMPARISON NEEDS ATTENTION',report['message'],'','You can adjust the advanced settings or prepare manual instructions in Printer setup & troubleshooting.',*report.get('manual_steps',[])]
        lines+=['','Saved working folder: '+report['run_directory'],'','Strength has not been independently verified. Climb results and material savings do not prove stiffness.']
        self.dashboard_write('\n'.join(lines))
        if status=='FAILED':
            self.book.select(self.dashboard_tab)
            messagebox.showerror('Comparison needs attention',report['message'],parent=self)

    def export_files(self):
        if not self.report or self.report['status']!='AUTOMATED SUCCESS':return
        destination=filedialog.askdirectory(title='Export validated files and reports')
        if not destination:return
        try:
            source=Path(self.report['run_directory']);target=Path(destination)/source.name
            if target.resolve()==source.resolve():self.status.set('Validated files are already saved in this folder.');return
            shutil.copytree(source,target)
            self.status.set(f'Print files saved to {target}. Open the project in OrcaSlicer to review it before printing.')
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
            self.details['manual_comparison']=result
            self.dashboard_write(f"MANUALLY IMPORTED PRINT ESTIMATES\nOriginal: {a.mass_g:.2f} g · {self.print_time(a.time_s)}\nComparison: {b.mass_g:.2f} g · {self.print_time(b.time_s)}\nEstimated material saving: {result['savings_percent_estimated']:.1f}%\n\n"+result['note'])
            self.status.set('Imported estimates are ready in Your results. Model and regional settings have not been verified.')
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
            variants=sorted({t.variant for t in self.trials});self.baseline_variant_box.configure(values=variants)
            if self.v['baseline_variant'].get() not in variants:self.v['baseline_variant'].set('')
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
            if not self.v['baseline_variant'].get():raise ValueError('Choose which CSV design label represents your original print, then compare again.')
            self.summary=summarize(self.trials,float(self.v['start_mm'].get()),float(self.v['end_mm'].get()),float(self.v['max_gap'].get()),self.v['baseline_variant'].get(),self.paired.get())
            self.details['climb_analysis']=self.summary
            lines=['CLIMB TEST COMPARISON']
            for row in self.summary['variants']:
                lines.append(f"{row['variant']}: {row['mean_climb_time_s']:.2f} s average across {row['n']} tests · {row['source']}")
            for row in self.summary['comparisons']:
                lines.append(f"{row['variant']}: {row['improvement_percent']:.1f}% change in climb time. {row['conclusion']}")
            lines+=self.summary['warnings']
            self.dashboard_write('\n'.join(lines),append=True)
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
        self.cancel.set();self.recorder.disconnect()
        for canvas in self.figures.values():self.dispose_figure(canvas)
        self.figures.clear()
        self.destroy()
        self.v.clear();self.metric_vars.clear()
        for name in ('status','model_info','sensor_label','manual','smooth','paired','setup_summary','search_choice','search_help','next_step','result_hint','material_choice'):
            setattr(self,name,None)
