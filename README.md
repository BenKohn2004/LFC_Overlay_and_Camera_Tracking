# 🤺 Favero 05 to OBS Wireless Bridge

A low-latency, wireless automation system that connects a **Favero 05 Full Arm** fencing machine to **OBS Studio**. Using two Wemos D1 Mini (ESP8266) modules and ESP-NOW, this system triggers broadcast-style overlays the millisecond a hit is detected.

---

## ⚡ Quick Start Checklist

Before running the system, ensure these non-obvious configurations are complete:

- [ ] **MAC Address:** Copy the Receiver's MAC address into the `receiverAddress[]` array in the Transmitter code.
- [ ] **COM Port:** Identify the Receiver's COM port in Device Manager and update `COM_PORT` in `Favero_OBS_Bridge.py`.
- [ ] **OBS Auth:** Enable WebSocket authentication in OBS and copy the password into the Python script.
- [ ] [cite_start]**Naming:** Ensure your OBS Scene is named `Main` and your sources are `Red_Light`, `Green_Light`, `White_Red_Light`, and `White_Green_Light` [cite: 91, 101-104].
- [ ] **Serial Lock:** Close the Arduino Serial Monitor before starting the Python script.

---

## 🛠 Hardware Architecture

| Component | Role | Connection |
| :--- | :--- | :--- |
| **Transmitter** | Parses Favero data & broadcasts via ESP-NOW | Favero Serial → RS232-TTL → Wemos RX |
| **Receiver** | Listens for ESP-NOW & bridges to USB | Wemos USB → Laptop COM Port |
| **Laptop** | Runs Python Bridge & OBS Studio | [cite_start]Interprets Serial `DATA,` strings  |



---

## 🚀 Installation & Setup

### 1. Firmware Upload
1.  **Transmitter:** Upload `8266_Parser_and_Transmitter_Unified.ino`.
    * [cite_start]*Note:* The baud rate is set to `2400` to match the Favero output.
2.  **Receiver:** Upload `8266_Receiver_Laptop_Bridge.ino`.
    * [cite_start]*Note:* This runs at `115200` baud for the laptop link.

### 2. OBS Configuration
1.  Navigate to `Tools` → `obs-websocket Settings`.
2.  Enable the server and note your password (default port is `4455`).
3.  In your **"Main"** scene, create four sources (Image or Color) with these exact names:
    * `Red_Light`
    * `Green_Light`
    * `White_Red_Light`
    * `White_Green_Light`

### 3. Python Environment
Install the required dependencies via terminal:
```bash
pip install pyserial obs-websocket-py
