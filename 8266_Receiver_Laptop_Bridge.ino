#include <ESP8266WiFi.h>
#include <espnow.h>

// Configuration
const char* STRIP_NAME = "Strip 1"; // Must match BOX_NAME in transmitter [cite: 88, 89]
bool verbose = false;               // Set to true for human-readable debugging

// Structure to hold the incoming message [cite: 90]
struct struct_message {
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

struct_message incomingMessage;

// Callback function that triggers when data is received via ESP-NOW [cite: 98]
void OnDataRecv(uint8_t* mac, uint8_t* incomingData, uint8_t len) {
  memcpy(&incomingMessage, incomingData, sizeof(incomingMessage));

  // Only process if the message belongs to this strip [cite: 99]
  if (String(incomingMessage.customMessage) == STRIP_NAME) {
    
    // OUTPUT FOR LAPTOP: Comma-separated values for easy Python parsing
    // Format: DATA,Red,Green,WhiteRed,WhiteGreen,LeftScore,RightScore,Min,Sec
    Serial.print("DATA,");
    Serial.print(incomingMessage.Red_Light);         Serial.print(",");
    Serial.print(incomingMessage.Green_Light);       Serial.print(",");
    Serial.print(incomingMessage.White_Red_Light);   Serial.print(",");
    Serial.print(incomingMessage.White_Green_Light); Serial.print(",");
    Serial.print(incomingMessage.Left_Score);        Serial.print(",");
    Serial.print(incomingMessage.Right_Score);       Serial.print(",");
    Serial.print(incomingMessage.Minutes_Remaining); Serial.print(",");
    Serial.println(incomingMessage.Seconds_Remaining);

    if (verbose) {
      Serial.printf("Received hit from %s: R:%d G:%d\n", 
                    incomingMessage.customMessage, 
                    incomingMessage.Red_Light, 
                    incomingMessage.Green_Light);
    }
  }
}

void setup() {
  // Higher baud rate for the laptop link 
  Serial.begin(115200);

  // Initialize WiFi as Station [cite: 95]
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  // Initialize ESP-NOW [cite: 96]
  if (esp_now_init() != 0) {
    return;
  }

  // Set role to slave/receiver and register callback [cite: 97]
  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(OnDataRecv);
}

void loop() {
  // No logic in loop; all actions happen in OnDataRecv callback
  delay(1); 
}
