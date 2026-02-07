#include <ESP8266WiFi.h>
#include <espnow.h>
#include <SoftwareSerial.h>

// ---------------- Configuration ----------------
#define BOX_NAME "Strip10"
#define VERBOSE true  // Set to true to see transmission logs

// MAC address of your existing Receiver
// uint8_t receiverAddress[] = { 0xE0, 0x98, 0x06, 0x9D, 0x3F, 0x3C }; //Main Receiver
uint8_t receiverAddress[] = { 0xA4, 0xCF, 0x12, 0xFD, 0x44, 0xCF };  // Temp Receiver A4:CF:12:FD:44:CF

// SoftwareSerial for the RS-485 Converter
SoftwareSerial BoxSerial(D7, D6);

// ---------------- ESP-NOW Payload ----------------
struct __attribute__((packed)) FaveroMessage {
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

FaveroMessage myData;
bool new_data = false;

// ---------------- Helper Functions ----------------

bool isChecksumValid(uint8_t *packet) {
  uint8_t sum = 0;
  for (int i = 1; i < 14; i++) {
    sum += packet[i];
  }
  return (sum == (uint8_t)(packet[14] + 0x12));
}

void decodeCards(byte index13) {
  // 1. Reset all card flags in myData
  myData.Yellow_Card_Red = false;    // Left Yellow
  myData.Red_Card_Red = false;       // Left Red
  myData.Yellow_Card_Green = false;  // Right Yellow
  myData.Red_Card_Green = false;     // Right Red

  // 2. Decode Left Side (Low Nibble)
  byte leftCard = index13 & 0x0F;
  if (leftCard == 0x01) myData.Yellow_Card_Red = true;
  else if (leftCard == 0x02) myData.Red_Card_Red = true;


  // 3. Decode Right Side (High Nibble)
  byte rightCard = (index13 >> 4) & 0x0F;
  if (rightCard == 0x01) myData.Yellow_Card_Green = true;
  else if (rightCard == 0x02) myData.Red_Card_Green = true;

  // 4. Verbose Output
  if (VERBOSE) {
    if (myData.Yellow_Card_Red) Serial.println("-> Card Active: LEFT YELLOW");
    if (myData.Red_Card_Red) Serial.println("-> Card Active: LEFT RED");
    if (myData.Yellow_Card_Green) Serial.println("-> Card Active: RIGHT YELLOW");
    if (myData.Red_Card_Green) Serial.println("-> Card Active: RIGHT RED");
  }
}

void decodePriority(byte index2) {
  // 1. Extract the top two bits (PP) by shifting right 6 places
  byte pp = (index2 >> 6) & 0x03;

  // 2. Map to the myData struct
  // 0x01 (binary 01) = Left, 0x02 (binary 10) = Right
  myData.Priority_Left = (pp == 0x01);
  myData.Priority_Right = (pp == 0x02);

  // 3. Verbose Serial Output
  if (VERBOSE) {
    Serial.print("Priority Check (Index 2): ");
    Serial.print(bitRead(index2, 7));
    Serial.print(bitRead(index2, 6));

    if (myData.Priority_Left) {
      Serial.println(" -> Priority: LEFT");
    } else if (myData.Priority_Right) {
      Serial.println(" -> Priority: RIGHT");
    } else {
      Serial.println(" -> Priority: NONE");
    }
  }
}

void decodeTimer(byte b2, byte b3) {
  // 1. Get the last 2 bits of Byte 2 (the 'rr' part)
  // We mask with 0x03 (binary 00000011)
  uint16_t highBits = (b2 & 0x03);

  // 2. Shift those high bits up by 8 and add Byte 3
  // This creates a 10-bit number
  uint16_t totalSeconds = (highBits << 8) | b3;

  // 3. Convert to Minutes and Seconds
  myData.Minutes_Remaining = totalSeconds / 60;
  myData.Seconds_Remaining = totalSeconds % 60;

  if (VERBOSE) {
    // Show Byte 2 in binary to see the 'ffff rr' part
    Serial.print("Timer Raw - B2:[");
    for (int i = 7; i >= 0; i--) Serial.print(bitRead(b2, i));
    Serial.print("] B3:[");
    for (int i = 7; i >= 0; i--) Serial.print(bitRead(b3, i));

    Serial.print("] -> ");
    Serial.print(myData.Minutes_Remaining);
    Serial.print("m ");
    Serial.print(myData.Seconds_Remaining);
    Serial.println("s");
  }
}

void decodeScores(byte leftByte, byte rightByte) {
  if (VERBOSE) {
    Serial.print("Score Bits - Left: [");
    for (int i = 7; i >= 0; i--) {
      Serial.print(bitRead(leftByte, i));
      if (i == 4) Serial.print(" ");
    }
    Serial.print("] Right: [");
    for (int i = 7; i >= 0; i--) {
      Serial.print(bitRead(rightByte, i));
      if (i == 4) Serial.print(" ");
    }
    Serial.println("]");
  }

  myData.Left_Score = (unsigned int)(leftByte & 0x7F);
  myData.Right_Score = (unsigned int)(rightByte & 0x7F);

  if (VERBOSE) {
    Serial.print("-> Scores Updated (MSB Cleared): L ");
    Serial.print(myData.Left_Score);
    Serial.print(" - R ");
    Serial.println(myData.Right_Score);
    if (leftByte & 0x80) Serial.println("   [Note: Left had most recent touch]");
    if (rightByte & 0x80) Serial.println("   [Note: Right had most recent touch]");
  }
}

void decodeLights(byte index7) {
  if (VERBOSE) {
    Serial.print("Byte 7 Bits: [");
    for (int i = 7; i >= 0; i--) {
      Serial.print(bitRead(index7, i));
      if (i == 6 || i == 3) Serial.print(" ");
    }
    Serial.println("]");
  }

  myData.Green_Light = false;
  myData.Red_Light = false;
  myData.White_Green_Light = false;
  myData.White_Red_Light = false;

  byte leftStatus = (index7 >> 3) & 0x07;
  if (leftStatus == 1) {
    myData.Red_Light = true;
    if (VERBOSE) Serial.println("-> Added Red to myData.");
  } else if (leftStatus == 2) {
    myData.White_Red_Light = true;
    if (VERBOSE) Serial.println("-> Added White-Red to myData.");
  }

  byte rightStatus = index7 & 0x07;
  if (rightStatus == 1) {
    myData.Green_Light = true;
    if (VERBOSE) Serial.println("-> Added Green to myData.");
  } else if (rightStatus == 2) {
    myData.White_Green_Light = true;
    if (VERBOSE) Serial.println("-> Added White-Green to myData.");
  }
}

// ---------------- Skewered Parser ----------------
void Skewered_Parser() {
  static byte currentPacket[16];
  static byte lastPacket[16];
  static int pIdx = 0;

  while (BoxSerial.available()) {
    byte b = BoxSerial.read();

    if (b == 0xEE) pIdx = 0;

    if (pIdx < 16) {
      currentPacket[pIdx++] = b;
    }

    if (pIdx == 16) {
      if (isChecksumValid(currentPacket) && currentPacket[15] == 0xFF) {

        bool isDifferent = false;
        for (int i = 0; i < 16; i++) {
          // Ignore header, sync, time bytes, and checksum
          if (i == 0 || i == 1 || i == 6 || i == 8 || i == 9 || i == 10 || i == 14 || i == 15) continue;
          if (currentPacket[i] != lastPacket[i]) {
            isDifferent = true;
            break;
          }
        }

        if (isDifferent) {
          new_data = true;
          decodeCards(currentPacket[13]);
          decodePriority(currentPacket[2]);
          decodeTimer(currentPacket[3], currentPacket[4]);
          decodeLights(currentPacket[7]);
          decodeScores(currentPacket[11], currentPacket[12]);

          if (VERBOSE) {
            Serial.print("Packet Received: ");
            for (int i = 0; i < 16; i++) {
              if (currentPacket[i] < 0x10) Serial.print("0");
              Serial.print(currentPacket[i], HEX);
              Serial.print(" ");
            }
            Serial.println();
          }

          memcpy(lastPacket, currentPacket, 16);
        }
      }
      pIdx = 0;  // Reset for next search
    }
  }
}

// ---------------- Standard Arduino ----------------

void OnDataSent(uint8_t *mac, uint8_t status) {
  if (VERBOSE) {
    if (status == 0) Serial.println("ESP-NOW Delivery Success");
    else Serial.println("ESP-NOW Delivery Fail");
  }
}

void setup() {
  Serial.begin(115200);
  BoxSerial.begin(115200);

  WiFi.mode(WIFI_STA);
  WiFi.macAddress(myData.macAddr);
  strncpy(myData.customMessage, BOX_NAME, sizeof(myData.customMessage));

  if (esp_now_init() != 0) {
    Serial.println("ESP-NOW Init Failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_register_send_cb(OnDataSent);
  esp_now_add_peer(receiverAddress, ESP_NOW_ROLE_SLAVE, 1, NULL, 0);

  Serial.println("System Ready. Filtering noise via Checksum + Offset.");
}

void loop() {
  Skewered_Parser();

  if (new_data) {
    esp_now_send(receiverAddress, (uint8_t *)&myData, sizeof(myData));
    new_data = false;
  }
}