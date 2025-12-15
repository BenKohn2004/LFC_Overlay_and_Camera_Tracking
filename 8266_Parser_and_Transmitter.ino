#include <ESP8266WiFi.h>
#include <espnow.h>

// --- Configuration ---
#define TEST_MODE false  // Set to true to simulate hits without a Favero machine
#define VERBOSE false   // Set to true to see debug info in Serial Monitor
#define BOX_NAME "Strip 1"

// REPLACE with the MAC Address of your RECEIVER Wemos
uint8_t receiverAddress[] = {0x12, 0x34, 0x56, 0x78, 0x90, 0xAB}; 

// Favero Constants
const char STARTING_BYTE = 255; [cite: 44]
const unsigned int MAX_MESSAGE_LENGTH = 10; [cite: 43]
const unsigned int MAX_SERIAL_BUFFER_BYTES = 128; [cite: 43]

// ESP-NOW Data Structure (Must match Receiver exactly)
typedef struct struct_message {
  uint8_t msgType;
  uint8_t macAddr[6];
  unsigned int Right_Score; [cite: 51]
  unsigned int Left_Score; [cite: 52]
  unsigned int Seconds_Remaining; [cite: 52]
  unsigned int Minutes_Remaining; [cite: 52]
  bool Green_Light; [cite: 52]
  bool Red_Light; [cite: 52]
  bool White_Green_Light; [cite: 52]
  bool White_Red_Light; [cite: 52]
  bool Yellow_Green_Light; [cite: 52]
  bool Yellow_Red_Light; [cite: 53]
  bool Yellow_Card_Green; [cite: 53]
  bool Yellow_Card_Red; [cite: 53]
  bool Red_Card_Green; [cite: 53]
  bool Red_Card_Red; [cite: 53]
  bool Priority_Left; [cite: 54]
  bool Priority_Right; [cite: 54]
  char customMessage[32]; [cite: 54]
} struct_message;

struct_message myData;
unsigned int message_pos = 0; [cite: 46]
bool new_data = false; [cite: 46]
unsigned long lastTransmitTime = 0; [cite: 47]
const unsigned long transmitInterval = 20; // 20ms heartbeat

// --- Helper Functions ---

// Optimized BCD to Integer conversion [cite: 47-57]
uint8_t bcdToInt(uint8_t bcd) {
  return ((bcd >> 4) * 10) + (bcd & 0x0F);
}

void OnDataSent(uint8_t *mac_addr, uint8_t sendStatus) {
  if (VERBOSE) {
    Serial.printf("Delivery %s\n", sendStatus == 0 ? "Success" : "Fail"); [cite: 55-58]
  }
}

void Favero_Parser() {
  if (Serial.available() > 0) {
    static uint8_t message[MAX_MESSAGE_LENGTH]; [cite: 12]
    static uint8_t prev_message[MAX_MESSAGE_LENGTH]; [cite: 13]
    uint8_t inByte = Serial.read(); [cite: 13]

    if (inByte == STARTING_BYTE) { [cite: 13]
      message_pos = 0;
    }

    if (message_pos < MAX_MESSAGE_LENGTH) { [cite: 15]
      message[message_pos++] = inByte; [cite: 16]
    }

    if (message_pos == MAX_MESSAGE_LENGTH) { [cite: 16]
      // Only process if data has changed [cite: 17]
      bool changed = false;
      for (int i = 1; i < 9; i++) {
        if (message[i] != prev_message[i]) { changed = true; break; }
      }

      if (changed) {
        new_data = true;
        // Map data from BCD and Bits [cite: 21-25]
        myData.Red_Light         = bitRead(message[5], 2);
        myData.Green_Light       = bitRead(message[5], 3);
        myData.White_Red_Light   = bitRead(message[5], 0);
        myData.White_Green_Light = bitRead(message[5], 1);
        myData.Left_Score        = bcdToInt(message[2]);
        myData.Right_Score       = bcdToInt(message[1]);
        myData.Seconds_Remaining = bcdToInt(message[3]);
        myData.Minutes_Remaining = bcdToInt(message[4]);
        
        memcpy(prev_message, message, MAX_MESSAGE_LENGTH); [cite: 25]
      }
      message_pos = 0;
    }
  }
}

void setup() {
  Serial.begin(TEST_MODE ? 115200 : 2400); [cite: 60-61]
  
  WiFi.mode(WIFI_STA); [cite: 61]
  WiFi.macAddress(myData.macAddr); [cite: 62]
  strcpy(myData.customMessage, BOX_NAME); [cite: 64]

  if (esp_now_init() != 0) return; [cite: 65]

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER); [cite: 67]
  esp_now_register_send_cb(OnDataSent); [cite: 68]
  
  // Register specific receiver instead of broadcast
  esp_now_add_peer(receiverAddress, ESP_NOW_ROLE_SLAVE, 1, NULL, 0); 
}

void loop() {
  if (TEST_MODE) {
    // Basic toggle logic for testing without machine
    if (millis() - lastTransmitTime > 2000) {
      myData.Red_Light = !myData.Red_Light;
      new_data = true;
    }
  } else {
    Favero_Parser(); [cite: 79]
  }

  // Transmit if data is new or for heartbeat [cite: 80]
  if (new_data || (millis() - lastTransmitTime > transmitInterval)) {
    esp_now_send(receiverAddress, (uint8_t *)&myData, sizeof(myData)); [cite: 81]
    lastTransmitTime = millis(); [cite: 80]
    new_data = false; [cite: 83]
  }
}
