"""Live mesh rendering/state tests plus a real STL→slice→simulation→export run."""
from pathlib import Path
import time
import numpy as np
import pytest
from AeroSlice.model import write_stl
from AeroSlice.main import make_examples
from AeroSlice.gui import Application
from AeroSlice.motion_simulation import compare_motion,MotionSettings
from test_motion_simulation import shape_mesh,completed_report


@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    make_examples(tmp_path/'examples')
    instance=Application(tmp_path/'examples')
    yield instance
    instance.close()


def finish(app):
    deadline=time.monotonic()+10
    while app.motion_panel.result is None and time.monotonic()<deadline:
        app.update();time.sleep(.02)
    assert app.motion_panel.result is not None,app.motion_panel.state.get()
    app.motion_panel.stop_playback()


@pytest.mark.gui
@pytest.mark.parametrize('size',('800x700','1440x900'))
def test_simulation_page_is_visible_through_navigation(app,size):
    app.geometry(size)
    app.navigation[1].invoke();app.update()
    panel=app.motion_panel
    assert panel.winfo_ismapped(), 'Simulation panel must be placed inside its notebook page'
    assert panel.winfo_width()>600 and panel.winfo_height()>400
    assert panel.waiting_label.winfo_ismapped()
    assert panel.play_button.winfo_ismapped()
    app.demo_model();app.report=completed_report(app.model)
    panel.refresh();finish(app);app.update()
    canvas=app.figures['motion_3d'].get_tk_widget()
    assert canvas.winfo_ismapped()
    assert canvas.winfo_width()>500 and canvas.winfo_height()>250
    app.navigation[0].invoke();app.update()
    assert not panel.winfo_ismapped()
    app.navigation[1].invoke();app.update()
    assert panel.winfo_ismapped() and canvas.winfo_ismapped()


@pytest.mark.gui
def test_actual_uploaded_meshes_and_replacement(app,tmp_path):
    # Fixture masses isolate UI behavior; the integration test below uses Orca.
    app.navigation[1].invoke();app.update()
    assert str(app.motion_panel.play_button['state'])=='disabled'
    for name in ('cube','prism','cylinder','sphere','irregular'):
        path=tmp_path/(name+'.stl');write_stl(path,shape_mesh(name))
        app.set_model(path);app.update()
        assert app.motion_panel.result is None and 'motion_3d' not in app.figures
        assert all(value.get()=='—' for value in app.motion_panel.metrics.values())
        app.report=completed_report(app.model)
        app.motion_panel.refresh();finish(app)
        result=app.motion_panel.result
        assert name in app.motion_panel.state.get()
        assert len(app.motion_panel.visual_triangles)==len(app.model.triangles)
        assert result['cases'][0]['densities']==(15,15,15)
        # Both displayed collections contain every uploaded facet after a shared
        # visual affine transform; only vertical trajectory displacement differs.
        app.motion_panel.set_playback_time(.3)
        for case,artist in zip(('original','optimized'),app.motion_panel.artists):
            expected=app.motion_panel.visual_triangles.copy()
            height=np.interp(.3,result[case]['time_s'],result[case]['height_m'])
            expected[:,:,2]+=height
            faces=artist._faces if hasattr(artist,'_faces') else artist._vec[:3,:].T.reshape(-1,3,3)
            np.testing.assert_allclose(faces,expected)
        app.motion_panel.inputs['fixed_mass_g'].set('120')
        assert app.motion_panel.result is None
        finish(app)
        assert app.motion_panel.result['original']['total_mass_g']==130
        app.motion_panel.inputs['fixed_mass_g'].set('100')
        if app.motion_panel.input_timer:
            app.motion_panel.after_cancel(app.motion_panel.input_timer);app.motion_panel.input_timer=None
    # Failed/unsliced and stale report states never keep an older animation.
    app.report=None;app.motion_panel.refresh();app.update()
    assert app.motion_panel.result is None and 'motion_3d' not in app.figures
    assert all(value.get()=='—' for value in app.motion_panel.metrics.values())


@pytest.mark.gui
def test_busy_invalid_and_stale_computation_states(app,tmp_path):
    app.demo_model();app.report=completed_report(app.model)
    app.busy=True;app.motion_panel.refresh()
    assert app.motion_panel.result is None and 'Waiting' in app.motion_panel.state.get()
    app.busy=False;app.motion_panel.inputs['thrust_n'].set('invalid');app.motion_panel.refresh()
    assert app.motion_panel.result is None and 'numeric' in app.motion_panel.state.get()
    app.motion_panel.inputs['thrust_n'].set('1.5');app.motion_panel.refresh()
    generation=app.motion_panel.generation
    old=compare_motion(app.model,app.report,MotionSettings())
    path=tmp_path/'replacement.stl';write_stl(path,shape_mesh('irregular'));app.set_model(path)
    app.motion_panel.queue.put((generation,old,None));app.motion_panel.poll()
    assert app.motion_panel.result is None and app.report is None


@pytest.mark.gui
@pytest.mark.integration
def test_irregular_upload_slice_simulation_and_export(app,tmp_path,monkeypatch):
    from AeroSlice.slicer import DEFAULT_EXECUTABLE
    from tkinter import messagebox,filedialog
    if not Path(DEFAULT_EXECUTABLE).is_file():pytest.skip('Installed OrcaSlicer required')
    errors=[];monkeypatch.setattr(messagebox,'showerror',lambda *a,**k:errors.append(a))
    path=tmp_path/'irregular.stl';write_stl(path,shape_mesh('irregular'));original=path.read_bytes()
    app.set_model(path);app.v['simulation_mode'].set('Weight saving');app.v['candidate_count'].set('3');app.v['output'].set(str(tmp_path/'runs'))
    app.start_optimization();deadline=time.monotonic()+60
    while app.busy and time.monotonic()<deadline:app.update();time.sleep(.02)
    assert not app.busy and not errors,errors
    assert app.report['status']=='AUTOMATED SUCCESS',app.report['message']
    app.navigation[1].invoke();finish(app)
    result=app.motion_panel.result
    assert result['original']['payload_mass_g']==app.report['candidates'][0]['metrics']['mass_g']
    assert result['climb_time_reduction_percent']>0
    assert path.read_bytes()==original
    destination=tmp_path/'export';destination.mkdir()
    monkeypatch.setattr(filedialog,'askdirectory',lambda **kwargs:str(destination))
    app.export_files();app.update()
    assert len(list(destination.rglob('optimized.gcode')))==1
    assert len(list(destination.rglob('baseline.gcode')))==1
    assert not list(destination.rglob('*.3mf'))
