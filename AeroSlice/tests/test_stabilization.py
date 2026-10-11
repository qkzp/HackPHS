"""Regressions discovered during the live-demo audit; no simulated slicer success."""
from pathlib import Path
from threading import Event
import time
import numpy as np
import pytest
from AeroSlice.analysis import Reading, process_trials, summarize
from AeroSlice.structural import Settings


@pytest.fixture
def app(tmp_path,monkeypatch):
    from tkinter import messagebox
    from AeroSlice.gui import Application
    from AeroSlice.main import make_examples
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    monkeypatch.setattr('AeroSlice.gui.ports',lambda:[])
    monkeypatch.setattr(messagebox,'showerror',lambda *a,**k:None)
    make_examples(tmp_path/'examples')
    instance=Application(tmp_path/'examples')
    yield instance
    instance.close()


@pytest.mark.gui
def test_new_model_clears_all_model_results(app):
    app.demo_model();app.demo_sensor();app.update()
    app.details['weighed_objects']={'baseline_payload_g_measured':10}
    app.v['measured_baseline'].set('10');app.v['force'].set('1')
    assert app.trials and app.summary and 'comparison' in app.figures
    app.demo_model();app.update()
    assert not app.trials and not app.readings and app.summary is None
    assert not app.details
    assert 'comparison' not in app.figures and 'motion' not in app.figures
    assert not app.v['measured_baseline'].get() and not app.v['force'].get()
    assert not app.trial_box.get()


@pytest.mark.gui
def test_invalid_sensor_processing_cannot_analyze_previous_trials(app):
    app.demo_sensor();assert app.summary
    app.v['sensor_min'].set('1000');app.v['sensor_max'].set('100')
    app.analyze_sensor();app.update()
    assert app.summary is None and not app.trials
    assert 'comparison' not in app.figures and 'motion' not in app.figures
    assert 'climb_analysis' not in app.details
    assert 'CLIMB TEST COMPARISON' not in app.dashboard_text.get('1.0','end')


@pytest.mark.gui
def test_invalid_csv_upload_clears_previous_measurements(app,tmp_path,monkeypatch):
    from tkinter import filedialog
    app.demo_sensor();assert app.trials and app.summary
    path=tmp_path/'bad.csv';path.write_text('wrong,columns\n1,2\n')
    monkeypatch.setattr(filedialog,'askopenfilename',lambda **k:str(path))
    app.upload_sensor();app.update()
    assert not app.readings and not app.trials and app.summary is None
    assert 'comparison' not in app.figures and 'motion' not in app.figures
    assert 'CLIMB TEST COMPARISON' not in app.dashboard_text.get('1.0','end')


@pytest.mark.gui
def test_empty_serial_port_list_still_has_working_selector(app,monkeypatch):
    from tkinter import ttk
    assert isinstance(app.port_box,ttk.Combobox)
    monkeypatch.setattr('AeroSlice.gui.ports',lambda:['test-port'])
    app.refresh_ports()
    assert tuple(app.port_box['values'])==('test-port',)


@pytest.mark.gui
def test_manual_recommendations_work_with_default_fea_mode(app,tmp_path):
    app.demo_model();app.manual.set(True);app.v['output'].set(str(tmp_path/'manual'))
    app.start_optimization();deadline=time.monotonic()+10
    while app.busy and time.monotonic()<deadline:app.update();time.sleep(.02)
    assert app.report and app.report['status']=='SEMIAUTOMATED'
    assert app.report['manual_steps'] and not app.report['exports']
    assert app.motion_panel.result is None


@pytest.mark.gui
@pytest.mark.parametrize('mode',['Weight saving','Weight saving + FEA comparison'])
def test_engineering_recommendations_are_unsliced_and_cleared_with_model(app,mode):
    app.demo_model();app.v['simulation_mode'].set(mode)
    app.generate();deadline=time.monotonic()+10
    while app.busy and time.monotonic()<deadline:app.update();time.sleep(.02)
    assert not app.busy and app.report is None
    preview=app.details['candidate_preview']
    assert preview['candidates'] and all('metrics' not in c for c in preview['candidates'])
    assert any(k.startswith('heatmap_') for k in app.figures)
    assert str(app.export_button['state'])=='disabled'
    app.demo_model();app.update()
    assert 'candidate_preview' not in app.details
    assert not any(k.startswith('heatmap_') for k in app.figures)


@pytest.mark.gui
@pytest.mark.integration
def test_printer_setup_check_does_not_replace_uploaded_model_or_report(app,tmp_path):
    from AeroSlice.slicer import DEFAULT_EXECUTABLE
    from AeroSlice.model import write_stl
    from test_motion_simulation import shape_mesh
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    path=tmp_path/'irregular.stl';write_stl(path,shape_mesh('irregular'));app.set_model(path)
    previous={'status':'FAILED','input_sha256':app.model.sha256,'message':'Previous model-specific result'}
    app.report=previous;sha=app.model.sha256
    app.v['output'].set(str(tmp_path/'checks'));app.check_compatibility()
    deadline=time.monotonic()+30
    while app.busy and time.monotonic()<deadline:app.update();time.sleep(.02)
    assert not app.busy
    assert app.details['printer_check']['status']=='AUTOMATED SUCCESS'
    assert app.model.path==path and app.model.sha256==sha and app.report is previous
    assert app.motion_panel.result is None


@pytest.mark.gui
def test_new_optimization_clears_previous_report_and_metrics(app,tmp_path,monkeypatch):
    app.demo_model();app.report={'status':'FAILED','input_sha256':app.model.sha256}
    app.metric_vars['before'].set('old');app.v['output'].set(str(tmp_path/'runs'))
    started=Event();done=Event()
    def hold(*args,**kwargs):started.set();done.wait(2);raise ValueError('Intentional failure-path test')
    monkeypatch.setattr('AeroSlice.gui.optimize',hold)
    try:
        app.start_optimization();assert started.wait(1)
        assert app.report is None
        assert all(v.get()=='—' for v in app.metric_vars.values())
        app.start_optimization()  # Programmatic/shortcut re-entry must not create another worker.
        first=app.worker_thread;app.worker(lambda:None);assert app.worker_thread is first
    finally:done.set();app.worker_thread.join(3)


@pytest.mark.gui
@pytest.mark.parametrize('cancelled',[False,True])
def test_upload_runs_mesh_import_off_ui_thread(app,cube,monkeypatch,cancelled):
    from tkinter import filedialog
    from AeroSlice.file_import import load_model
    started=Event();release=Event();heartbeat=[]
    def slow_load(path):
        started.set();release.wait(3);return load_model(path)
    monkeypatch.setattr('AeroSlice.gui.load_model',slow_load)
    monkeypatch.setattr(filedialog,'askopenfilename',lambda **k:str(cube))
    app.upload_model()
    try:
        assert started.wait(1) and app.busy
        app.after(1,lambda:heartbeat.append(True))
        for _ in range(10):app.update();time.sleep(.01)
        assert heartbeat and app.model is None
        assert 'Loading' in app.next_step.get()
        if cancelled:app.cancel_button.invoke()
    finally:release.set()
    deadline=time.monotonic()+5
    while app.busy and time.monotonic()<deadline:app.update();time.sleep(.02)
    assert not app.busy
    if cancelled:assert app.model is None and 'cancelled' in app.status.get()
    else:assert app.model.path==cube


@pytest.mark.gui
@pytest.mark.parametrize('failure',['missing','copy_error'])
def test_incomplete_gcode_export_leaves_no_partial_folder(app,tmp_path,monkeypatch,failure):
    from tkinter import filedialog
    app.demo_model();source=tmp_path/'run';source.mkdir()
    base=source/'baseline.gcode';base.write_text('partial export fixture')
    selected=source/'optimized.gcode'
    if failure=='copy_error':
        import shutil
        selected.write_text('copy failure fixture');copy=shutil.copy2
        def fail_copy(src,dst):
            if Path(dst).name=='optimized.gcode':raise OSError('copy failure')
            return copy(src,dst)
        monkeypatch.setattr('AeroSlice.gui.shutil.copy2',fail_copy)
    app.report={'status':'AUTOMATED SUCCESS','input_sha256':app.model.sha256,
                'run_directory':str(source),'exports':{'baseline.gcode':str(base),'optimized.gcode':str(selected)}}
    destination=tmp_path/'export';destination.mkdir()
    monkeypatch.setattr(filedialog,'askdirectory',lambda **k:str(destination))
    app.export_files();app.update()
    assert not list(destination.iterdir())
    if failure=='missing':assert str(app.export_button['state'])=='disabled'
    assert ('missing or empty' if failure=='missing' else 'copy failure') in app.status.get()


@pytest.mark.gui
def test_corrupted_preferences_do_not_crash_startup(tmp_path,monkeypatch):
    from AeroSlice.gui import Application
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    config=tmp_path/'Library/Application Support/AeroSlice/config.json';config.parent.mkdir(parents=True)
    for content in ('[]','{"orca_executable":null,"sensor_baud":"bad","slicer_timeout_s":-1}'):
        config.write_text(content);instance=Application(tmp_path)
        try:
            assert instance.config_data['sensor_baud']==115200
            assert instance.config_data['slicer_timeout_s']==180
            assert isinstance(instance.config_data['orca_executable'],str)
        finally:instance.close()


@pytest.mark.parametrize('missing',['candidates','densities','settings'])
def test_incomplete_motion_report_shows_useful_error(cube,missing):
    from AeroSlice.file_import import load_model
    from AeroSlice.motion_simulation import compare_motion,MotionSettings
    from test_motion_simulation import completed_report
    model=load_model(cube);report=completed_report(model)
    if missing=='densities':report['candidates'][1].pop('densities')
    else:report.pop(missing)
    with pytest.raises(ValueError):compare_motion(model,report,MotionSettings())


@pytest.mark.integration
def test_slice_failure_does_not_keep_partial_print_exports(cube,tmp_path,monkeypatch):
    import shutil
    from AeroSlice.slicer import OrcaSlicer,Profiles,DEFAULT_EXECUTABLE
    from AeroSlice.optimizer import optimize
    from AeroSlice.structural import weight_saving_settings
    from AeroSlice.file_import import load_model
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    copy=shutil.copy2
    def fail_copy(src,dst):
        if Path(dst).name=='optimized.gcode':raise OSError('Deliberate final export failure')
        return copy(src,dst)
    monkeypatch.setattr('AeroSlice.optimizer.shutil.copy2',fail_copy)
    report=optimize(load_model(cube),weight_saving_settings(candidate_count=3),tmp_path/'runs',OrcaSlicer(),Profiles())
    assert report['status']=='FAILED' and 'final export failure' in report['message']
    assert not report['exports']
    assert not (Path(report['run_directory'])/'baseline.gcode').exists()
    assert not (Path(report['run_directory'])/'optimized.gcode').exists()
    assert report['candidates'][0]['metrics']['mass_g']>0


@pytest.mark.parametrize('timeout',[float('nan'),float('inf'),0,-1])
def test_invalid_slicer_timeout_rejected(timeout):
    from AeroSlice.slicer import OrcaSlicer
    with pytest.raises(ValueError):OrcaSlicer(timeout=timeout)


@pytest.mark.parametrize('changes',[{'contact_axis':'xy'},{'load_axis':''},{'rotation':(0,0)},{'candidate_count':3.5}])
def test_malformed_settings_are_rejected_before_work(changes):
    with pytest.raises(ValueError):Settings(**changes).validate()


@pytest.mark.parametrize('n',[3,40,100])
def test_student_t_interval_uses_actual_degrees_of_freedom(n):
    from scipy.stats import t
    times=np.linspace(1,3,n)
    trials=process_trials([Reading(ms,d,str(i),'baseline') for i,seconds in enumerate(times)
                          for ms,d in ((0,800),(seconds*1000,200))])
    row=summarize(trials,750,300,max_gap_s=4)['variants'][0]
    elapsed=times*.75
    delta=t.ppf(.975,n-1)*elapsed.std(ddof=1)/np.sqrt(n)
    assert row['mean_time_95_ci_s']==pytest.approx([elapsed.mean()-delta,elapsed.mean()+delta],rel=1e-10)
