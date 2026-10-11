"""Real pyserial I/O against a POSIX pseudo-terminal, not physical ESP32 testing."""
import os
import time
from types import SimpleNamespace
import pytest
import serial
from AeroSlice.sensor import Recorder


def test_serial_recording_and_firmware_errors():
    if os.name!='posix':pytest.skip('POSIX pseudo-terminal required')
    import pty
    master,slave=pty.openpty();recorder=Recorder()
    try:
        recorder.connect(os.ttyname(slave));recorder.configure(25,26,100)
        recorder.start('demo-trial','baseline')
        with pytest.raises(ValueError,match='Stop the current'):recorder.start('duplicate','baseline')
        os.write(master,b'timestamp_ms,distance_mm,error\n0,800,\n100,,TIMEOUT\n200,nan,\n300,770,\n# ERROR: unsupported pin pair\n')
        deadline=time.monotonic()+3
        while len(recorder.readings)<4 and time.monotonic()<deadline:time.sleep(.02)
        assert len(recorder.readings)==4
        assert recorder.readings[0].distance_mm==800
        assert recorder.readings[1].distance_mm is None and recorder.readings[1].error=='TIMEOUT'
        assert recorder.readings[2].raw_distance=='nan' and recorder.readings[2].error=='INVALID_DISTANCE'
        assert all(r.trial_id=='demo-trial' and r.variant=='baseline' for r in recorder.readings)
        time.sleep(.05);messages=[]
        while not recorder.messages.empty():messages.append(recorder.messages.get_nowait())
        assert any(kind=='error' and 'unsupported pin' in value for kind,value in messages if kind!='reading')
        recorder.stop();assert not recorder.recording
        os.write(master,b'400,760,\n');time.sleep(.3)
        assert len(recorder.readings)==4
        thread=recorder.thread;recorder.disconnect()
        assert not thread.is_alive() and recorder.serial is None
    finally:recorder.disconnect();os.close(master);os.close(slave)


def test_failed_start_command_does_not_mark_recording_active():
    recorder=Recorder()
    def fail_write(_):raise serial.SerialException('Disconnected test port')
    recorder.serial=SimpleNamespace(write=fail_write)
    with pytest.raises(serial.SerialException):recorder.start('1','baseline')
    assert not recorder.recording
