#include <ESP8266WiFi.h>
#include <espnow.h>

// ---------------- Configuration ----------------
const char* STRIP_NAME = "Strip 10";
bool verbose = false;

// ---------------- Message Structure ----------------
// MUST match transmitter exactly
struct struct_message {
  uint8_t msgType;          // 0 = Favero data, 1 = QR data
  uint8_t macAddr[6];

  // Scoring
  unsigned int Right_Score;
  unsigned int Left_Score;
  unsigned int Seconds_Remaining;
  unsigned int Minutes_Remaining;

  // Lights
  bool Green_Light;
  bool Red_Light;
  bool White_Green_Light;
  bool White_Red_Light;
  bool Yellow_Green_Light;
  bool Yellow_Red_Light;

  // Cards
  bool Yellow_Card_Green;
  bool Yellow_Card_Red;
  bool Red_Card_Green;
  bool Red_Card_Red;

  // Priority
  bool Priority_Left;
  bool Priority_Right;

  // QR or strip identifier
  char customMessage[128];
};

struct struct_message incomingMessage;

// ---------------- ESP-NOW Receive Callback ----------------
void OnDataRecv(uint8_t* mac, uint8_t* incomingData, uint8_t len) {
  memcpy(&incomingMessage, incomingData, sizeof(incomingMessage));

  // ---------- Favero Scoring Data ----------
  if (incomingMessage.msgType == 0) {

    if (String(incomingMessage.customMessage) != STRIP_NAME) return;

    Serial.print("DATA,");

    // Lights
    Serial.print(incomingMessage.Red_Light);         Serial.print(",");
    Serial.print(incomingMessage.Green_Light);       Serial.print(",");
    Serial.print(incomingMessage.White_Red_Light);   Serial.print(",");
    Serial.print(incomingMessage.White_Green_Light); Serial.print(",");

    // Score & Time
    Serial.print(incomingMessage.Left_Score);        Serial.print(",");
    Serial.print(incomingMessage.Right_Score);       Serial.print(",");
    Serial.print(incomingMessage.Minutes_Remaining); Serial.print(",");
    Serial.print(incomingMessage.Seconds_Remaining); Serial.print(",");

    // Cards
    Serial.print(incomingMessage.Yellow_Card_Red);   Serial.print(",");
    Serial.print(incomingMessage.Yellow_Card_Green); Serial.print(",");
    Serial.print(incomingMessage.Red_Card_Red);      Serial.print(",");
    Serial.print(incomingMessage.Red_Card_Green);    Serial.print(",");

    // Priority
    Serial.print(incomingMessage.Priority_Left);     Serial.print(",");
    Serial.println(incomingMessage.Priority_Right);

    if (verbose) {
      Serial.printf("Score %d - %d\n",
        incomingMessage.Left_Score,
        incomingMessage.Right_Score);
    }
  }

  // ---------- QR Code Payload ----------
  else if (incomingMessage.msgType == 1) {
    // Pass JSON straight through to Python
    Serial.print("QR,");
    Serial.println(incomingMessage.customMessage);

    if (verbose) {
      Serial.println("QR received:");
      Serial.println(incomingMessage.customMessage);
    }
  }
}

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != 0) {
    Serial.println("❌ ESP-NOW init failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(OnDataRecv);

  Serial.println("✅ ESP8266 Receiver Ready");
}

// ---------------- Loop ----------------
void loop() {
  // Nothing needed — event driven
}
