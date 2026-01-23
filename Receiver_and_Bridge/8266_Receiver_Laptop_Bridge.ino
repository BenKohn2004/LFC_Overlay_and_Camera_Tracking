#include <ESP8266WiFi.h>
#include <espnow.h>

// ================= MESSAGE STRUCTS =================

// Favero message (msgType = 0)
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

// QR message (msgType = 1)
struct QRMessage {
  uint8_t msgType;
  uint8_t isLeft;     // 1 = Left, 0 = Right
  char name[64];
  char club[64];
  char strip[32];
};

// ================= RX BUFFER =================

uint8_t rxBuffer[250];
uint8_t rxLen = 0;
volatile bool packetReady = false;

// ================= ESP-NOW CALLBACK =================
// ⚠️ DO NOT PRINT OR PARSE HERE
void OnDataRecv(uint8_t* mac, uint8_t* data, uint8_t len) {
  if (len > sizeof(rxBuffer)) return;

  memcpy(rxBuffer, data, len);
  rxLen = len;
  packetReady = true;
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("=== ESP8266 Receiver Boot ===");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != 0) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(OnDataRecv);

  Serial.println("Receiver ready");
}

// ================= LOOP =================
void loop() {
  if (!packetReady) return;
  packetReady = false;

  // ---------------- Favero ----------------
  if (rxLen == sizeof(FaveroMessage)) {
    FaveroMessage msg;
    memcpy(&msg, rxBuffer, sizeof(msg));
    msg.customMessage[31] = '\0';

    Serial.print("DATA,");
    Serial.print(msg.Red_Light);         Serial.print(",");
    Serial.print(msg.Green_Light);       Serial.print(",");
    Serial.print(msg.White_Red_Light);   Serial.print(",");
    Serial.print(msg.White_Green_Light); Serial.print(",");
    Serial.print(msg.Left_Score);        Serial.print(",");
    Serial.print(msg.Right_Score);       Serial.print(",");
    Serial.print(msg.Minutes_Remaining); Serial.print(",");
    Serial.print(msg.Seconds_Remaining); Serial.print(",");
    Serial.print(msg.Yellow_Card_Red);   Serial.print(",");
    Serial.print(msg.Yellow_Card_Green); Serial.print(",");
    Serial.print(msg.Red_Card_Red);      Serial.print(",");
    Serial.print(msg.Red_Card_Green);    Serial.print(",");
    Serial.print(msg.Priority_Left);     Serial.print(",");
    Serial.println(msg.Priority_Right);
  }

  // ---------------- QR ----------------
  else if (rxLen == sizeof(QRMessage)) {
    QRMessage msg;
    memcpy(&msg, rxBuffer, sizeof(msg));

    msg.name[63]  = '\0';
    msg.club[63]  = '\0';
    msg.strip[31] = '\0';

    // sanitize commas for Python safety
    for (int i = 0; msg.name[i];  i++) if (msg.name[i]  == ',') msg.name[i]  = ' ';
    for (int i = 0; msg.club[i];  i++) if (msg.club[i]  == ',') msg.club[i]  = ' ';
    for (int i = 0; msg.strip[i]; i++) if (msg.strip[i] == ',') msg.strip[i] = ' ';

    Serial.print("QR,");
    Serial.print(msg.isLeft ? "Left" : "Right");
    Serial.print(",");
    Serial.print(msg.name);
    Serial.print(",");
    Serial.print(msg.club);
    Serial.print(",");
    Serial.println(msg.strip);
  }

  // ---------------- Unknown ----------------
  else {
    Serial.print("UNKNOWN_PACKET_LEN,");
    Serial.println(rxLen);
  }
}
