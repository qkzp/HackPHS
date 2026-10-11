# Sensor firmware — hardware integration NOT TESTED

Open `esp32_ultrasonic.ino` in Arduino IDE and select the actual ESP32 board.
This sketch targets classic ESP32 GPIO numbering and a pulse/echo sensor.
The sensor model has not been confirmed. Other protocols/boards need a verified adapter.
No Arduino toolchain or physical sensor was available during software testing.

Commands are `START`, `STOP`, `PINS 25 26`, and `INTERVAL 60` (milliseconds).
Stop before changing configuration. A new START resets the elapsed trial timestamp.
Serial output is `timestamp_ms,distance_mm,error` at 115200 baud.
Timeouts leave distance blank. The desktop app adds trial ID, variant and provenance.

For HC-SR04 only: the module data sheet specifies a 10 µs trigger and recommends
more than 60 ms between measurements. Supply and operating range must be verified
for the actual module. Use an interval greater than 60 ms if required; 100 ms is a
conservative starting point. A standard HC-SR04 uses a 5 V supply/echo. Do not
connect its echo directly to an ESP32 GPIO: use an appropriate resistor divider
or level shifter and a common ground. Verify wiring with an adult supervisor.
The sketch's 343 m/s conversion is an approximate room-temperature sound speed;
calibrate against known distances and temperature before experimental conclusions.
The 30 ms timeout is configurable only in the firmware build and is not a claim of
sensor range. Enter verified reliable min/max distances in AeroSlice.

Sources:
- [HC-SR04 module data sheet](https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf)
- [ESP32 series data sheet](https://documentation.espressif.com/esp32_datasheet_en.pdf)

Use a secured frame, protected moving components, enclosed payload and accessible
external power cutoff. Motor activation is always separate. Physical testing
requires appropriate precautions and adult supervision.
