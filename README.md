# LFC Overlay & Camera Tracking System

A broadcasting solution for fencing that features **automated camera tracking** and repeats scoring machine data onto the livestream overlay.

## 🚀 Overview
This project automates the livestreams using three relatively independent systems through an ESP-NOW wireless network and a Python-to-OBS bridge.

1.  **Camera Tracking:** A servo-mounted camera follows the fencers by measuring the unspooling of the Favero reels.
2.  **Scoring Overlay:** Real-time lights, score, and time data pulled from the Favero FA-05 scoring machine.
3.  **QR Registration:** A handheld scanner to update fencer names and club affiliations in the broadcast.

---

## 🏗 System Architecture
The system utilizes **ESP8266 (Wemos D1 Mini)** microcontrollers communicating via **ESP-NOW** (a low-latency wireless protocol) to a central receiver (Wemos) connected to a laptop running **OBS (Open Broadcaster Software)**.

### 1. Camera Tracking System
The camera uses a [quadrature encoder ring](https://github.com/BenKohn2004/LFC_Overlay_and_Camera_Tracking/blob/main/Favero%20Reel%20Encoder%20Tiled.pdf) mounted on the Favero reel drum to calculate fencer positions in real-time. The ring segments were printed out on [8 1/2 x 11 labels](https://www.amazon.com/Labelchoice-Shipping-Printers-Adhesive-Mailing/dp/B0B5RCBMW8) and adhered to the base of the Favero Reel drum.

* **Hardware:**
    * **Servo:** [DS3218 (270° Digital Servo)](https://www.aliexpress.us/item/3256807308983626.html).
    * **Camera:** [2K HD Manual Varifocal](https://www.aliexpress.us/item/3256808470911939.html) (2.8-12mm) USB Camera with CS Lens.
    * **Sensors:** [TCRT5000 IR](https://www.aliexpress.us/item/3256806722701672.html) sensors.
* **Logic:** The system measures the length of the reel cable played out. By calculating the midpoint between the two ends of the Favero reel cables, the servo rotates the camera to keep the action centered.
* **Calibration:**
    * Update `center_line` and `hypotenuse` in `Wemos_Reel_Encoder.ino` based on your camera's physical distance from the strip.
         This is done by connecting the reel with the encoder, zeroing it through the PCB mounted button and then extending the reel cord to the center line and camera while taking note of the value of 'position' listed on the PCB's [OLED display](https://www.aliexpress.us/item/3256805954920554.html)
    * The TCRT5000 sensors must be calibrated using the onboard potentiometer; dial the sensitivity until it reliably detects the white bars on the encoder ring but ignores the black.
* **3D Files:** The [Optical Sensor Holder](https://cad.onshape.com/documents/fab3dbb0c6cd24d122a26ac7/w/167803772a56f7a36cd09560/e/40a31ee80700d67aef5da61b) is designed to fit snugly against the rivet on the back of the Favero reel.

### 2. Scoring Machine Integration
The system intercepts the data stream from a **Favero FA-05** via the RJ11 data port.

* **Wiring:** See `Schematic_Favero-Parser-and-Receiver_2025-12-16.pdf`. 
* **Note:** Use the Wemos version employing the **CH340 chip** for maximum stability.
* **Configuration:** Each "Parser" Wemos must have a unique `Box Name` if you are managing multiple strips. Update the code with the MAC address of your central receiver.

### 3. QR Code Scanner
For rapid fencer changes during tournaments. This module allows you to scan a fencer's info and have OBS update the names/clubs instantly.

* **Hardware:** Waveshare Round 2D Codes Scanner Module.
* **Housing:** [3D Model for QR Reader](https://cad.onshape.com/documents/1d7ee73f291c58a099dbd764/w/b41d5c9f579bbfb616cc79d3/e/3b1fc9ae92bd20791cf18614).

---

## 💻 Software & OBS Integration

### The Python Bridge (`Favero_OBS_Bridge.py`)
This script acts as the "brain." It listens to the Serial port for data from the Wemos receiver and pushes it to OBS via **Websockets**.

* **Setup:**
    1.  Install dependencies: `pip install obsws-python pyserial`.
    2.  Update the script with your OBS Websocket password and local image asset paths.
    3.  Import the provided OBS scene: `Camera_Tracking_Favero_Overlay.json`.

---

## 📐 Mathematics of Tracking
The system calculates the midpoint between the two fencers to determine the servo angle. If the camera is placed at a distance $D$ from the center of the strip, and the fencers are at positions $L$ and $R$ relative to the reels:

$$\text{Midpoint} = \frac{L + R}{2}$$

The required servo angle $\theta$ is determined by:

$$\theta = \arctan\left(\frac{\text{Midpoint}}{D}\right)$$

---

## ⚠️ Troubleshooting
* **Inconsistent Counting:** Usually caused by the encoder ring coming loose from the Favero drum. Ensure it is securely attached and the reel returns to "zero" when fully retracted.
* **Environmental Light:** If the IR sensors struggle, try calibrating them in a dimmer environment to simulate the interior of the reel housing.
* **Serial Connection:** Ensure the Wemos receiver is assigned to the correct COM port in the Python script.

---

## 🛠 Fabrication
* **PCBs:** The board is panelized and includes both the external housing portion and the TCRT5000 connector. Find the files in `Gerber_Favero_Optical_Encoder_PCB.zip`.
