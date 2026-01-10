import serial
import time
import json
from obswebsocket import obsws, requests
import serial.tools.list_ports
import os

# --- Configuration ---
BAUD_RATE = 115200
OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASS = "ql2C52K1fl8RtyZU"
SCENE_NAME = "Main"

# OBS Text Sources
LEFT_SCORE_INPUT = "Left_Score_Text"
RIGHT_SCORE_INPUT = "Right_Score_Text"
CLOCK_INPUT = "Clock_Text"

LEFT_NAME_INPUT = "Left Fencer Name"
RIGHT_NAME_INPUT = "Right Fencer Name"

LEFT_CLUB_IMAGE = "Left Club"
RIGHT_CLUB_IMAGE = "Right Club"

CLUB_IMAGE_DIR = r"C:\Users\Ben\Desktop\OBS Files to Email"

# Mapping CSV indices → OBS sources
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

# -----------------------------
def find_arduino():
    ports = list(serial.tools.list_ports.comports())
    devices = [p.device for p in ports if any(k in p.description for k in ["USB", "CH340", "Arduino"])]
    return (devices[0], 1) if len(devices) == 1 else (devices, len(devices))

# -----------------------------
def connect_obs():
    client = obsws(OBS_HOST, OBS_PORT, OBS_PASS)
    client.connect()
    print(f"✅ Connected to OBS WebSocket ({SCENE_NAME})")
    return client

# -----------------------------
def get_item_id(client, scene, source):
    resp = client.call(requests.GetSceneItemList(sceneName=scene))
    for item in resp.getSceneItems():
        if item['sourceName'] == source:
            return item['sceneItemId']
    return None

# -----------------------------
def set_text(obs, source, text):
    obs.call(requests.SetInputSettings(
        inputName=source,
        inputSettings={"text": text},
        overlay=True
    ))

def set_image(obs, source, path):
    obs.call(requests.SetInputSettings(
        inputName=source,
        inputSettings={"file": path},
        overlay=True
    ))

# -----------------------------
def main():
    COM_PORT, count = find_arduino()
    if count != 1:
        print("❌ Arduino/Wemos not uniquely detected")
        return

    obs = connect_obs()
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    print(f"🎧 Listening on {COM_PORT}...")

    last_visibility = {k: None for k in VISIBILITY_MAP.values()}
    last_score_l = last_score_r = last_clock = ""
    last_left_name = last_right_name = ""
    last_left_club = last_right_club = ""

    try:
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue

            # ---------------- DATA ----------------
            if line.startswith("DATA,"):
                parts = line.split(",")

                for idx, source in VISIBILITY_MAP.items():
                    if idx < len(parts):
                        active = parts[idx] == "1"
                        if active != last_visibility[source]:
                            item_id = get_item_id(obs, SCENE_NAME, source)
                            obs.call(requests.SetSceneItemEnabled(
                                sceneName=SCENE_NAME,
                                sceneItemId=item_id,
                                sceneItemEnabled=active
                            ))
                            last_visibility[source] = active

                score_l, score_r = parts[5], parts[6]
                if score_l != last_score_l:
                    set_text(obs, LEFT_SCORE_INPUT, score_l)
                    last_score_l = score_l
                if score_r != last_score_r:
                    set_text(obs, RIGHT_SCORE_INPUT, score_r)
                    last_score_r = score_r

                clock = f"{parts[7]}:{parts[8].zfill(2)}"
                if clock != last_clock:
                    set_text(obs, CLOCK_INPUT, clock)
                    last_clock = clock

            # ---------------- QR ----------------
            elif line.startswith("QR,"):
                try:
                    payload = json.loads(line[3:])
                    side = payload["side"]
                    name = payload["name"]
                    club = payload["club"]

                    img_path = os.path.join(CLUB_IMAGE_DIR, f"{club}.png")

                    if side == "L":
                        if name != last_left_name:
                            set_text(obs, LEFT_NAME_INPUT, name)
                            last_left_name = name
                        if club != last_left_club and os.path.exists(img_path):
                            set_image(obs, LEFT_CLUB_IMAGE, img_path)
                            last_left_club = club

                    elif side == "R":
                        if name != last_right_name:
                            set_text(obs, RIGHT_NAME_INPUT, name)
                            last_right_name = name
                        if club != last_right_club and os.path.exists(img_path):
                            set_image(obs, RIGHT_CLUB_IMAGE, img_path)
                            last_right_club = club

                except Exception as e:
                    print(f"⚠ QR parse error: {e}")

    except KeyboardInterrupt:
        print("\n⏹ Stopping bridge")
    finally:
        ser.close()
        obs.disconnect()

# -----------------------------
if __name__ == "__main__":
    main()
