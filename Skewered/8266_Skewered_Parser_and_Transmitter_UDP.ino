#include <ESP8266WiFi.h>
#include <WiFiUdp.h>

// ---------------- Configuration ----------------
#define BOX_NAME "Strip10"
#define VERBOSE false  // Set to true to see transmission logs. Keep false in
                       // deployment: at up to 100 tx/s the blocking Serial
                       // prints slow the parse loop (and this board's FTDI
                       // serial output is untrustworthy anyway).

// WiFi network credentials
// USE_SOFTAP true  = the ESP broadcasts its own "SkeweredNet" network and the
//                    receiver (PC / Raspberry Pi) joins it. Good for bench tests.
// USE_SOFTAP false = the ESP joins an existing hotspot with these credentials
//                    (e.g. one broadcast by the Raspberry Pi 5).
#define USE_SOFTAP true
const char* WIFI_SSID = "SkeweredNet";
const char* WIFI_PASSWORD = "skewered1234";

// UDP target: the subnet broadcast address (x.x.x.255), computed at runtime
// from our own IP so it works on any /24 network (SoftAP's 192.168.4.x, the
// Pi hotspot's subnet, etc.). The ESP8266 lwIP stack does NOT reliably send
// the limited broadcast 255.255.255.255 out of the SoftAP interface.
const uint16_t UDP_PORT = 4210;

IPAddress udpTarget() {
#if USE_SOFTAP
  IPAddress ip = WiFi.softAPIP();
#else
  IPAddress ip = WiFi.localIP();
#endif
  ip[3] = 255;
  return ip;
}

WiFiUDP Udp;

// Box RS-485 input on the HARDWARE UART, not bit-banged SoftwareSerial.
// SoftwareSerial at 115200 starved interrupts under live box data and tripped
// the ESP8266 hardware watchdog (reset every 1-2 min; confirmed via the debug
// beacon's rst_reason = HW-watchdog while the loop was still running at full
// speed). After Serial.swap() UART0 RX moves to GPIO13 = D7 -- exactly where
// the converter's TTL RX is already wired -- so NO rewiring is needed. The
// trade-off is losing USB serial (we rely on the WiFi debug beacon; this
// board's FTDI was counterfeit anyway).
HardwareSerial &BoxSerial = Serial;

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

// Debug telemetry counters (reported once per second on port 4211)
uint32_t loopCount = 0;
uint32_t rxBytes = 0;
uint32_t validPackets = 0;
uint32_t sendFails = 0;
unsigned long lastGoodSend = 0;   // millis of last successful endPacket

// Serial self-heal: if the box line goes silent (boot-order wedge: the
// converter powering up after the UART leaves RX stuck in a break state),
// re-initialize the UART + pin until data flows.
unsigned long lastRxByte = 0;     // millis of last byte from the box
unsigned long lastReinit = 0;
uint32_t serialReinits = 0;
const unsigned long SERIAL_SILENT_REINIT_MS = 10000;

void initBoxSerial() {
  Serial.end();
  // pull-up BEFORE begin/swap: the RS-485 converter's output needs RX idled
  // high, and setting it after the swap re-muxes the pin off the UART
  pinMode(13, INPUT_PULLUP);
  Serial.begin(115200);
  Serial.swap();   // UART0 RX -> GPIO13 = D7 (where the converter is wired)
}

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
  // Nibble layout is PP|NN: high 2 bits = p-card, low 2 bits = normal card.
  // Mask to 2 bits so an active p-card doesn't hide the normal card.
  byte leftCard = index13 & 0x03;
  if (leftCard == 0x01) myData.Yellow_Card_Red = true;
  else if (leftCard == 0x02) myData.Red_Card_Red = true;

  // 3. Decode Right Side (High Nibble)
  byte rightCard = (index13 >> 4) & 0x03;
  if (rightCard == 0x01) myData.Yellow_Card_Green = true;
  else if (rightCard == 0x02) myData.Red_Card_Green = true;

  // P-cards (passivity) — not in the payload yet, log only
  if (VERBOSE) {
    byte leftPCard = (index13 >> 2) & 0x03;
    byte rightPCard = (index13 >> 6) & 0x03;
    if (leftPCard) Serial.println(leftPCard == 1 ? "-> P-Card: LEFT YELLOW" : "-> P-Card: LEFT RED");
    if (rightPCard) Serial.println(rightPCard == 1 ? "-> P-Card: RIGHT YELLOW" : "-> P-Card: RIGHT RED");
  }

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

  // Byte 2 flags: | 00 ffff rr | -> bit 3 = centiseconds flag.
  // When time remaining is 10s or less the box sends CENTIseconds.
  if (b2 & 0x08) {
    totalSeconds = totalSeconds / 100;
  }

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

  // Bound the drain time: with a floating/weakly-biased RS-485 line the
  // converter chatters continuously, and an unbounded while() never returns —
  // starving loop() and the UDP heartbeat until the watchdog reboots us into
  // the same noise. 8 ms per call keeps worst-case heartbeat latency low
  // while draining far faster than 115200 baud can refill.
  unsigned long drainStart = millis();
  while (BoxSerial.available() && millis() - drainStart < 8) {
    byte b = BoxSerial.read();
    rxBytes++;
    lastRxByte = millis();

    if (b == 0xEE) pIdx = 0;

    if (pIdx < 16) {
      currentPacket[pIdx++] = b;
    }

    if (pIdx == 16) {
      if (isChecksumValid(currentPacket) && currentPacket[15] == 0xFF) {
        validPackets++;

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
  Udp.beginPacket(udpTarget(), UDP_PORT);
  Udp.write((uint8_t *)&myData, sizeof(myData));
  int result = Udp.endPacket();
  if (result != 1) sendFails++;
  else lastGoodSend = millis();

  if (VERBOSE) {
    if (result == 1) Serial.println("UDP Packet Sent");
    else Serial.println("UDP Send Fail");
  }
}

// ---------------- Debug telemetry (port 4211) ----------------
// One packet per second with internal health counters, so a wedge can be
// diagnosed over the air (the COM5 serial adapter is untrustworthy).
// Python unpack: struct.unpack("<B6IBBB", data)  -> 28 bytes
//   magic 0xDD, uptime_ms, loop_count, rx_bytes, valid_packets,
//   send_fails, free_heap, stations, rst_reason, serial_reinits (capped 255)
// rst_reason (ESP8266 rst_info.reason): 0=power-on 1=HW watchdog
//   2=exception/crash 3=SW watchdog 4=ESP.restart() (our dead-man)
//   5=deep-sleep wake 6=external reset pin. Brownouts usually show as 0 or 6.

const uint16_t DEBUG_PORT = 4211;
uint8_t g_rst_reason = 0;      // captured once in setup()

struct __attribute__((packed)) DebugMessage {
  uint8_t magic;
  uint32_t uptime_ms;
  uint32_t loop_count;
  uint32_t rx_bytes;
  uint32_t valid_packets;
  uint32_t send_fails;
  uint32_t free_heap;
  uint8_t stations;
  uint8_t rst_reason;
  uint8_t serial_reinits;
};

void sendDebug() {
  DebugMessage d;
  d.magic = 0xDD;
  d.uptime_ms = millis();
  d.loop_count = loopCount;
  d.rx_bytes = rxBytes;
  d.valid_packets = validPackets;
  d.send_fails = sendFails;
  d.free_heap = ESP.getFreeHeap();
  d.stations = WiFi.softAPgetStationNum();
  d.rst_reason = g_rst_reason;
  d.serial_reinits = serialReinits > 255 ? 255 : serialReinits;
  Udp.beginPacket(udpTarget(), DEBUG_PORT);
  Udp.write((uint8_t *)&d, sizeof(d));
  Udp.endPacket();
}

// ---------------- Standard Arduino ----------------

void setup() {
  g_rst_reason = ESP.getResetInfoPtr()->reason;   // why the last boot ended
  initBoxSerial();   // UART on D7 with RX pull-up; also used by the self-heal
  // (BoxSerial is a reference to Serial, so it is already started + swapped)

#if USE_SOFTAP
  WiFi.mode(WIFI_AP);
  WiFi.softAP(WIFI_SSID, WIFI_PASSWORD);
  WiFi.macAddress(myData.macAddr);
  Serial.print("SoftAP started: ");
  Serial.print(WIFI_SSID);
  Serial.print("  IP: ");
  Serial.println(WiFi.softAPIP());
#else
  WiFi.mode(WIFI_STA);
  WiFi.macAddress(myData.macAddr);

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
#endif

  strncpy(myData.customMessage, BOX_NAME, sizeof(myData.customMessage));
  Serial.println("System Ready. Filtering noise via Checksum + Offset.");
}

unsigned long lastTx = 0;
const unsigned long HEARTBEAT_MS = 100;  // floor: retransmit state at least every 100 ms
// Ceiling raised 10 -> 30 ms (2026-07-20): at 10 ms the box's constant display
// refreshes drove ~100 sends/s, starving lwIP TX pbufs (~4 endPacket failures
// per second logged all day) and eventually wedging the send path entirely.
const unsigned long MIN_TX_GAP_MS = 30;

// TX-path dead-man switch: if NO send (telemetry or debug) has succeeded for
// this long, the lwIP send path is wedged (observed: loop alive, AP beaconing,
// zero packets forever) -- restart the chip rather than stay silent all night.
const unsigned long TX_DEAD_RESTART_MS = 15000;

unsigned long lastDebugTx = 0;

void loop() {
  loopCount++;
  Skewered_Parser();

  unsigned long dnow = millis();
  if (dnow - lastDebugTx >= 1000) {
    sendDebug();
    lastDebugTx = dnow;
  }

  // serial self-heal: the box streams constantly, so 10 s of silence means
  // the UART is wedged (or the wire/converter is out) -- re-init and retry
  // every 10 s until bytes flow again; harmless if the line is just unplugged
  if (dnow - lastRxByte >= SERIAL_SILENT_REINIT_MS &&
      dnow - lastReinit >= SERIAL_SILENT_REINIT_MS) {
    initBoxSerial();
    lastReinit = dnow;
    serialReinits++;
  }

  // dead-man switch: sends should succeed many times per second; a long
  // stretch with zero successes means the TX path is wedged -> self-restart
  // (grace period after boot so slow AP startup doesn't trigger it)
  if (dnow > 30000 && lastGoodSend > 0 &&
      dnow - lastGoodSend > TX_DEAD_RESTART_MS) {
    ESP.restart();
  }
  if (lastGoodSend == 0 && dnow > 60000) {
    ESP.restart();   // never managed a single send after a full minute
  }

  // Transmit on every state change (new_data) and at least every
  // HEARTBEAT_MS regardless, but never more often than MIN_TX_GAP_MS.
  // new_data is held (not cleared) while gated, so the latest state
  // always goes out on the next allowed tick - including after a WiFi
  // dropout, since nothing sends while the link is down.
#if USE_SOFTAP
  // Broadcast unconditionally in SoftAP mode. This used to gate on
  // softAPgetStationNum() > 0, but the ESP8266 AP silently expires stations
  // that only listen (the Pi receives broadcasts and never transmits), which
  // froze all telemetry until the station re-announced itself. Ten 67-byte
  // packets per second cost negligible airtime even with no stations attached.
  bool link_up = true;
#else
  bool link_up = WiFi.status() == WL_CONNECTED;
#endif
  if (link_up) {
    unsigned long now = millis();
    if ((new_data || now - lastTx >= HEARTBEAT_MS) && now - lastTx >= MIN_TX_GAP_MS) {
      sendUdpUpdate();
      new_data = false;
      lastTx = now;
    }
  }
}
