from pathlib import Path
import pytest

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
        app.update();app.upload_button.invoke();app.update();assert app.model.path==cube
        app.optimize_button.invoke()
        deadline=time.monotonic()+40
        while app.busy and time.monotonic()<deadline:
            app.update();time.sleep(.03)
        assert not app.busy,'GUI slicing timed out'
        assert not errors,errors
        assert app.report['status']=='AUTOMATED SUCCESS',app.report['message']
        assert str(app.export_button['state'])=='normal'
        destination=tmp_path/'exported';destination.mkdir()
        monkeypatch.setattr(filedialog,'askdirectory',lambda **kwargs:str(destination))
        app.export_button.invoke();app.update()
        assert len(list(destination.rglob('optimized.gcode')))==1
        assert len(list(destination.rglob('optimized.3mf')))==1
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
