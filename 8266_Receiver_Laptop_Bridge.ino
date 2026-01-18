#include <ESP8266WiFi.h>
#include <espnow.h>

// ---------------- Configuration ----------------
const char* STRIP_NAME = "Strip 10";
bool verbose = false;

const unsigned long QR_COOLDOWN_MS = 500;
unsigned long lastQRTime = 0;

// ---------------- Message Structure ----------------
struct struct_message {
  uint8_t msgType;          // 0 = Favero, 1 = QR
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

  char customMessage[128];  // JSON
};

struct struct_message incomingMessage;

// ---------------- Utility ----------------
bool isValidQRJson(const char* json) {
  String s = String(json);

  // Basic framing
  if (!s.startsWith("{") || !s.endsWith("}")) return false;

  // Required fields
  if (s.indexOf("\"side\"") == -1) return false;
  if (s.indexOf("\"name\"") == -1) return false;

  return true;
}

// ---------------- ESP-NOW Receive Callback ----------------
void OnDataRecv(uint8_t* mac, uint8_t* incomingData, uint8_t len) {
  memcpy(&incomingMessage, incomingData, sizeof(incomingMessage));

  // ---------- Favero Data ----------
  if (incomingMessage.msgType == 0) {

    if (String(incomingMessage.customMessage) != STRIP_NAME) return;

    Serial.print("DATA,");

    Serial.print(incomingMessage.Red_Light);         Serial.print(",");
    Serial.print(incomingMessage.Green_Light);       Serial.print(",");
    Serial.print(incomingMessage.White_Red_Light);   Serial.print(",");
    Serial.print(incomingMessage.White_Green_Light); Serial.print(",");

    Serial.print(incomingMessage.Left_Score);        Serial.print(",");
    Serial.print(incomingMessage.Right_Score);       Serial.print(",");
    Serial.print(incomingMessage.Minutes_Remaining); Serial.print(",");
    Serial.print(incomingMessage.Seconds_Remaining); Serial.print(",");

    Serial.print(incomingMessage.Yellow_Card_Red);   Serial.print(",");
    Serial.print(incomingMessage.Yellow_Card_Green); Serial.print(",");
    Serial.print(incomingMessage.Red_Card_Red);      Serial.print(",");
    Serial.print(incomingMessage.Red_Card_Green);    Serial.print(",");

    Serial.print(incomingMessage.Priority_Left);     Serial.print(",");
    Serial.println(incomingMessage.Priority_Right);

    if (verbose) {
      Serial.printf("Score %d - %d\n",
        incomingMessage.Left_Score,
        incomingMessage.Right_Score);
    }
  }

  // ---------- QR Payload ----------
  else if (incomingMessage.msgType == 1) {

    unsigned long now = millis();
    if (now - lastQRTime < QR_COOLDOWN_MS) return;
    lastQRTime = now;

    if (!isValidQRJson(incomingMessage.customMessage)) {
      if (verbose) {
        Serial.println("⚠ Invalid QR JSON dropped:");
        Serial.println(incomingMessage.customMessage);
      }
      return;
    }

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
  // Event driven
}
