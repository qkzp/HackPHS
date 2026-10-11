from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch,tmp_path):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))

@pytest.mark.gui
def test_model_replacement_and_sensor_demo(cube,tmp_path):
    import tkinter as tk
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('NOT TESTED: graphical Tk session required')
    from AeroSlice.gui import Application
    from AeroSlice.main import make_examples
    make_examples(tmp_path/'examples')
    app=Application(tmp_path/'examples')
    try:
        app.update();assert str(app.optimize_button['state'])=='disabled'
        assert app.optimize_button.winfo_ismapped()
        assert app.optimize_button.winfo_rooty()+app.optimize_button.winfo_height() <= app.winfo_rooty()+app.winfo_height()
        assert app.export_button.winfo_ismapped()
        assert app.export_button.winfo_rooty()+app.export_button.winfo_height()<=app.winfo_rooty()+app.winfo_height()
        app.controls_canvas.yview_moveto(1);app.update()
        assert app.optimize_button.winfo_ismapped()
        app.set_model(cube);app.update();assert app.model.path==cube
        assert str(app.optimize_button['state'])=='normal'
        app.demo_model();app.update();assert app.model.path.name=='demo_payload.stl'
        app.demo_sensor();app.update();assert len(app.trials)==10;assert app.summary
        assert all('SYNTHETIC' in r['source'] for r in app.summary['variants'])
    finally:app.close()

@pytest.mark.gui
@pytest.mark.integration
def test_upload_optimize_export_through_gui(cube,tmp_path,monkeypatch):
    import time
    import tkinter as tk
    from AeroSlice.slicer import DEFAULT_EXECUTABLE
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('NOT TESTED: installed OrcaSlicer required')
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('NOT TESTED: graphical Tk session required')
    from AeroSlice.gui import Application
    from AeroSlice.main import make_examples
    from tkinter import filedialog,messagebox
    make_examples(tmp_path/'examples');app=Application(tmp_path/'examples')
    errors=[]
    monkeypatch.setattr(messagebox,'showerror',lambda *args,**kwargs:errors.append(args))
    monkeypatch.setattr(filedialog,'askopenfilename',lambda **kwargs:str(cube))
    app.config_path=tmp_path/'preferences.json';app.v['output'].set(str(tmp_path/'outputs'));app.v['candidate_count'].set('3')
    try:
        app.update();app.upload_button.invoke()
        deadline=time.monotonic()+10
        while app.busy and time.monotonic()<deadline:app.update();time.sleep(.02)
        assert not app.busy and app.model.path==cube
        app.optimize_button.invoke()
        deadline=time.monotonic()+40
        while app.busy and time.monotonic()<deadline:
            app.update();time.sleep(.03)
        assert not app.busy,'GUI slicing timed out'
        assert not errors,errors
        assert app.report['status']=='AUTOMATED SUCCESS',app.report['message']
        selected=next(c for c in app.report['candidates'] if c['id']==app.report['selected'])
        retention=min(selected['fea'][mesh]['relative_stiffness_ratio'] for mesh in ('coarse','refined'))
        assert app.metric_vars['stiffness'].get()==f'{100*retention:.1f}%'
        assert retention>=.95
        assert str(app.export_button['state'])=='normal'
        destination=tmp_path/'exported';destination.mkdir()
        monkeypatch.setattr(filedialog,'askdirectory',lambda **kwargs:str(destination))
        app.export_button.invoke();app.update()
        assert len(list(destination.rglob('optimized.gcode')))==1
        assert len(list(destination.rglob('baseline.gcode')))==1
        assert not list(destination.rglob('*.3mf'))
        assert not list(destination.rglob('payload.stl'))
        assert len(list(destination.rglob('report.json')))==1
        saved=next(destination.rglob('optimized.gcode'))
        assert saved.read_bytes()==Path(app.report['exports']['optimized.gcode']).read_bytes()
        assert app.print_export_folder==saved.parent
        opened=[]
        monkeypatch.setattr('AeroSlice.gui.subprocess.Popen',lambda command:opened.append(command))
        app.open_button.invoke()
        assert opened==[['open',str(saved.parent)]]
    finally:app.close()

@pytest.mark.gui
def test_close_cancels_worker_process(tmp_path):
    """Process lifecycle fixture; not a real slicer or hardware test."""
    import tkinter as tk
    import time
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('NOT TESTED: graphical Tk session required')
    from AeroSlice.gui import Application
    from AeroSlice.slicer import OrcaSlicer
    executable=tmp_path/'sleep_process';executable.write_text('#!/bin/sh\nexec sleep 10\n');executable.chmod(0o755)
    app=Application(tmp_path)
    slicer=OrcaSlicer(str(executable),cancel=app.cancel)
    app.worker(lambda:slicer.run([],tmp_path/'process.log'))
    time.sleep(.15);app.close();app.worker_thread.join(timeout=3)
    assert not app.worker_thread.is_alive()

@pytest.mark.gui
def test_beginner_controls_preserve_engineering_settings(tmp_path):
    import tkinter as tk
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('NOT TESTED: graphical Tk session required')
    from AeroSlice.gui import Application
    from AeroSlice.main import make_examples
    make_examples(tmp_path/'examples');app=Application(tmp_path/'examples')
    try:
        app.update()
        for key in ('optimization','printer','live','stiffness'):
            assert not app.sections[key]['body'].winfo_ismapped()
        before=app.settings()
        assert str(app.stiffness_target_entry['state'])=='normal'
        app.v['fea_retention'].set('96');assert app.fea_settings().minimum_stiffness_ratio==.96
        app.retention_presets[1].invoke();assert app.fea_settings().minimum_stiffness_ratio==.98
        app.retention_scale.set(97);assert app.fea_settings().minimum_stiffness_ratio==.97
        app.v['fea_retention'].set('95')
        app.navigation[2].invoke();app.update();assert app.book.index(app.book.select())==2
        app.navigation[0].invoke();app.update();assert app.book.index(app.book.select())==0
        app.search_choice.set('Quick · 3 designs');app.search_box.event_generate('<<ComboboxSelected>>');app.update()
        after=app.settings()
        assert after.candidate_count==3
        assert after.baseline==before.baseline and after.protected_minima==before.protected_minima and after.coverage==before.coverage
        app.toggle_section('optimization');app.update()
        assert app.sections['optimization']['body'].winfo_ismapped()
        app.v['candidate_count'].set('10');assert app.search_choice.get()=='Custom · 10 designs'
        app.toggle_section('optimization');app.update()
        assert not app.sections['optimization']['body'].winfo_ismapped()
        app.demo_model();app.update();assert app.upload_button['text']=='Replace model…'
        app.set_preview_view(90,-90);assert app.figures['preview'].figure.axes[0].elev==90
        from types import SimpleNamespace
        axis=app.figures['preview'].figure.axes[0];span=axis.get_xlim()[1]-axis.get_xlim()[0]
        app.zoom_preview(SimpleNamespace(inaxes=axis,button='up'))
        assert axis.get_xlim()[1]-axis.get_xlim()[0]<span
        app.metric_vars['before'].set('old estimate');app.demo_model();app.update()
        assert app.metric_vars['before'].get()=='—'
        assert 'model' in app.next_step.get().lower() or 'ready' in app.next_step.get().lower()
    finally:app.close()

@pytest.mark.gui
def test_material_dropdown_maps_to_installed_profile(tmp_path):
    import tkinter as tk
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('NOT TESTED: graphical Tk session required')
    from AeroSlice.gui import Application
    app=Application(tmp_path)
    try:
        app.update()
        if 'PETG · standard profile' not in app.material_profiles:
            pytest.skip('NOT TESTED: installed Flashforge PETG profile required for selector test')
        app.material_choice.set('PETG · standard profile');app.material_box.event_generate('<<ComboboxSelected>>');app.update()
        assert app.profiles().filament=='Flashforge Generic PETG'
        assert 'PETG' in app.setup_summary.get()
        app.v['filament_profile'].set('Custom material profile');app.update()
        assert app.material_choice.get()=='Custom · Custom material profile'
    finally:app.close()

@pytest.mark.gui
@pytest.mark.integration
def test_fea_mode_requires_load_and_exports_simulation_reports(cube,tmp_path,monkeypatch):
    import tkinter as tk
    import time
    from tkinter import messagebox,filedialog
    from AeroSlice.gui import Application
    from AeroSlice.main import make_examples
    from AeroSlice.slicer import DEFAULT_EXECUTABLE
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('Graphical Tk session required')
    make_examples(tmp_path/'examples');app=Application(tmp_path/'examples');errors=[]
    monkeypatch.setattr(messagebox,'showerror',lambda *args,**kwargs:errors.append(args))
    app.config_path=tmp_path/'config.json'
    try:
        app.set_model(cube);app.v['simulation_mode'].set('FEA direct search (experimental)');app.update_actions()
        assert app.sections['simulation']['open']
        app.start_optimization()
        assert errors and not app.busy and app.report is None
        errors.clear();app.v['fea_force'].set('1');app.v['fea_mesh'].set('4');app.v['fea_tolerance'].set('.5')
        app.v['protected'].set('10,10,10');app.v['maximum'].set('20');app.config_data['density_set']=[10,20]
        app.v['candidate_count'].set('3');app.v['output'].set(str(tmp_path/'work'))
        app.start_optimization();deadline=time.monotonic()+60
        while app.busy and time.monotonic()<deadline:
            app.update();time.sleep(.03)
        assert not app.busy and not errors,errors
        assert app.report['status']=='AUTOMATED SUCCESS',app.report['message']
        assert 'PHYSICALLY UNVALIDATED' in app.dashboard_text.get('1.0','end')
        destination=tmp_path/'exports';destination.mkdir()
        monkeypatch.setattr(filedialog,'askdirectory',lambda **kwargs:str(destination))
        app.export_button.invoke();app.update()
        assert len(list(destination.rglob('optimized.gcode')))==1
        assert len(list(destination.rglob('fea_selected.png')))==1
        assert len(list(destination.rglob('fea_search.csv')))==1
        assert not list(destination.rglob('*.3mf'))
    finally:app.close()

@pytest.mark.gui
@pytest.mark.integration
def test_weight_first_fea_comparison_without_thrust(cube,tmp_path,monkeypatch):
    import tkinter as tk
    import time
    from tkinter import messagebox
    from AeroSlice.gui import Application
    from AeroSlice.main import make_examples
    from AeroSlice.slicer import DEFAULT_EXECUTABLE
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    try:
        probe=tk.Tk();probe.destroy()
    except tk.TclError:pytest.skip('Graphical Tk session required')
    make_examples(tmp_path/'examples');app=Application(tmp_path/'examples');errors=[]
    monkeypatch.setattr(messagebox,'showerror',lambda *args,**kwargs:errors.append(args))
    app.config_path=tmp_path/'config.json'
    try:
        assert app.v['simulation_mode'].get()=='Weight saving + FEA comparison'
        assert app.fea_settings().minimum_stiffness_ratio==.95
        app.set_model(cube);app.v['simulation_mode'].set('Weight saving + FEA comparison');app.update_actions()
        assert app.v['fea_force'].get()==''
        assert not app.sections['simulation_absolute']['open']
        assert app.fea_settings().assessment=='relative_stiffness'
        assert app.fea_settings().force_n==1
        app.v['fea_mesh'].set('4');app.v['fea_tolerance'].set('.5');app.v['fea_retention'].set('50')
        app.v['protected'].set('10,10,10');app.v['maximum'].set('20');app.config_data['density_set']=[10,20]
        app.v['candidate_count'].set('3');app.v['output'].set(str(tmp_path/'work'));app.start_optimization()
        deadline=time.monotonic()+60
        while app.busy and time.monotonic()<deadline:
            app.update();time.sleep(.03)
        assert not app.busy and not errors,errors
        assert app.report['status']=='AUTOMATED SUCCESS',app.report['message']
        assert app.report['fea_selected']['relative_stiffness_ratio']>=.5
        assert 'actual thrust is not an input' in app.dashboard_text.get('1.0','end')
        assert app.report['savings_percent_estimated']>0
    finally:app.close()

@pytest.mark.gui
def test_responsive_workspace_and_preview(cube,tmp_path):
    """Exercise real widget bounds, scrolling and camera reset at supported sizes."""
    from AeroSlice.gui import Application
    app=Application(tmp_path)
    try:
        app.overrideredirect(True)  # Test sizes beyond the physical screen's work area.
        app.set_model(cube)
        for width,height in ((1280,800),(1440,900),(1664,919),(1920,1080),(800,700)):
            app.geometry(f'{width}x{height}+0+0');app.update()
            assert app.stacked_workspace==(width<1120)
            for widget in (app.optimize_button,app.export_button):
                assert widget.winfo_rooty()+widget.winfo_height()<=app.winfo_rooty()+height
                assert widget.winfo_x()+widget.winfo_width()<=widget.master.winfo_width()
            for widget in (app.material_box,app.simulation_box,app.stiffness_target_entry):
                assert widget.winfo_x()+widget.winfo_width()<=widget.master.winfo_width()
            assert app.preview_container.winfo_height()>=250
            if not app.stacked_workspace:
                assert not app.optimize_page.bar.winfo_ismapped()
            else:
                assert not app.settings_scroll.bar.winfo_ismapped()
        app.geometry('1280x800+0+0');app.update()
        app.toggle_section('optimization');app.toggle_section('simulation');app.toggle_section('printer');app.update()
        assert app.settings_scroll.bar.winfo_ismapped()
        app.settings_scroll.canvas.yview_moveto(1);app.update()
        field=app.v['output']
        assert field.get()
        assert app.optimize_button.winfo_ismapped()
        app.set_preview_view(90,-90);app.fit_preview()
        assert app.figures['preview'].figure.axes[0].elev==25
        assert not app.progress_bar.winfo_ismapped()
        from threading import Event
        finished=Event()
        app.worker(lambda:finished.wait(timeout=2))
        app.update();assert app.progress_bar.winfo_ismapped()
        assert str(app.optimize_button['state'])=='disabled'
        finished.set();app.worker_thread.join(timeout=2)
    finally:app.close()

@pytest.mark.gui
def test_success_status_does_not_enable_missing_export_files(tmp_path):
    from AeroSlice.gui import Application
    app=Application(tmp_path)
    try:
        app.report={'status':'AUTOMATED SUCCESS','exports':{'optimized.gcode':str(tmp_path/'missing.gcode')}}
        app.update_actions();app.update()
        assert str(app.export_button['state'])=='disabled'
        assert 'ready to save' in app.export_status.get()
        assert 'No optimized' in app.export_status.get()
    finally:app.close()
