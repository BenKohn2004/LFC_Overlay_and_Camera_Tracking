#include <ESP8266WiFi.h>
#include <espnow.h>

// --- Configuration ---
#define TEST_MODE false  // Set to true to simulate hits without a Favero machine
#define VERBOSE false    // Set to true to see debug info in Serial Monitor
#define BOX_NAME "Strip 1"

// REPLACE with the MAC Address of your RECEIVER Wemos
uint8_t receiverAddress[] = {0x12, 0x34, 0x56, 0x78, 0x90, 0xAB}; 

// Favero Protocol Constants
const char STARTING_BYTE = 255;
const unsigned int MAX_MESSAGE_LENGTH = 10;

// ESP-NOW Data Structure (Must match Receiver exactly)
typedef struct struct_message {
  uint8_t msgType;
  uint8_t macAddr[6];
  unsigned int Right_Score;
  unsigned int Left_Score;
  unsigned int Seconds_Remaining;
  unsigned int Minutes_Remaining;
  bool Green_Light;
  bool Red_Light;
  bool White_Green_Light;
  bool White_Red_Light;
  bool Yellow_Green_Light;
  bool Yellow_Red_Light;
  bool Yellow_Card_Green;
  bool Yellow_Card_Red;
  bool Red_Card_Green;
  bool Red_Card_Red;
  bool Priority_Left;
  bool Priority_Right;
  char customMessage[32];
} struct_message;

struct_message myData;
unsigned int message_pos = 0;
bool new_data = false;
unsigned long lastTransmitTime = 0;
const unsigned long transmitInterval = 20; // 20ms heartbeat frequency

// --- Helper Functions ---

// Optimized BCD to Integer conversion
uint8_t bcdToInt(uint8_t bcd) {
  return ((bcd >> 4) * 10) + (bcd & 0x0F);
}

void OnDataSent(uint8_t *mac_addr, uint8_t sendStatus) {
  if (VERBOSE) {
    Serial.print("Delivery Status: ");
    Serial.println(sendStatus == 0 ? "Success" : "Fail");
  }
}

void Favero_Parser() {
  if (Serial.available() > 0) {
    static uint8_t message[MAX_MESSAGE_LENGTH];
    static uint8_t prev_message[MAX_MESSAGE_LENGTH];
    uint8_t inByte = Serial.read();

    // Align with the starting byte of the 10-byte packet
    if (inByte == STARTING_BYTE) {
      message_pos = 0;
    }

    if (message_pos < MAX_MESSAGE_LENGTH) {
      message[message_pos++] = inByte;
    }

    if (message_pos == MAX_MESSAGE_LENGTH) {
      // Check if data has changed compared to last valid packet
      bool changed = false;
      for (int i = 1; i < 9; i++) {
        if (message[i] != prev_message[i]) { 
          changed = true; 
          break; 
        }
      }

      if (changed) {
        new_data = true;
        
        // Map light states from Byte 5
        myData.White_Red_Light   = bitRead(message[5], 0);
        myData.White_Green_Light = bitRead(message[5], 1);
        myData.Red_Light         = bitRead(message[5], 2);
        myData.Green_Light       = bitRead(message[5], 3);
        myData.Yellow_Green_Light = bitRead(message[5], 4);
        myData.Yellow_Red_Light   = bitRead(message[5], 5);

        // Map scores and time using BCD conversion
        myData.Right_Score       = bcdToInt(message[1]);
        myData.Left_Score        = bcdToInt(message[2]);
        myData.Seconds_Remaining = bcdToInt(message[3]);
        myData.Minutes_Remaining = bcdToInt(message[4]);

        // Map Cards and Priority from Byte 6 and 8
        myData.Priority_Right    = bitRead(message[6], 2);
        myData.Priority_Left     = bitRead(message[6], 3);
        myData.Red_Card_Green    = bitRead(message[8], 0);
        myData.Red_Card_Red      = bitRead(message[8], 1);
        myData.Yellow_Card_Green = bitRead(message[8], 2);
        myData.Yellow_Card_Red   = bitRead(message[8], 3);
        
        memcpy(prev_message, message, MAX_MESSAGE_LENGTH);
      }
      message_pos = 0;
    }
  }
}

void setup() {
  // 2400 baud is the standard for Favero 05 serial output
  Serial.begin(TEST_MODE ? 115200 : 2400); 
  
  WiFi.mode(WIFI_STA);
  WiFi.macAddress(myData.macAddr);
  strncpy(myData.customMessage, BOX_NAME, 32);

  if (esp_now_init() != 0) {
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_register_send_cb(OnDataSent);
  
  // Register the specific receiver MAC address
  esp_now_add_peer(receiverAddress, ESP_NOW_ROLE_SLAVE, 1, NULL, 0); 
}

void loop() {
  if (TEST_MODE) {
    // Basic toggle logic for testing transmission without a machine
    if (millis() - lastTransmitTime > 2000) {
      myData.Red_Light = !myData.Red_Light;
      new_data = true;
    }
  } else {
    Favero_Parser();
  }

  // Send update if data changed or as a periodic heartbeat
  if (new_data || (millis() - lastTransmitTime > transmitInterval)) {
    esp_now_send(receiverAddress, (uint8_t *)&myData, sizeof(myData));
    lastTransmitTime = millis();
    new_data = false;
  }
}
