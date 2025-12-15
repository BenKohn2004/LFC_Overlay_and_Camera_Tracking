import serial
import time
from obswebsocket import obsws, requests

# --- Configuration ---
COM_PORT = 'COM3'         # Update this to your Wemos COM port
BAUD_RATE = 115200
OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASS = "your_password" # Set in OBS Tools -> WebSocket Settings
SCENE_NAME = "Main"       # Your specified scene name

# Mapping CSV indices to your OBS Source Names 
SOURCE_MAP = {
    1: "Red_Light",
    2: "Green_Light",
    3: "White_Red_Light",
    4: "White_Green_Light"
}

def connect_obs():
    client = obsws(OBS_HOST, OBS_PORT, OBS_PASS)
    try:
        client.connect()
        print(f"Connected to OBS. Monitoring scene: {SCENE_NAME}")
        return client
    except Exception as e:
        print(f"Could not connect to OBS: {e}")
        return None

def main():
    obs = connect_obs()
    if not obs: return

    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
        print(f"Listening to Wemos on {COM_PORT}...")
    except Exception as e:
        print(f"Serial Error: {e}")
        return

    # Track states to prevent redundant WebSocket calls
    last_states = {name: None for name in SOURCE_MAP.values()}

    try:
        while True:
            line = ser.readline().decode('utf-8').strip()
            
            # Check for the data prefix from the Receiver 
            if line.startswith("DATA,"):
                parts = line.split(",")
                
                # Iterate through the four light types 
                for i in range(1, 5):
                    is_active = (parts[i] == "1")
                    source_name = SOURCE_MAP[i]
                    
                    # Only update OBS if the state has changed
                    if is_active != last_states[source_name]:
                        item_id = get_item_id(obs, SCENE_NAME, source_name)
                        
                        if item_id is not None:
                            obs.call(requests.SetSceneItemEnabled(
                                sceneName=SCENE_NAME,
                                sceneItemId=item_id,
                                sceneItemEnabled=is_active
                            ))
                            last_states[source_name] = is_active
                            status = "VISIBLE" if is_active else "HIDDEN"
                            print(f"{source_name} is now {status}")

    except KeyboardInterrupt:
        print("\nStopping Bridge...")
    finally:
        ser.close()
        obs.disconnect()

def get_item_id(client, scene_name, source_name):
    """Retrieves the internal OBS ID for a source name"""
    try:
        response = client.call(requests.GetSceneItemList(sceneName=scene_name))
        for item in response.getSceneItems():
            if item['sourceName'] == source_name:
                return item['sceneItemId']
    except Exception:
        return None
    return None

if __name__ == "__main__":
    main()
