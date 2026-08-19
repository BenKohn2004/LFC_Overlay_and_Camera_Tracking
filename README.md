# LFC Overlay & Camera Tracking System

A broadcasting and review solution for fencing that features **automated camera tracking** and repeats scoring machine data onto a live overlay.

## 🚀 Overview
The project is three largely independent systems:

1.  **Camera Tracking:** A servo-mounted camera follows the fencers by measuring the unspooling of the reels, using quadrature encoder rings read by optical sensors.
2.  **Scoring Overlay:** Real-time lights, score, clock and hit timing taken from a **Skewered** scoring box over **Bluetooth LE**.
3.  **Raspberry Pi Station:** A self-contained touchscreen box that renders the overlay, records the strip, indexes every touch, and plays back instant replays.
---

## 🏗 System Architecture

Two separate radios, doing two unrelated jobs:

* **Scoring data** goes straight from the Skewered box to the Raspberry Pi over **Bluetooth LE**. There is no wiring and no intermediate microcontroller.
* **Camera tracking** uses **ESP8266 (Wemos D1 Mini)** boards talking **ESP-NOW** — the encoder board on the reel sends positions to the servo board on the camera.

### 1. Camera Tracking System
The camera uses a quadrature encoder ring made of [Matte Black Vinyl Wrap]([Camera%20Tracking%20System/Favero%20Reel%20Encoder%20Tiled.pdf](https://www.amazon.com/dp/B07PYK74SG)) mounted on the reel drum to calculate fencer positions in real-time. The ring segments were printed out on [8 1/2 x 11 labels](https://www.amazon.com/Labelchoice-Shipping-Printers-Adhesive-Mailing/dp/B0B5RCBMW8) and adhered to the base of the reel drum.

* **Hardware:**
    * **Servo:** [DS3218 (270° Digital Servo)](https://www.aliexpress.us/item/3256807308983626.html).
    * **Camera:** [2K HD Manual Varifocal](https://www.aliexpress.us/item/3256808470911939.html) (2.8-12mm) USB Camera with CS Lens.
    * **Sensors:** [TCRT5000 IR](https://www.aliexpress.us/item/3256806722701672.html) sensors.
* **Logic:** The system measures the length of the reel cable played out. By calculating the midpoint between the two ends of the reel cables, the servo rotates the camera to keep the action centered.
* **Calibration:**
    * Update `center_line` and `hypotenuse` in `Wemos_Reel_Encoder.ino` based on your camera's physical distance from the strip.
         This is done by connecting the reel with the encoder, zeroing it through the PCB mounted button and then extending the reel cord to the center line and camera while taking note of the value of 'position' listed on the PCB's [OLED display](https://www.aliexpress.us/item/3256805954920554.html)
    * The TCRT5000 sensors must be calibrated using the onboard potentiometer; dial the sensitivity until it reliably detects the white bars on the encoder ring but ignores the black. It is worth verifying each bar on the encoder ring. The position will drift off zero during use if the sensor does not reliably detect the bars.
* **3D Files:** The [Optical Sensor Holder](https://www.onshape.com/en/) is publicly available through OnShape and is titled Favero Reel Optical Holder. It is designed to fit snugly against the rivet on the back of the reel.

### 2. Scoring Machine Integration (Skewered over BLE)
The **Skewered** box advertises its complete state over Bluetooth LE, so the Pi reads it directly — no data cable, no RS-485 converter, no transmitter board.

* **How it works:** the box advertises under the short name `SkF:<strip id>` with manufacturer ID `0x0E88`, and every advertisement carries a full state packet (scores, clock, lights, cards, priority, weapon, period and per-side hit timing). See the [Skewered protocol](https://github.com/skewered-fencing/protocol).
* **`Raspberry_Pi_Station/ble_bridge.py`** decodes those advertisements and re-emits them as a UDP packet on port 4210 for `station.py`. It uses **BlueZ over D-Bus** (`python3-dbus` + PyGObject, both preinstalled on Raspberry Pi OS) — no `pip install` required. It runs from boot via `skewered-ble-bridge.service`.
* **Two things to get right if you modify it:**
    * The box's BLE address is **Non-Resolvable Random** and rotates. Match on the manufacturer ID, never the address.
    * **`DuplicateData` must be enabled** in the discovery filter, or the adapter suppresses byte-identical advertisements and an idle box goes nearly silent (measured: 4 reports in 18 s filtered, ~6-13 Hz unfiltered).
* **Hit timing:** bytes 07-09 carry a per-side millisecond value whose meaning depends on the light status — a live, ticking age for a valid or off-target hit, and a frozen lateness figure for a late one. Touches are back-dated by that age so their timestamps reflect when the hit actually happened rather than when the packet arrived, and late hits are shown on the overlay.

> **Legacy path:** the earlier Wi-Fi route — an ESP8266 parsing the box's RS-485 output through a MAX485 and broadcasting UDP from its own SoftAP — is kept in [`Skewered/`](Skewered/) for reference, along with a Favero FA-05 parser in [`Scoring Machine Integration/`](Scoring%20Machine%20Integration/). Neither is needed for the BLE setup.

### 3. Raspberry Pi Station
A Raspberry Pi 5 with a 7" touchscreen and a USB webcam: live view with the overlay burned in, activity-triggered recording, a SQLite index of every touch, an on-screen bout browser with instant replay, and YouTube upload. Full detail in [`Raspberry_Pi_Station/README.md`](Raspberry_Pi_Station/README.md).

## 💻 OBS Integration (legacy Favero route)

Before the Raspberry Pi station, the overlay was driven by OBS on a laptop. `Receiver_and_Bridge/Favero_OBS_Bridge.py` listens to a Wemos receiver on the serial port and pushes state to OBS via **Websockets**. This path still works for a Favero FA-05, but the Pi station replaces it — it renders its own overlay and burns it into the recording.

* **Setup:**
    1.  Install dependencies: `pip install obsws-python pyserial`.
    2.  Update the script with your OBS Websocket password and local image asset paths.
    3.  Import the provided OBS scene: [`Receiver_and_Bridge/Camera_Tracking_Favero_Overlay.json`](Receiver_and_Bridge/Camera_Tracking_Favero_Overlay.json).

---

## ⚠️ Troubleshooting
* **Inconsistent Counting:** Usually caused by the encoder ring coming loose from the reel drum. Ensure it is securely attached and the reel returns to "zero" when fully retracted.
* **Environmental Light:** If the IR sensors struggle, try calibrating them in a dimmer environment to simulate the interior of the reel housing.
* **No scoring data on the Pi:** check the bridge with `systemctl status skewered-ble-bridge`. `Troubleshooting_Tools/hit_capture.py` logs every decoded advertisement and is the fastest way to see whether the box is being heard at all.
* **Box not found:** confirm it is advertising with `bluetoothctl scan on` — it appears as `SkF:<strip id>`. Note that scan output is duplicate-filtered, so an idle box updates only when its state changes.

---

## 🛠 Fabrication
* **PCBs:** The board is panelized and includes both the external housing portion and the TCRT5000 connector. Find the files in [`Camera Tracking System/Gerber_Favero_Optical_Encoder_PCB.zip`](Camera%20Tracking%20System/Gerber_Favero_Optical_Encoder_PCB.zip).
