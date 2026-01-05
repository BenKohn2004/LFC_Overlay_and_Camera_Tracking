import serial
import time
from obswebsocket import obsws, requests
import serial.tools.list_ports

# --- Configuration ---
BAUD_RATE = 115200                  # Must match Receiver
OBS_HOST = "localhost"              
OBS_PORT = 4455
OBS_PASS = "ql2C52K1fl8RtyZU"      # Updated password
SCENE_NAME = "Main"                 # Must match your OBS Scene name

# Mapping CSV indices from Receiver DATA string to OBS Source Names
VISIBILITY_MAP = {
    1: "Red_Light",
    2: "Green_Light",
    3: "White_Red_Light",
    4: "White_Green_Light",
    9: "Yellow_Card_Red",
    10: "Yellow_Card_Green",
    11: "Red_Card_Red",
    12: "Red_Card_Green",
    13: "Priority_Left",
    14: "Priority_Right"
}

# Score and clock inputs
LEFT_SCORE_INPUT = "Left_Score_Text"
RIGHT_SCORE_INPUT = "Right_Score_Text"
CLOCK_INPUT = "Clock_Text"

# -----------------------------
# Automatic COM port detection with multi-device check
# -----------------------------
def find_arduino():
    """Detect connected Arduino/Wemos devices via USB."""
    ports = list(serial.tools.list_ports.comports())
    arduino_ports = [p.device for p in ports if any(keyword in p.description for keyword in ["USB", "CH340", "Arduino"])]
    
    if len(arduino_ports) == 0:
        return None, 0
    elif len(arduino_ports) == 1:
        return arduino_ports[0], 1
    else:
        return arduino_ports, len(arduino_ports)

# -----------------------------
# Connect to OBS WebSocket
# -----------------------------
def connect_obs():
    client = obsws(OBS_HOST, OBS_PORT, OBS_PASS)
    try:
        client.connect()
        print(f"✅ Connected to OBS WebSocket. Monitoring scene: {SCENE_NAME}")
        return client
    except Exception as e:
        print(f"❌ OBS Connection Error: {e}")
        return None

# -----------------------------
# Helper to find OBS scene item ID
# -----------------------------
def get_item_id(client, scene_name, source_name):
    try:
        response = client.call(requests.GetSceneItemList(sceneName=scene_name))
        for item in response.getSceneItems():
            if item['sourceName'] == source_name:
                return item['sceneItemId']
    except Exception:
        return None
    return None

# -----------------------------
# Main bridge function
# -----------------------------
def main():
    COM_PORT, count = find_arduino()
    if count == 0:
        print("❌ No Arduino/Wemos detected! Connect the device and try again.")
        return
    elif count > 1:
        print("❌ Multiple Arduino/Wemos devices detected! Please remove extra devices.")
        print("Detected devices:")
        for port in COM_PORT:
            print(f"  - {port}")
        return
    else:
        print(f"✅ Arduino/Wemos found on {COM_PORT}")

    obs = connect_obs()
    if not obs:
        return

    # Attempt to open the serial port
    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
        print(f"🎧 Listening for Favero data on {COM_PORT}...")
    except Exception as e:
        print(f"❌ Serial Error: {e}")
        return

    # Track previous states to minimize OBS updates
    last_visibility = {name: None for name in VISIBILITY_MAP.values()}
    last_score_l = ""
    last_score_r = ""
    last_clock = ""

    try:
        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if not line.startswith("DATA,"):
                continue  # Skip unrelated lines

            parts = line.split(",")

            # 1️⃣ Update lights/cards/priority
            for idx, source_name in VISIBILITY_MAP.items():
                if idx < len(parts):
                    is_active = (parts[idx] == "1")
                    if is_active != last_visibility[source_name]:
                        item_id = get_item_id(obs, SCENE_NAME, source_name)
                        if item_id is not None:
                            obs.call(requests.SetSceneItemEnabled(
                                sceneName=SCENE_NAME,
                                sceneItemId=item_id,
                                sceneItemEnabled=is_active
                            ))
                            last_visibility[source_name] = is_active

            # 2️⃣ Update scores
            if len(parts) > 6:
                score_l, score_r = parts[5], parts[6]
                if score_l != last_score_l:
                    obs.call(requests.SetInputSettings(
                        inputName=LEFT_SCORE_INPUT,
                        inputSettings={"text": score_l}
                    ))
                    last_score_l = score_l
                if score_r != last_score_r:
                    obs.call(requests.SetInputSettings(
                        inputName=RIGHT_SCORE_INPUT,
                        inputSettings={"text": score_r}
                    ))
                    last_score_r = score_r

            # 3️⃣ Update clock
            if len(parts) > 8:
                mins, secs = parts[7], parts[8].zfill(2)
                current_clock = f"{mins}:{secs}"
                if current_clock != last_clock:
                    obs.call(requests.SetInputSettings(
                        inputName=CLOCK_INPUT,
                        inputSettings={"text": current_clock}
                    ))
                    last_clock = current_clock

    except KeyboardInterrupt:
        print("\n⏹ Shutting down bridge...")
    finally:
        ser.close()
        obs.disconnect()
        print("✅ Disconnected cleanly from OBS and serial port.")

# -----------------------------
if __name__ == "__main__":
    main()
