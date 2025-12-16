# 🤺 Favero 05 to OBS Wireless Bridge

A low-latency, wireless automation system that connects a **Favero 05 Full Arm** fencing machine to **OBS Studio**. Using two Wemos D1 Mini (ESP8266) modules and ESP-NOW, this system triggers broadcast-style overlays the millisecond a hit is detected.

---

## ⚡ Quick Start Checklist

Before running the system, perform these **five** non-obvious steps to get the system communicating:

- [ ] **MAC Address:** Identify the Receiver's MAC address and copy it into the `receiverAddress[]` array in the Transmitter code.
- [ ] **COM Port:** Identify the Receiver's COM port in Device Manager and update `COM_PORT` in `Favero_OBS_Bridge.py`.
- [ ] **OBS Auth:** Enable WebSocket authentication in OBS and copy the password into the Python script.
- [ ] [cite_start]**Naming:** Ensure your OBS Scene is named `Main` and your sources match the naming convention below [cite: 91, 101-104].
- [ ] **Serial Lock:** Close the Arduino Serial Monitor before starting the Python script.

---

## 🛠 Hardware Architecture

| Component | Role | Connection |
| :--- | :--- | :--- |
| **Transmitter** | Parses Favero data & broadcasts via ESP-NOW | Favero Serial → RS232-TTL → Wemos RX |
| **Receiver** | Listens for ESP-NOW & bridges to USB | Wemos USB → Laptop COM Port |
| **Laptop** | Runs Python Bridge & OBS Studio | [cite_start]Interprets Serial `DATA,` strings [cite: 140, 141] |



---

## 📺 OBS Configuration & Verification Checklist

Ensure your OBS Studio environment matches these requirements exactly. The script relies on case-sensitive names to locate and control your overlay elements.

### 1. WebSocket Server Settings
Navigate to **Tools** > **obs-websocket Settings**:
- [ ] **Enable WebSocket server** is checked.
- [ ] **Server Port** is set to `4455`.
- [ ] **Enable authentication** is checked.
- [ ] **Password** matches the `OBS_PASS` variable in your Python script.

### 2. Scene Setup
- [ ] A Scene exists named exactly **`Main`** (case-sensitive).

### 3. Light & Card Sources (Visibility)
Create these sources (Images, Media, or Color Sources) within the **`Main`** scene.
- [ ] [cite_start]**`Red_Light`**: Valid hit for the Left fencer.
- [ ] [cite_start]**`Green_Light`**: Valid hit for the Right fencer.
- [ ] [cite_start]**`White_Red_Light`**: Off-target hit for the Left fencer.
- [ ] [cite_start]**`White_Green_Light`**: Off-target hit for the Right fencer.
- [ ] **`Yellow_Card_Red`**: Left fencer warning card.
- [ ] **`Yellow_Card_Green`**: Right fencer warning card.
- [ ] **`Red_Card_Red`**: Left fencer penalty card.
- [ ] **`Red_Card_Green`**: Right fencer penalty card.
- [ ] **`Priority_Left`**: Left fencer advantage/overtime lamp.
- [ ] **`Priority_Right`**: Right fencer advantage/overtime lamp.

### 4. Text Sources (Dynamic Data)
Create **Text (GDI+)** sources for numerical and time data.
- [ ] [cite_start]**`Left_Score_Text`**: Displays the current score for the left fencer.
- [ ] [cite_start]**`Right_Score_Text`**: Displays the current score for the right fencer.
- [ ] [cite_start]**`Clock_Text`**: Displays match time in `MM:SS` format.

---

## 🚀 Installation & Setup

### 1. Firmware Upload
1.  **Transmitter:** Upload `8266_Parser_and_Transmitter_Unified.ino`.
    * *Note:* The baud rate is set to `2400` to match the Favero output[cite: 128].
2.  **Receiver:** Upload `8266_Receiver_Laptop_Bridge.ino`.
    * [cite_start]*Note:* This runs at `115200` baud for the laptop link[cite: 143].

### 2. Python Environment
Install the required dependencies via terminal:
```bash
pip install pyserial obs-websocket-py
