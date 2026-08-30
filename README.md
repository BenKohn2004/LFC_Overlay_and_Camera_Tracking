LFC Overlay & Camera Tracking System
An automated video refereeing, recording, and camera tracking system designed for fencing strips. The project consists of two independent subsystems:

Camera Tracking Subsystem: Measures fencer positions using optical quadrature encoders built into Favero reels and wirelessly controls a motorized camera servo via ESP-NOW to keep fencers centered.

Video Recording & Overlay Subsystem: A Raspberry Pi station that intercepts Bluetooth Low Energy (BLE) packets from a Skewered Fencing machine to burn match scores, lights, and timing onto webcam footage and manage clip export.

System Architecture
Subsystem	Core Hardware	Communication	Primary Function
Reel Sensors (x2)	Wemos D1 Mini, TCRT5000 IR (x2), OLED	ESP-NOW	Encodes drum rotation and broadcasts unspool position.
Camera Servo Unit	Wemos D1 Mini, DS3218 270° Servo	ESP-NOW	Receives positions from both reels and pans camera to midpoint.
Video & Overlay Station	Raspberry Pi 5, 7" Touchscreen, USB Camera	BLE (Bluetooth LE)	Intercepts scoring state, generates overlay, indexes touches, and exports clips.
 
📹 Part 1: Camera Tracking System
1. Optical Encoder Hardware (Per Reel)
Quadrature Encoding Principle: See this YouTube Video Explanation.

Pattern: Four $\frac{1}{8}$-circle segments of matte black vinyl wrap are adhered to the bottom of the Favero reel drum alternating with four exposed reflective aluminum sections.

Sensors: Two TCRT5000 IR reflective sensors placed 32 mm apart, housed in a 3D Printed Sensor Holder.

2. Electronics & PCB
Fabrication Files: Gerber Files & Circuit Schematic.

Bill of Materials (BOM):

Microcontroller: Wemos D1 Mini (ESP8266)

Display: 0.96" I2C OLED Screen (⚠️ Note: Verify OLED pinout order; some modules swap VCC and GND)

Power: 18650 Li-ion cell, 18650 Holder, TP4056 USB-C Charging Module, and Mini Toggle Switch

Passives & Hardware: 10kΩ resistors, 0.1 µF & 100 µF capacitors, pushbuttons, screw terminals, and M3 × 8 mm screws.

Enclosure: 3D Printed Electronics Case mounted directly to the back of the Favero reel via tapped M3 threads.

3. Camera Servo Hardware
Servo: DS3218 270° Digital Servo mounted on a tripod.

Optics: Manual Zoom/Focus CS-mount USB Camera.

Controller: Standalone Wemos D1 Mini wired directly to the servo PWM line.

4. Firmware Configuration
Reel Unit Firmware: Wemos_Reel_Encoder.ino

Set reel assignment:

cpp
outgoingData.senderID = 1; // 1 for Left Reel, 2 for Right Reel
Adjust strip geometry / camera distance:

cpp
int hypotenuse = 250; // Distance calibration units displayed on OLED
Camera Servo Firmware: Wemos_Camera_Servo Rev 1.ino

Part 2: Video Recording, Overlay & Replay Station
The recording station receives real-time match data over Bluetooth Low Energy (BLE) from a Skewered Fencing scoring apparatus (credit to Augusto Roman / Skewered Fencing).

1. Hardware Setup
SBC: Raspberry Pi 5 with Active Cooler

Screen: 7" Touchscreen Display

Power: USB-PD Battery Bank

2. Software & Automation Behavior
Automatic Recording Trigger: Starts recording upon detecting score changes or touch lights, and keeps recording for 5 minutes after the last action.

Auto Bout Reset: Automatically splits bouts into new sessions when the score resets to 0 - 0.

Touch Indexing & Timestamping: Continuously buffers video and logs exact touch timestamps in an internal SQLite database.

Exporting Options:

Export All Bouts: Extracts and exports only the trimmed clips surrounding each touch.

Export Specific Bout: Exports the full uninterrupted recording of the selected match.

Transfer is handled directly through the UI to an attached USB drive.

Customization: Fencer names and team logos are configurable directly on the touchscreen interface.
