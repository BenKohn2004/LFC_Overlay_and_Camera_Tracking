#include <ESP8266WiFi.h>
#include <espnow.h>

// ---------------- Configuration ----------------
#define TEST_MODE false     // true = simulate hits
#define VERBOSE   false
#define BOX_NAME "Strip 10"

// REPLACE with MAC of RECEIVER Wemos
uint8_t receiverAddress[] = {0xE0, 0x98, 0x06, 0x9D, 0x3F, 0x3C};

// Favero protocol
const uint8_t STARTING_BYTE = 255;
const uint8_t MAX_MESSAGE_LENGTH = 10;

// Optional clock heartbeat (ms)
const unsigned long CLOCK_HEARTBEAT = 1000;

// ---------------- ESP-NOW Payload ----------------
typedef struct struct_message {
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
} struct_message;

struct_message myData;
struct_message lastSent;

unsigned int message_pos = 0;
bool new_data = false;

unsigned long lastTransmitTime = 0;
unsigned long lastClockTransmit = 0;

// ---------------- Helpers ----------------
uint8_t bcdToInt(uint8_t bcd) {
  return ((bcd >> 4) * 10) + (bcd & 0x0F);
}

void OnDataSent(uint8_t *mac, uint8_t status) {
  if (VERBOSE) {
    Serial.println(status == 0 ? "ESP-NOW Send OK" : "ESP-NOW Send FAIL");
  }
}

// ---------------- Favero Parser ----------------
void Favero_Parser() {
  static uint8_t message[MAX_MESSAGE_LENGTH];
  static uint8_t prev_message[MAX_MESSAGE_LENGTH];

  while (Serial.available()) {
    uint8_t inByte = Serial.read();

    if (inByte == STARTING_BYTE) {
      message_pos = 0;
    }

    if (message_pos < MAX_MESSAGE_LENGTH) {
      message[message_pos++] = inByte;
    }

    if (message_pos == MAX_MESSAGE_LENGTH) {

      bool changed = false;
      for (int i = 1; i < 9; i++) {
        if (message[i] != prev_message[i]) {
          changed = true;
          break;
        }
      }

      if (changed) {
        new_data = true;

        // Byte 5 – lights
        myData.White_Red_Light    = bitRead(message[5], 0);
        myData.White_Green_Light  = bitRead(message[5], 1);
        myData.Red_Light          = bitRead(message[5], 2);
        myData.Green_Light        = bitRead(message[5], 3);
        myData.Yellow_Green_Light = bitRead(message[5], 4);
        myData.Yellow_Red_Light   = bitRead(message[5], 5);

        // Scores & time
        myData.Right_Score       = bcdToInt(message[1]);
        myData.Left_Score        = bcdToInt(message[2]);
        myData.Seconds_Remaining = bcdToInt(message[3]);
        myData.Minutes_Remaining = bcdToInt(message[4]);

        // Priority & cards
        myData.Priority_Right    = bitRead(message[6], 2);
        myData.Priority_Left     = bitRead(message[6], 3);
        myData.Red_Card_Green    = bitRead(message[8], 0);
        myData.Red_Card_Red      = bitRead(message[8], 1);
        myData.Yellow_Card_Green = bitRead(message[8], 2);
        myData.Yellow_Card_Red   = bitRead(message[8], 3);

        memcpy(prev_message, message, MAX_MESSAGE_LENGTH);
      }

      message_pos = 0;
    }
  }
}

// ---------------- Setup ----------------
void setup() {
  Serial.begin(TEST_MODE ? 115200 : 2400);

  WiFi.mode(WIFI_STA);
  WiFi.macAddress(myData.macAddr);
  strncpy(myData.customMessage, BOX_NAME, sizeof(myData.customMessage));

  if (esp_now_init() != 0) {
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_register_send_cb(OnDataSent);
  esp_now_add_peer(receiverAddress, ESP_NOW_ROLE_SLAVE, 1, NULL, 0);

  memset(&lastSent, 0, sizeof(lastSent));
}

// ---------------- Main Loop ----------------
void loop() {
  unsigned long now = millis();

  if (TEST_MODE) {
    if (now - lastTransmitTime > 2000) {
      myData.Red_Light = !myData.Red_Light;
      new_data = true;
    }
  } else {
    Favero_Parser();
  }

  // Send immediately on change
  if (new_data) {
    esp_now_send(receiverAddress, (uint8_t *)&myData, sizeof(myData));
    lastTransmitTime = now;
    lastClockTransmit = now;
    lastSent = myData;
    new_data = false;
  }

  // Optional 1-second heartbeat for clock stability
  else if (now - lastClockTransmit >= CLOCK_HEARTBEAT) {
    esp_now_send(receiverAddress, (uint8_t *)&myData, sizeof(myData));
    lastClockTransmit = now;
  }
}
