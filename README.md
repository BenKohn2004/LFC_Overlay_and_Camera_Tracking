# LFC_Overlay_and_Camera_Tracking
LFC_Overlay_and_Camera_Tracking

Favero 05 Full Arm to OBS Bridge
This project provides a low-latency wireless bridge between a Favero 05 Full Arm fencing scoring machine and OBS Studio. It uses two Wemos D1 Mini (ESP8266) modules to transmit scoring and light data via ESP-NOW and a Python script to trigger OBS overlays via WebSockets.

⚡ Quick Start: The "Don't Forget" List
If you are setting this up for the first time, perform these five non-obvious steps to get the system communicating:

Identify the Receiver's Identity (MAC Address):

The Transmitter needs to know exactly which "radio" to talk to.

Find it: Upload a "MAC Address Finder" sketch to your Receiver Wemos. Open the Serial Monitor to find the address (e.g., 0x12, 0x34, 0x56, 0x78, 0x90, 0xAB).


Link it: Open 8266_Parser_and_Transmitter_Unified.ino and paste that code into the receiverAddress[] array.

Locate the Correct COM Port:

Windows assigns COM numbers (COM3, COM4, etc.) to USB devices dynamically.

Find it: Open Device Manager → Ports (COM & LPT).

Confirm it: Unplug your Receiver Wemos and plug it back in; the entry that disappears and reappears is your target.

Update: Enter this number into the COM_PORT variable in Favero_OBS_Bridge.py.

Set the OBS "Secret Handshake" (Password):

The Python script cannot control OBS without permission.

In OBS: Go to Tools → obs-websocket Settings.

The Key: Ensure Enable authentication is checked and copy the password.

Update: Paste this into the OBS_PASS variable in your Python script.

Align the Scene and Source Names:

Scene: In OBS, your scene must be named exactly Main (case-sensitive).


Sources: Add your layers and name them exactly: Red_Light, Green_Light, White_Red_Light, and White_Green_Light .
+1

Clear the Serial Path:

Only one program can "talk" to a Wemos at a time.

The Rule: Always close the Arduino Serial Monitor before launching Favero_OBS_Bridge.py.

1. Hardware Setup
Transmitter Wemos

Connection: Connect the Favero Serial Output to the RX pin of the Wemos.
+1

Voltage: Use an RS-232 to TTL converter to ensure the Wemos receives 3.3V logic levels.


Code: Upload 8266_Parser_and_Transmitter_Unified.ino.

Receiver Wemos

Connection: Connect via USB to the streaming laptop.


Code: Upload 8266_Receiver_Laptop_Bridge.ino.

2. OBS Configuration
Enable WebSockets:

Open OBS → Tools → obs-websocket Settings.

Check Enable WebSocket server.

Set Server Port to 4455.

Scene Setup:

Create a Scene named "Main".

Add four sources named exactly: Red_Light, Green_Light, White_Red_Light, and White_Green_Light .
+1

3. Python Bridge Setup
Install Requirements:

Bash

pip install pyserial obs-websocket-py
Run the Bridge:

Run Favero_OBS_Bridge.py.

The lights in OBS will now mirror the Favero machine in real-time .
+1

4. Troubleshooting
No lights in OBS: Ensure the Python script says "Connected to OBS." Check that your Scene and Source names match exactly.


Inverted Lights: If the Red light triggers the Green source, simply rename the sources in OBS or swap the names in the Python SOURCE_MAP .
+1


Data Lag: Ensure the Receiver is set to 115200 baud.
