#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <SoftwareSerial.h>

// ---------------- Configuration ----------------
#define BOX_NAME "Strip10"
#define VERBOSE true  // Set to true to see transmission logs

// WiFi network credentials
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";

// UDP target — broadcast by default so the Raspberry Pi just listens on
// UDP_PORT without needing a fixed IP. Once the Pi has a static address,
// replace with e.g. IPAddress(192, 168, 1, 50) to send to it directly.
IPAddress udpTarget(255, 255, 255, 255);
const uint16_t UDP_PORT = 4210;

WiFiUDP Udp;

// SoftwareSerial for the RS-485 Converter
SoftwareSerial BoxSerial(D7, D6);

// ---------------- UDP Payload ----------------
// Same packed layout as the ESP-NOW version: 67 bytes total.
// Python (Raspberry Pi) unpack:
//   import struct
//   fmt = "<B6s4I12?32s"   # msgType, mac, R/L score, sec, min, 12 flags, name
//   fields = struct.unpack(fmt, data)
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

// ---------------- UDP Transmit ----------------

void sendUdpUpdate() {
  Udp.beginPacket(udpTarget, UDP_PORT);
  Udp.write((uint8_t *)&myData, sizeof(myData));
  int result = Udp.endPacket();

  if (VERBOSE) {
    if (result == 1) Serial.println("UDP Packet Sent");
    else Serial.println("UDP Send Fail");
  }
}

// ---------------- Standard Arduino ----------------

void setup() {
  Serial.begin(115200);
  BoxSerial.begin(115200);

  WiFi.mode(WIFI_STA);
  WiFi.macAddress(myData.macAddr);
  strncpy(myData.customMessage, BOX_NAME, sizeof(myData.customMessage));

  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Wait up to ~15 s for the connection; the ESP8266 keeps retrying in the
  // background after this, so the parser starts either way.
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi Connected. IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi not connected yet - will keep retrying.");
  }

  Serial.println("System Ready. Filtering noise via Checksum + Offset.");
}

void loop() {
  Skewered_Parser();

  // Hold new_data until WiFi is up so the latest state isn't lost
  // during a dropout - it sends as soon as the connection returns.
  if (new_data && WiFi.status() == WL_CONNECTED) {
    sendUdpUpdate();
    new_data = false;
  }
}
