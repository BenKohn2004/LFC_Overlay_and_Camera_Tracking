#include <ESP8266WiFi.h>
#include <espnow.h>

// Must match the transmitter's struct exactly
struct FaveroMessage {
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
};

FaveroMessage incomingData;

// Callback function when data is received via ESP-NOW
void onDataRecv(uint8_t * mac, uint8_t *incomingRawData, uint8_t len) {
  // Check if received data size matches our struct
  if (len == sizeof(FaveroMessage)) {
    memcpy(&incomingData, incomingRawData, sizeof(incomingData));

    // Only process if it's a Favero message (msgType 0)
    if (incomingData.msgType == 0) {
      sendToPython();
    }
  }
}

void sendToPython() {
  // Header
  Serial.print("DATA,"); // Index 0
  
  // Lights & Cards (Matching VISIBILITY_MAP indices)
  Serial.print(incomingData.Red_Light ? "1," : "0,");          // Index 1
  Serial.print(incomingData.Green_Light ? "1," : "0,");        // Index 2
  Serial.print(incomingData.White_Red_Light ? "1," : "0,");    // Index 3
  Serial.print(incomingData.White_Green_Light ? "1," : "0,");  // Index 4
  
  // Scores
  Serial.print(incomingData.Left_Score);                       // Index 5
  Serial.print(",");
  Serial.print(incomingData.Right_Score);                      // Index 6
  Serial.print(",");
  
  // Clock
  Serial.print(incomingData.Minutes_Remaining);                // Index 7
  Serial.print(",");
  Serial.print(incomingData.Seconds_Remaining);                // Index 8
  Serial.print(",");
  
  // Cards & Priority (Indices 9-14)
  Serial.print(incomingData.Yellow_Card_Red ? "1," : "0,");    // Index 9
  Serial.print(incomingData.Yellow_Card_Green ? "1," : "0,");  // Index 10
  Serial.print(incomingData.Red_Card_Red ? "1," : "0,");       // Index 11
  Serial.print(incomingData.Red_Card_Green ? "1," : "0,");     // Index 12
  Serial.print(incomingData.Priority_Left ? "1," : "0,");      // Index 13
  Serial.print(incomingData.Priority_Right ? "1" : "0");       // Index 14
  
  // Newline to trigger Python's ser.readline()
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  
  // Set device as a Wi-Fi Station
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  // Init ESP-NOW
  if (esp_now_init() != 0) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  
  // Register the receive callback
  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(onDataRecv);
}

void loop() {
  // Nothing needed here, everything happens in the callback
}
