#include <ESP8266WiFi.h>
#include <espnow.h>

// --- Configuration Constants ---
#define TEST_MODE true
#define VERBOSE false
#define RELAY_ENABLED false
#define BOX_NAME "Strip 1"
#define TRANSMIT_INTERVAL 10 

// Specific Receiver MAC - Replace with your actual Receiver MAC
uint8_t receiverAddress[] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

// Pin Definitions
const int pins[] = {4, 5, 14, 12}; // Red, Green, WhiteRed, WhiteGreen [cite: 14, 15]

// Data Structure [cite: 20, 24]
typedef struct struct_message {
  uint8_t msgType;
  uint8_t macAddr[6];
  unsigned int Right_Score, Left_Score;
  unsigned int Seconds_Remaining, Minutes_Remaining;
  bool lights[6]; // Index: 0=Red, 1=Green, 2=W_Red, 3=W_Green, 4=Y_Red, 5=Y_Green
  bool cards[4];  // Index: 0=Y_Red, 1=Y_Green, 2=R_Red, 3=R_Green
  bool priority[2]; // 0=Left, 1=Right
  char customMessage[32];
} struct_message;

struct_message myData;
unsigned long lastTransmitTime = 0;
unsigned long lastChangeTime = 0;

void OnDataSent(uint8_t *mac_addr, uint8_t sendStatus) {
  if (VERBOSE) {
    Serial.printf("%s: Send Status: %s\n", BOX_NAME, sendStatus == 0 ? "Success" : "Fail"); [cite: 25, 27]
  }
}

void setup() {
  Serial.begin(TEST_MODE ? 115200 : 2400); [cite: 30, 31]
  
  WiFi.mode(WIFI_STA);
  WiFi.macAddress(myData.macAddr); [cite: 33]
  strncpy(myData.customMessage, BOX_NAME, 32); [cite: 34]

  if (esp_now_init() != 0) return; [cite: 35]

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER); [cite: 37]
  esp_now_register_send_cb(OnDataSent); [cite: 38]
  esp_now_add_peer(receiverAddress, ESP_NOW_ROLE_SLAVE, 1, NULL, 0); [cite: 38]

  for(int p : pins) pinMode(p, OUTPUT); [cite: 36]
}

void loop() {
  if (TEST_MODE) {
    handleTestMode(); // Toggle lights for testing [cite: 40]
  } else {
    Favero_Parser(); // Real Favero Data [cite: 49]
  }

  // Periodic Transmission [cite: 50, 51]
  if (millis() - lastTransmitTime > TRANSMIT_INTERVAL) {
    esp_now_send(receiverAddress, (uint8_t *)&myData, sizeof(myData));
    lastTransmitTime = millis();
  }

  if (RELAY_ENABLED) updateRelays(); [cite: 53, 57]
}

void updateRelays() {
  digitalWrite(4, myData.lights[0] ? HIGH : LOW); // Red [cite: 53, 54]
  digitalWrite(5, myData.lights[1] ? HIGH : LOW); // Green [cite: 55, 56]
  // Add others as needed...
}
