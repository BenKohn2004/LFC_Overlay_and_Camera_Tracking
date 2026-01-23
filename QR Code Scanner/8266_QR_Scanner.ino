#include <ESP8266WiFi.h>
#include <espnow.h>
#include <SoftwareSerial.h>

// ---------------- Configuration ----------------
#define QR_RX_PIN D5       // Waveshare Tx → D5
#define QR_COOLDOWN_MS 500 // minimum time between sending same QR

// ---------------- ESP-NOW ----------------
uint8_t receiverAddress[] = {0xE0, 0x98, 0x06, 0x9D, 0x3F, 0x3C};

// ---------------- Message Structure ----------------
struct struct_message {
  uint8_t msgType;          // 0 = Favero, 1 = QR
  uint8_t macAddr[6];       // sender MAC
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
  char customMessage[128];  // QR JSON
};

struct struct_message qrMessage;

// ---------------- Software Serial ----------------
SoftwareSerial qrSerial(QR_RX_PIN, -1); // RX only, no TX

// ---------------- State ----------------
unsigned long lastQRTime = 0;

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);
  qrSerial.begin(9600); // Waveshare default baud

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != 0) {
    Serial.println("❌ ESP-NOW init failed");
    return;
  }

  // Add receiver as peer
  esp_now_add_peer(receiverAddress, ESP_NOW_ROLE_COMBO, 1, NULL, 0);

  // Fill sender MAC
  WiFi.macAddress(qrMessage.macAddr);
  qrMessage.msgType = 1; // QR

  Serial.println("✅ QR Transmitter Ready");
}

// ---------------- Main Loop ----------------
void loop() {
  // Check if scanner sent data
  if (qrSerial.available()) {
    String qrLine = qrSerial.readStringUntil('\n');
    qrLine.trim(); // remove whitespace/newlines
    if (qrLine.length() == 0) return;

    // Cooldown to prevent sending the same QR repeatedly too fast
    unsigned long now = millis();
    if (now - lastQRTime < QR_COOLDOWN_MS) return;
    lastQRTime = now;

    // Copy QR string into struct_message
    qrLine.toCharArray(qrMessage.customMessage, sizeof(qrMessage.customMessage));

    // Send via ESP-NOW
    esp_now_send(receiverAddress, (uint8_t*)&qrMessage, sizeof(qrMessage));

    Serial.print("QR Sent: ");
    Serial.println(qrLine);
  }
}
