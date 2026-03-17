#include <ESP8266WiFi.h>
#include <espnow.h>

// Set this to match the #define BoxName in your transmitter (e.g., "Strip 1", "Strip 2")
#define TargetBox "Strip 1"

// ================= MESSAGE STRUCTS =================
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
  char customMessage[32]; // This holds the BoxName from the transmitter
};

struct QRMessage {
  uint8_t msgType;
  uint8_t isLeft;
  char name[64];
  char club[64];
  char strip[32];
};

// ================= RX BUFFER =================
uint8_t rxBuffer[250];
uint8_t rxLen = 0;
volatile bool packetReady = false;

void OnDataRecv(uint8_t* mac, uint8_t* data, uint8_t len) {
  if (len > sizeof(rxBuffer)) return;
  memcpy(rxBuffer, data, len);
  rxLen = len;
  packetReady = true;
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.print("=== Receiver for: ");
  Serial.print(TargetBox);
  Serial.println(" Booting ===");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != 0) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(OnDataRecv);
}

void loop() {
  if (!packetReady) return;
  packetReady = false;

  // ---------------- Favero ----------------
  if (rxLen == sizeof(FaveroMessage)) {
    FaveroMessage msg;
    memcpy(&msg, rxBuffer, sizeof(msg));
    msg.customMessage[31] = '\0'; // Ensure null termination

    // Check if the message came from the box we care about
    if (strcmp(msg.customMessage, TargetBox) == 0) {
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
    // Optional: Ignore messages from other boxes silently
  }

  // ---------------- QR ----------------
  else if (rxLen == sizeof(QRMessage)) {
    QRMessage msg;
    memcpy(&msg, rxBuffer, sizeof(msg));
    
    // Check if the QR strip name matches our TargetBox
    if (strcmp(msg.strip, TargetBox) == 0) {
      msg.name[63]  = '\0';
      msg.club[63]  = '\0';
      msg.strip[31] = '\0';

      for (int i = 0; msg.name[i];  i++) if (msg.name[i]  == ',') msg.name[i]  = ' ';
      for (int i = 0; msg.club[i];  i++) if (msg.club[i]  == ',') msg.club[i]  = ' ';

      Serial.print("QR,");
      Serial.print(msg.isLeft ? "Left" : "Right");
      Serial.print(",");
      Serial.print(msg.name);
      Serial.print(",");
      Serial.print(msg.club);
      Serial.print(",");
      Serial.println(msg.strip);
    }
  }
}
