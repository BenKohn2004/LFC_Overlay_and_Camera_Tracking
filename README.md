# LFC Overlay & Camera Tracking System

An automated video refereeing, recording, and camera tracking system designed for fencing strips. The project consists of two independent subsystems:

1. **Camera Tracking Subsystem:** Measures fencer positions using optical quadrature encoders built into Favero reels and wirelessly controls a motorized camera servo via ESP-NOW to keep fencers centered in frame.
2. **Video Recording & Overlay Subsystem:** A Raspberry Pi station that intercepts Bluetooth Low Energy (BLE) packets from a [Skewered Fencing machine](https://github.com/skewered-fencing) to burn match scores, lights, and timing onto webcam footage and manage clip export.

---

## System Architecture

| Subsystem | Core Hardware | Communication | Primary Function |
|---|---|---|---|
| **Reel Sensors (x2)** | Wemos D1 Mini (ESP8266), TCRT5000 IR Sensors (x2), 0.96" OLED | **ESP-NOW** | Encodes drum rotation and broadcasts unspool position. |
| **Camera Servo Unit** | Wemos D1 Mini (ESP8266), DS3218 270° Servo | **ESP-NOW** | Receives positions from both reels and pans camera to midpoint. |
| **Video & Overlay Station** | Raspberry Pi 5, 7" Touchscreen, USB Camera | **BLE (Bluetooth LE)** | Intercepts scoring state, generates overlay, indexes touches, and exports clips. |

---

## 📹 Part 1: Camera Tracking System

### 1. Optical Encoder Hardware (Per Reel)
* **Quadrature Encoding Principle:** See this [YouTube Video Explanation](https://youtu.be/cfHPRQ0f3-o).
* **Pattern:** Four $\frac{1}{8}$-circle segments of [matte black vinyl wrap](https://www.amazon.com/dp/B07PYK74SG) are adhered to the bottom of the Favero reel drum, alternating with four exposed reflective aluminum sections.
* **Sensors:** Two TCRT5000 IR reflective sensors placed **32 mm apart**, housed in a [3D Printed Sensor Holder](https://cad.onshape.com/documents/fab3dbb0c6cd24d122a26ac7/w/167803772a56f7a36cd09560/e/40a31ee80700d67aef5da61b?renderMode=0&uiState=6a9347b5703c2e93da4af6cf).

### 2. Electronics & PCB
* **Fabrication Files:** [Gerber Files](https://github.com/BenKohn2004/LFC_Overlay_and_Camera_Tracking/blob/main/Camera%20Tracking%20System/Gerber_Favero_Optical_Encoder_PCB_Favero_Optical_Encoder_2_2026-08-18.zip) & [Circuit Schematic](https://github.com/BenKohn2004/LFC_Overlay_and_Camera_Tracking/blob/main/Camera%20Tracking%20System/Schematic_Favero_Optical_Encoder_2026-08-30.pdf).
* **Bill of Materials (BOM):**
  * **Microcontroller:** [Wemos D1 Mini (ESP8266)](https://www.amazon.com/NodeMcu-Internet-Development-ESP8266-Compatible/dp/B0BHW1CNCM/ref=sr_1_3)
  * **Display:** [0.96" I2C OLED Screen](https://www.amazon.com/iPistBit-Display-Luminous-Compatible-Raspberry/dp/B0D2NPB4BM/) *(⚠️ Note: Verify OLED pinout order; some modules swap `VCC` and `GND`)*
  * **Power System:** 18650 Li-ion cell, [18650 Battery Holder](https://www.temu.com/10pcs-5pcs-18650-power-bank-cases-1x-3-6v-4-2v-18650-battery-holder-storage-box-case-1-slot-battery-container-with-wire-lead-g-601099751570278.html), [TP4056 USB-C Charging Module](https://www.temu.com/12pcs--lithium-battery-charging-module-with-micro-usb-type-c-ports-protection-board-18650-charging-board-protection-module-g-606096320255275.html), and [Mini SPDT Toggle Switch](https://www.temu.com/30pcs--mini-toggle-switches-mini--toggle-switches-compact-high-knob-design-for--on---boards-g-601100638881014.html)
  * **Passives & Hardware:** 10kΩ resistors, 0.1 µF & 100 µF capacitors, [pushbuttons](https://www.amazon.com/dp/B00R17XUFC), [screw terminals](https://www.amazon.com/Molence-Terminal-Connector-Terminals-26-18AWG/dp/B09F6TC7RP/), and [M3 × 8 mm screws](https://www.temu.com/uadkl-347pcs--hex-socket-button-head-screws-nuts-assortment-kit-round--set-5-20mm-metric-machine-screws-with-storage-box-for-electronics-furniture-hardware-repair-g-607178903681949.html)
* **Enclosure:** [3D Printed Electronics Case](https://cad.onshape.com/documents/7d9532982665e26a0dfa1674/w/e8c4ee4c5a7db918b2149cef/e/8ab91a05bc2fb4744dda546d?renderMode=0&uiState=6a934cc2d62aea1f596a2f0e) mounted directly to the back of the Favero reel via tapped M3 threads.
* **Power Note:** The power switch toggles between charging the battery and powering the circuit board. To power the board directly during testing, connect via the Wemos USB port.

### 3. Camera Servo Hardware
* **Servo:** [DS3218 270° Digital Servo](https://www.amazon.com/dp/B07HNTKSZT) mounted atop a standard tripod.
* **Optics:** [Manual Zoom/Focus CS-mount USB Camera](https://www.aliexpress.us/item/3256805987213806.html).
* **Controller:** Standalone Wemos D1 Mini wired to the servo PWM signal (no custom PCB required).

### 4. Firmware Configuration
* **Reel Encoder Firmware:** [`Wemos_Reel_Encoder.ino`](https://github.com/BenKohn2004/LFC_Overlay_and_Camera_Tracking/blob/main/Camera%20Tracking%20System/Wemos_Reel_Encoder.ino)
  * Set reel side assignment:
    ```cpp
    outgoingData.senderID = 1; // 1 for Left Reel, 2 for Right Reel
    ```
  * Adjust strip geometry / camera distance:
    ```cpp
    int hypotenuse = 250; // Distance calibration units displayed on OLED
    ```
* **Camera Servo Firmware:** [`Wemos_Camera_Servo Rev 1.ino`](https://github.com/BenKohn2004/LFC_Overlay_and_Camera_Tracking/blob/main/Camera%20Tracking%20System/Wemos_Camera_Servo%20Rev%201.ino)

---

## Part 2: Video Recording, Overlay & Replay Station

The recording station is reasonably straightforward and relies heavily on the excellent work done by [Augusto Roman](https://github.com/skewered-fencing) in putting together his Skewered Fencing scoring box. The Skewered Fencing Box broadcasts the fencing data via Bluetooth (BLE) and the data is then used by the Raspberry Pi to create the fencing overlay.

### 1. Hardware Setup
* **SBC:** [Raspberry Pi 5 with Active Cooler](https://www.amazon.com/RasTech-Raspberry-Active-Cooler-Readers/dp/B0D2WYFS23/ref=sr_1_1)
* **Screen:** [7" Touchscreen Display](https://www.amazon.com/dp/B0D3QB7X4Z)
* **Power:** [USB-PD Battery Bank](https://www.amazon.com/dp/B0BJQ7F16T)

### 2. Software & Automation Behavior
* **Automatic Recording Trigger:** Automatically initiates clip capture upon detecting a scoring event (lights/score change) and continues recording for **5 minutes** after the last activity.
* **Auto Bout Reset:** Automatically splits matches into new bout sessions whenever the score is reset to `0 - 0`.
* **Touch Indexing & Timestamping:** Continuously records footage while indexing exact touch timestamps into an SQLite database.
* **Exporting Options:**
  * **Export All Bouts:** Generates trimmed highlight video files containing only the action surrounding each touch.
  * **Export Specific Bout:** Exports the full uninterrupted recording of the selected bout.
  * Footage can be exported directly to an attached USB flash drive via the touchscreen UI.
* **Customization:** Fencer names and club/team logos can be updated on the fly directly through the interface.
* **Streaming Note:** The system is optimized for local recording and video review; however, the pipeline can be adapted for live streaming (e.g., OBS / RTMP) if desired.
