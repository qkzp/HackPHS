/* AeroSlice sensor-only firmware. No motor or propeller control.
 * Example pulse/echo interface, conditional HC-SR04 compatibility.
 * Verify the actual sensor, pins and ESP32 board before wiring.
 * A 5 V HC-SR04 echo MUST be reduced to a safe 3.3 V GPIO level.
 * Configure pins/interval via serial; no source edits needed for normal setup.
 * Commands: START, STOP, PINS <trigger> <echo>, INTERVAL <ms>.
 */
#include <Arduino.h>
int triggerPin = 25;
int echoPin = 26;
unsigned long intervalMs = 100;
unsigned long lastMeasurement = 0;
unsigned long trialStart = 0;
const unsigned long pulseTimeoutUs = 30000;
bool recording = false;
String command;

bool safePin(int pin, bool output) {
  // Classic ESP32 exposed GPIOs; exclude flash, strapping and input-only TX pins.
  const int safe[] = {4,13,14,16,17,18,19,21,22,23,25,26,27,32,33};
  for (int p : safe) if (pin == p) return true;
  if (!output && (pin == 34 || pin == 35 || pin == 36 || pin == 39)) return true;
  return false;
}
void configurePins() {
  pinMode(triggerPin, OUTPUT); digitalWrite(triggerPin, LOW); pinMode(echoPin, INPUT);
}
void execute(String line) {
  line.trim(); line.toUpperCase();
  if (line == "START") {
    trialStart = millis(); lastMeasurement = millis() - intervalMs; recording = true;
    Serial.println("timestamp_ms,distance_mm,error");
  } else if (line == "STOP") { recording = false; Serial.println("# STOPPED"); }
  else if (line.startsWith("INTERVAL ")) {
    long value = line.substring(9).toInt();
    if (recording) Serial.println("# ERROR: STOP before configuration");
    else if (value < 60 || value > 60000) Serial.println("# ERROR: interval must be 60..60000 ms");
    else { intervalMs = value; Serial.println("# INTERVAL UPDATED"); }
  } else if (line.startsWith("PINS ")) {
    int a=-1,b=-1;
    if (recording) Serial.println("# ERROR: STOP before configuration");
    else if (sscanf(line.c_str(),"PINS %d %d",&a,&b)!=2 || a==b || !safePin(a,true) || !safePin(b,false)) Serial.println("# ERROR: unsupported pin pair for classic ESP32");
    else { pinMode(triggerPin,INPUT); triggerPin=a; echoPin=b; configurePins(); Serial.println("# PINS UPDATED"); }
  } else Serial.println("# ERROR: commands START, STOP, PINS <trigger> <echo>, INTERVAL <ms>");
}
void setup() {
  Serial.begin(115200); configurePins();
  Serial.println("# AeroSlice sensor only. Verify sensor, board, echo voltage and operating range.");
}
void loop() {
  while (Serial.available()) {
    char c=Serial.read();
    if (c=='\n') { execute(command); command=""; }
    else if (c!='\r') {
      if (command.length()<100) command+=c;
      else { command=""; Serial.println("# ERROR: command too long"); }
    }
  }
  unsigned long now=millis();
  if (recording && now-lastMeasurement >= intervalMs) {
    lastMeasurement=now;
    digitalWrite(triggerPin,LOW); delayMicroseconds(2);
    digitalWrite(triggerPin,HIGH); delayMicroseconds(10); digitalWrite(triggerPin,LOW);
    unsigned long duration=pulseIn(echoPin,HIGH,pulseTimeoutUs);
    Serial.print(now-trialStart); Serial.print(",");
    if (duration==0) Serial.println(",TIMEOUT");
    else {
      // 343 m/s is a room-temperature approximation, not sensor calibration.
      Serial.print(duration * 0.343 / 2.0, 2); Serial.println(",");
    }
  }
}
