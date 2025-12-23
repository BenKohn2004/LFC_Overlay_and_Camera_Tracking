#include <ESP8266WiFi.h>
#include <espnow.h>

// Configuration
const char* STRIP_NAME = "Strip 10"; 
bool verbose = false;

// Structure must match Transmitter exactly [cite: 107, 120-122]
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

void OnDataRecv(uint8_t* mac, uint8_t* incomingData, uint8_t len) {
  memcpy(&incomingMessage, incomingData, sizeof(incomingMessage));

  if (String(incomingMessage.customMessage) == STRIP_NAME) {
    
    // OUTPUT FOR LAPTOP: Expanded CSV for Python parsing [cite: 111]
    Serial.print("DATA,");
    // Indices 1-4: Lights [cite: 111, 132-134]
    Serial.print(incomingMessage.Red_Light);         Serial.print(",");
    Serial.print(incomingMessage.Green_Light);       Serial.print(",");
    Serial.print(incomingMessage.White_Red_Light);   Serial.print(",");
    Serial.print(incomingMessage.White_Green_Light); Serial.print(",");
    
    // Indices 5-8: Score & Time [cite: 111, 135-136]
    Serial.print(incomingMessage.Left_Score);        Serial.print(",");
    Serial.print(incomingMessage.Right_Score);       Serial.print(",");
    Serial.print(incomingMessage.Minutes_Remaining); Serial.print(",");
    Serial.print(incomingMessage.Seconds_Remaining); Serial.print(",");

    // Indices 9-12: Cards [cite: 139]
    Serial.print(incomingMessage.Yellow_Card_Red);   Serial.print(",");
    Serial.print(incomingMessage.Yellow_Card_Green); Serial.print(",");
    Serial.print(incomingMessage.Red_Card_Red);      Serial.print(",");
    Serial.print(incomingMessage.Red_Card_Green);    Serial.print(",");

    // Indices 13-14: Priority [cite: 137-138]
    Serial.print(incomingMessage.Priority_Left);     Serial.print(",");
    Serial.println(incomingMessage.Priority_Right); 

    if (verbose) {
      Serial.printf("Strip: %s | Score: %d-%d\n", STRIP_NAME, incomingMessage.Left_Score, incomingMessage.Right_Score);
    }
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != 0) return;

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(OnDataRecv);
}

void loop() {
  delay(1); 
}
