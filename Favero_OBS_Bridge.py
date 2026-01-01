import serial
import time
from obswebsocket import obsws, requests

# --- Configuration ---
COM_PORT = 'COM3'         # Update to your Receiver's COM port
BAUD_RATE = 115200        # Must match Receiver [cite: 143]
OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASS = "password" # Set in OBS Tools -> WebSocket Settings
SCENE_NAME = "Main"       # Must match your OBS Scene name

# Mapping CSV indices from Receiver DATA string to OBS Source Names 
# Index: 1=Red, 2=Green, 3=WhiteRed, 4=WhiteGreen
# Index: 9=Y_Red_Card, 10=Y_Green_Card, 11=R_Red_Card, 12=R_Green_Card
# Index: 13=Prio_Left, 14=Prio_Right
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

def connect_obs():
    client = obsws(OBS_HOST, OBS_PORT, OBS_PASS)
    try:
        client.connect()
        print(f"Successfully connected to OBS. Monitoring scene: {SCENE_NAME}")
        return client
    except Exception as e:
        print(f"OBS Connection Error: {e}")
        return None

def main():
    obs = connect_obs()
    if not obs: return

    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
        print(f"Listening for Favero data on {COM_PORT}...")
    except Exception as e:
        print(f"Serial Error: {e}")
        return

    # Tracking states to minimize network traffic
    last_visibility = {name: None for name in VISIBILITY_MAP.values()}
    last_score_l = ""
    last_score_r = ""
    last_clock = ""

    try:
        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if line.startswith("DATA,"):
                parts = line.split(",")
                
                # 1. Update Visibility Items (Lights, Cards, Priority) 
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

                # 2. Update Scores 
                if len(parts) > 6:
                    score_l, score_r = parts[5], parts[6]
                    if score_l != last_score_l:
                        obs.call(requests.SetInputSettings(inputName="Left_Score_Text", inputSettings={"text": score_l}))
                        last_score_l = score_l
                    if score_r != last_score_r:
                        obs.call(requests.SetInputSettings(inputName="Right_Score_Text", inputSettings={"text": score_r}))
                        last_score_r = score_r

                # 3. Update Clock (MM:SS) 
                if len(parts) > 8:
                    mins, secs = parts[7], parts[8].zfill(2)
                    current_clock = f"{mins}:{secs}"
                    if current_clock != last_clock:
                        obs.call(requests.SetInputSettings(inputName="Clock_Text", inputSettings={"text": current_clock}))
                        last_clock = current_clock

    except KeyboardInterrupt:
        print("\nShutting down bridge...")
    finally:
        ser.close()
        obs.disconnect()

def get_item_id(client, scene_name, source_name):
    """Helper to find the internal OBS SceneItemID"""
    try:
        response = client.call(requests.GetSceneItemList(sceneName=scene_name))
        for item in response.getSceneItems():
            if item['sourceName'] == source_name:
                return item['sceneItemId']
    except:
        return None
    return None

if __name__ == "__main__":
    main()
