import serial
import time
from obswebsocket import obsws, requests

# --- Configuration ---
COM_PORT = 'COM3'         
BAUD_RATE = 115200
OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASS = "your_password" 
SCENE_NAME = "Main"

# Mapping CSV indices for visibility toggle (Lights and Cards) [cite: 111, 122]
VISIBILITY_MAP = {
    1: "Red_Light",
    2: "Green_Light",
    3: "White_Red_Light",
    4: "White_Green_Light",
    # Note: These indices depend on your Receiver's Serial.print order
    # If you update the Receiver to send card data, add indices 9, 10, 11, 12 here.
}

def connect_obs():
    client = obsws(OBS_HOST, OBS_PORT, OBS_PASS)
    try:
        client.connect()
        return client
    except Exception as e:
        print(f"Connection Error: {e}")
        return None

def main():
    obs = connect_obs()
    if not obs: return

    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
        print(f"Bridge Active on {COM_PORT}...")
    except Exception as e:
        print(f"Serial Error: {e}")
        return

    # Track states to minimize WebSocket traffic
    last_states = {}
    last_score_l = ""
    last_score_r = ""
    last_clock = ""

    try:
        while True:
            line = ser.readline().decode('utf-8').strip()
            
            if line.startswith("DATA,"):
                parts = line.split(",")
                
                # 1. Update Visibility (Lights) 
                for i in range(1, 5):
                    is_active = (parts[i] == "1")
                    source_name = VISIBILITY_MAP[i]
                    if is_active != last_states.get(source_name):
                        item_id = get_item_id(obs, SCENE_NAME, source_name)
                        if item_id:
                            obs.call(requests.SetSceneItemEnabled(
                                sceneName=SCENE_NAME, sceneItemId=item_id, sceneItemEnabled=is_active
                            ))
                            last_states[source_name] = is_active

                # 2. Update Scores [cite: 111, 135, 136]
                score_l, score_r = parts[5], parts[6]
                if score_l != last_score_l:
                    obs.call(requests.SetInputSettings(inputName="Left_Score_Text", inputSettings={"text": score_l}))
                    last_score_l = score_l
                if score_r != last_score_r:
                    obs.call(requests.SetInputSettings(inputName="Right_Score_Text", inputSettings={"text": score_r}))
                    last_score_r = score_r

                # 3. Update Clock (Format MM:SS) [cite: 111, 136]
                mins, secs = parts[7], parts[8].zfill(2)
                current_clock = f"{mins}:{secs}"
                if current_clock != last_clock:
                    obs.call(requests.SetInputSettings(inputName="Clock_Text", inputSettings={"text": current_clock}))
                    last_clock = current_clock

    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        ser.close()
        obs.disconnect()

def get_item_id(client, scene_name, source_name):
    try:
        response = client.call(requests.GetSceneItemList(sceneName=scene_name))
        for item in response.getSceneItems():
            if item['sourceName'] == source_name:
                return item['sceneItemId']
    except: return None
    return None

if __name__ == "__main__":
    main()
