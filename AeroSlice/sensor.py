"""ESP32 serial measurement only; deliberately exposes no motor commands."""
from __future__ import annotations
from threading import Thread, Event
from queue import Queue
import csv
import math
import io
import serial
from serial.tools import list_ports
from .analysis import Reading


def ports() -> list[str]: return [p.device for p in list_ports.comports()]

class Recorder:
    def __init__(self):
        self.messages=Queue();self.serial=None;self.stop_event=Event();self.thread=None
        self.recording=False;self.trial_id='1';self.variant='baseline';self.readings=[]

    def connect(self, port: str, baud: int = 115200) -> None:
        self.disconnect()
        self.serial=serial.Serial(port,baud,timeout=.2)
        self.stop_event.clear();self.thread=Thread(target=self._read,daemon=True);self.thread.start()

    def configure(self, trigger_pin: int, echo_pin: int, interval_ms: int) -> None:
        if not self.serial: raise ValueError('Connect a serial port first.')
        if self.recording: raise ValueError('Stop recording before configuring the sensor.')
        if trigger_pin==echo_pin or not 0<=trigger_pin<=39 or not 0<=echo_pin<=39 or not 60<=interval_ms<=60000:
            raise ValueError('Use distinct GPIO numbers 0–39 and an interval of 60–60000 ms; firmware verifies supported pins.')
        self.serial.write(f'PINS {trigger_pin} {echo_pin}\nINTERVAL {interval_ms}\n'.encode('ascii'))

    def start(self, trial_id: str, variant: str) -> None:
        if not self.serial: raise ValueError('Connect a serial port first.')
        if self.recording:raise ValueError('Stop the current recording before starting another trial.')
        if not trial_id.strip() or not variant.strip(): raise ValueError('Enter trial ID and variant.')
        self.serial.write(b'START\n')
        self.trial_id=trial_id;self.variant=variant;self.recording=True

    def stop(self) -> None:
        self.recording=False
        if self.serial and self.serial.is_open: self.serial.write(b'STOP\n')

    def disconnect(self) -> None:
        if self.serial:
            try: self.stop()
            except (OSError,serial.SerialException): pass
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=1)
        if self.serial: self.serial.close()
        self.serial=None;self.thread=None

    def _read(self) -> None:
        while not self.stop_event.is_set():
            try:
                line=self.serial.readline().decode('utf-8',errors='replace').strip()
                if not line: continue
                values=next(csv.reader(io.StringIO(line)))
                if values[0]=='timestamp_ms' or line.startswith('#'):
                    self.messages.put(('error' if line.startswith('# ERROR') else 'info',line));continue
                ts=float(values[0]);distance=float(values[1]) if len(values)>1 and values[1] else None
                if not math.isfinite(ts) or ts<0: raise ValueError('invalid timestamp')
                error=values[2] if len(values)>2 else ''
                raw_distance=values[1] if len(values)>1 else ''
                if distance is not None and (not math.isfinite(distance) or distance<=0): error='INVALID_DISTANCE';distance=None
                row=Reading(ts,distance,self.trial_id,self.variant,'MEASURED (ESP32 serial)',error,raw_distance)
                if self.recording: self.readings.append(row)
                self.messages.put(('reading',row))
            except (ValueError,IndexError) as exc: self.messages.put(('error',f'Invalid sensor record: {exc}'))
            except (OSError,serial.SerialException) as exc:
                self.messages.put(('error',str(exc)));self.recording=False;break
