#include <ESP8266WiFi.h>
#include <espnow.h>
#include <SoftwareSerial.h>

// ---------------- CONFIG ----------------
#define QR_RX D5   // Scanner TX → ESP RX
#define QR_TX D6   // Scanner RX → ESP TX (optional)

uint8_t receiverMAC[] = {0xE0, 0x98, 0x06, 0x9D, 0x3F, 0x3C}; // Receiver ESP8266

SoftwareSerial qrSerial(QR_RX, QR_TX);

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(115200);     // USB debug
  qrSerial.begin(9600);     // Most Waveshare scanners default to 9600

  WiFi.mode(WIFI_STA);

  if (esp_now_init() != 0) {
    Serial.println("❌ ESP-NOW init failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_add_peer(receiverMAC, ESP_NOW_ROLE_SLAVE, 1, NULL, 0);

  Serial.println("📷 QR Scanner ready");
}

// ---------------- LOOP ----------------
void loop() {
  static String buffer = "";

  while (qrSerial.available()) {
    char c = qrSerial.read();

    if (c == '\n' || c == '\r') {
      if (buffer.length() > 5) {
        String out = "QR," + buffer;
        esp_now_send(receiverMAC, (uint8_t*)out.c_str(), out.length() + 1);
        Serial.println(out); // debug
      }
      buffer = "";
    } else {
      buffer += c;
    }
  }
}
