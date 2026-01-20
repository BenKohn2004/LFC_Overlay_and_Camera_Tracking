#include <ESP8266WiFi.h>
#include <espnow.h>
#include <SoftwareSerial.h>
#include <ArduinoJson.h>  // For parsing QR code JSON

// ---------------- CONFIG ----------------
#define QR_RX D5   // Scanner TX → ESP RX
#define QR_TX D6   // Scanner RX → ESP TX (optional)

uint8_t receiverMAC[] = {0xE0, 0x98, 0x06, 0x9D, 0x3F, 0x3C}; // Receiver ESP8266

SoftwareSerial qrSerial(QR_RX, QR_TX);

// ---------------- QR STRUCT ----------------
struct QRMessage {
  uint8_t msgType; // 1
  uint8_t isLeft;  // 1 = Left, 0 = Right
  char name[64];
  char club[64];
  char strip[32];
};

// ---------------- GLOBALS ----------------
const char* STRIP_NAME = "Strip 10";

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(115200);        // Debug output
  qrSerial.begin(9600);        // Waveshare scanner default

  WiFi.mode(WIFI_STA);

  if (esp_now_init() != 0) {
    Serial.println("❌ ESP-NOW init failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_add_peer(receiverMAC, ESP_NOW_ROLE_SLAVE, 1, NULL, 0);

  Serial.println("📷 QR Scanner Ready");
}

// ---------------- LOOP ----------------
void loop() {
  static String buffer = "";

  while (qrSerial.available()) {
    char c = qrSerial.read();

    if (c == '\n' || c == '\r') {
      if (buffer.length() > 5) {  // ignore very short lines
        // Parse JSON
        StaticJsonDocument<256> doc;
        DeserializationError err = deserializeJson(doc, buffer);
        if (!err) {
          QRMessage msg;
          msg.msgType = 1;
          msg.isLeft = (doc["side"] == "L") ? 1 : 0;

          strncpy(msg.name, doc["name"] | "", sizeof(msg.name)-1);
          msg.name[sizeof(msg.name)-1] = '\0';

          strncpy(msg.club, doc["club"] | "", sizeof(msg.club)-1);
          msg.club[sizeof(msg.club)-1] = '\0';

          strncpy(msg.strip, STRIP_NAME, sizeof(msg.strip)-1);
          msg.strip[sizeof(msg.strip)-1] = '\0';

          // Send via ESP-NOW
          uint8_t result = esp_now_send(receiverMAC, (uint8_t*)&msg, sizeof(msg));

          // Debug Serial output
          Serial.print("Sent QRMessage: ");
          Serial.print(msg.isLeft ? "Left | " : "Right | ");
          Serial.print(msg.name); Serial.print(" | ");
          Serial.print(msg.club); Serial.print(" | ");
          Serial.print(msg.strip); Serial.print(" | Result: ");
          if(result == 0) Serial.println("Success ✅");
          else Serial.println("Fail ❌");
        } else {
          Serial.print("⚠ JSON parse failed: ");
          Serial.println(buffer);
        }
      }
      buffer = "";  // clear for next QR code
    } else {
      buffer += c;
    }
  }
}
