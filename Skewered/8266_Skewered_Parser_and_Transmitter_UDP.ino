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

// Channel 11, not the default 1. ESP-NOW on an ESP8266 defaults to channel 1,
// so a room with other Wemos boards running stock firmware puts all of them
// directly on top of this AP. 1 and 11 do not overlap at all, so this
// separates the scoring link from them without touching a single other board.
//
// It matters more here than for most traffic: telemetry goes to the subnet
// broadcast address, and 802.11 broadcast frames get no acknowledgement and no
// retransmission -- a collided packet is simply lost, silently, as a dropped
// state update. Contention hurts this link before it hurts anything else.
//
// Safe for the Pi: its connection profile pins the BSSID (which does not
// change with channel) and leaves channel/band unset, so it re-associates
// wherever the AP moves.
#define AP_CHANNEL 11

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
// 71 bytes total (was 67 before the hit-age fields were appended).
// Python (Raspberry Pi) unpack:
//   import struct
//   fmt = "<B6s4I12?32sHH"  # msgType, mac, R/L score, sec, min, 12 flags,
//                           # name, left hit age, right hit age
//   fields = struct.unpack(fmt, data)
// Appended at the END so the older 67-byte layout stays a prefix of this one:
// receivers can accept both lengths and there is no upgrade ordering to get
// wrong.
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
  // Milliseconds since the hit on each side, brought up to date at transmit
  // time (see sendUdpUpdate). 0 = no hit on that side.
  uint16_t Left_Hit_Age;
  uint16_t Right_Hit_Age;
};

// The Pi unpacks by exact byte count, so a struct change that slips through
// unnoticed would silently drop every packet. Make it a compile error instead.
static_assert(sizeof(FaveroMessage) == 71,
              "payload size changed - update FMT on the Pi to match");

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

// The pins behind the D-labels. D7 = GPIO13 carries the converter's TTL RX and
// becomes UART0 RX after the swap. D6 = GPIO12 has been unused since the
// SoftwareSerial version, but the reset below parks it high-Z anyway so that
// nothing on this board can drive the converter's DI line.
const uint8_t PIN_D7 = 13;
const uint8_t PIN_D6 = 12;

void initBoxSerial() {
  Serial.end();
  // pull-up BEFORE begin/swap: the RS-485 converter's output needs RX idled
  // high, and setting it after the swap re-muxes the pin off the UART
  pinMode(PIN_D7, INPUT_PULLUP);
  Serial.begin(115200);
  Serial.swap();   // UART0 RX -> GPIO13 = D7 (where the converter is wired)
}

// Deep link reset: what pulling the D7 wire off does, without the wire.
//
// initBoxSerial() on its own has had a long trial and does not clear the
// cold-boot wedge -- 332 self-heal re-inits produced 3 recoveries (2026-08-02).
// The reason is that it never lets GPIO13 go genuinely high-impedance: it tears
// the UART down and hands the pin straight from one driven state (UART input
// with pull-up) to another. Here the pin sits at INPUT with no pull-up for
// PIN_FLOAT_MS first, which is as close to unplugged as software gets.
//
// It cannot go further from this end. The converter is powered from 5 V and
// drives GPIO13 from the outside, so driving the pin low or high to "cycle" it
// would be output-against-output contention through an input that is not 5 V
// tolerant -- the exact stress the latch-up theory blames for the wedge.
// Actually removing the external drive needs hardware: converter VCC on 3V3, a
// divider on RXD, or a MOSFET gating the converter's ground.
const unsigned long PIN_FLOAT_MS = 50;

void resetBoxLink() {
  Serial.end();
  pinMode(PIN_D7, INPUT);   // high-Z -- nothing on this board holds the line
  pinMode(PIN_D6, INPUT);
  delay(PIN_FLOAT_MS);
  initBoxSerial();          // pull-up back on, UART0 back on GPIO13
  // Drop whatever the transition shook loose: a break condition, a half frame,
  // or the spurious byte a re-init has always produced (the one that makes rx
  // creep in lockstep with sr and has misread as line activity before).
  //
  // Time-bounded for the same reason Skewered_Parser's drain is: a floating or
  // weakly-biased line chatters continuously, and an unbounded while() on it
  // never returns. This one runs inside setup() too, where a hang would mean
  // the AP never starts at all.
  unsigned long drainStart = millis();
  while (BoxSerial.available() && millis() - drainStart < 8) BoxSerial.read();
}

// Early-boot link resets. The failure only ever appears at cold boot with the
// box already powered, so cycle the link a few times across the first seconds
// instead of waiting out the 10 s silence self-heal. The times straddle the
// 3.3 V rail settling and the AP coming up.
//
// Each one is skipped if valid frames are already arriving: a healthy boot is
// never interrupted, and the reset still fires in every case that looks like
// the fault.
const unsigned long BOOT_RESET_AT_MS[] = {2000, 6000, 12000};
const uint8_t BOOT_RESET_COUNT =
    sizeof(BOOT_RESET_AT_MS) / sizeof(BOOT_RESET_AT_MS[0]);
uint8_t bootResetIdx = 0;
uint8_t bootResets = 0;   // how many actually fired (beacon: br=)

// ---------------- Pin traffic probe (DIAGNOSTIC) ----------------
// Answers one question the line probe cannot: WHICH pin is the box's data on?
//
// The converter modules label their TTL pins inconsistently -- "RXD"/"TXD" are
// written from the module's point of view on some boards and the MCU's on
// others -- so "RXD goes to D7" does not settle whether D7 carries data INTO
// the ESP or a signal out of it. This counts transitions on both candidate
// pins and reports each in the beacon, which settles it without a scope.
//
// Reading both pins from ONE register fetch keeps the comparison fair: neither
// pin is sampled at a different moment than the other. The loop turns over
// several million times a second against an 8.7 us bit time at 115200, so it
// cannot miss a bit.
//
// Pull-ups stay ON while sampling, deliberately. A floating pin picks up noise
// and would count edges that mean nothing; held up by the internal pull-up a
// disconnected pin reads a steady high and counts ZERO, so a negative result
// is real evidence rather than an absence of evidence. Anything actually
// driving the line walks over a ~45k pull-up without noticing it.
//
// pinMode() on GPIO13 re-muxes the pin off UART0 (the same trap initBoxSerial
// warns about), so this finishes by restoring the UART.
const unsigned long EDGE_WINDOW_US = 20000;   // ~4 box frames at ~5 ms each
const unsigned long EDGE_PERIOD_MS = 5000;

// 0xFFFF = "no window has run yet". Without a sentinel, a probe that never
// executes reports the same zeros as a probe that ran and saw a dead pin --
// an absence of evidence wearing the costume of evidence.
const uint16_t EDGES_UNSAMPLED = 0xFFFF;
uint16_t g_edges_d6 = EDGES_UNSAMPLED;
uint16_t g_edges_d7 = EDGES_UNSAMPLED;
unsigned long lastEdgeSample = 0;

void sampleEdges() {
  pinMode(PIN_D6, INPUT_PULLUP);
  pinMode(PIN_D7, INPUT_PULLUP);   // also re-muxes GPIO13 off UART0
  delayMicroseconds(200);          // let the nodes settle

  uint32_t g = GPI;
  bool p6 = g & (1u << PIN_D6);
  bool p7 = g & (1u << PIN_D7);
  uint16_t e6 = 0, e7 = 0;

  unsigned long t0 = micros();
  while (micros() - t0 < EDGE_WINDOW_US) {
    g = GPI;                       // both pins, one fetch, same instant
    bool c6 = g & (1u << PIN_D6);
    bool c7 = g & (1u << PIN_D7);
    if (c6 != p6) { e6++; p6 = c6; }
    if (c7 != p7) { e7++; p7 = c7; }
  }

  g_edges_d6 = e6;
  g_edges_d7 = e7;
  initBoxSerial();                 // pull-up + UART0 back on GPIO13
}

// ---------------- RS-485 line probe ----------------
// Tells apart "the scoring box is switched off" from "the link is broken".
// In the telemetry those are indistinguishable: valid frames simply stop.
// GPIO13 idles high either way, because initBoxSerial() holds it there with
// the internal pull-up -- a disconnected wire and a converter driving an idle
// mark level read exactly the same.
//
// So drop the pull-up and see what the line does unaided:
//   stable HIGH -> something is actively driving the mark level: the converter
//                  is powered and working, the box is merely idle/off
//   stable LOW  -> line held low: break condition, or the pair is swapped
//   unstable    -> high impedance, nothing driving it at all: a broken
//                  connection, or the receiver is tri-stated (RE not held low)
//
// Caveat: the ESP8266 has no internal pull-down on GPIO13 (only GPIO16), so
// this is a floating-vs-driven heuristic, not a clean three-state read. Stray
// capacitance can hold a disconnected line at its last level; sampling across
// ~1.6 ms makes that unlikely but not impossible.
//
// pinMode() on GPIO13 re-muxes the pin off the UART -- the same trap
// initBoxSerial() warns about -- so the probe finishes by calling it.
//
// Idleness is measured in VALID FRAMES, not raw bytes. A line with absent or
// weak failsafe biasing trickles noise (observed at ~10 bytes/s with zero
// valid frames), and gating on raw bytes would reset the timer every second,
// so the probe would never fire in exactly the case worth diagnosing. Note
// this differs from the self-heal above, which deliberately gates on raw
// bytes because any byte proves the UART itself is alive.

const unsigned long PROBE_IDLE_MS = 5000;     // no valid frame for this long
const unsigned long PROBE_PERIOD_MS = 10000;  // and re-probe no faster than this

enum : uint8_t {
  PROBE_NONE = 0,    // not run -- the link is delivering, nothing to diagnose
  PROBE_FLOAT = 1,   // high-Z: broken connection or tri-stated receiver
  PROBE_HIGH = 2,    // driven mark: converter alive, box idle
  PROBE_LOW = 3,     // held low: break condition or mis-wired pair
};

uint8_t g_line_probe = PROBE_NONE;
uint32_t lastValidCount = 0;
unsigned long lastValidChange = 0;
unsigned long lastProbe = 0;

void probeLine() {
  const int SAMPLES = 32;
  pinMode(PIN_D7, INPUT);      // drop the pull-up (also re-muxes off the UART)
  delayMicroseconds(200);      // let the node settle
  int high = 0;
  for (int i = 0; i < SAMPLES; i++) {
    if (digitalRead(PIN_D7)) high++;
    delayMicroseconds(50);
  }
  initBoxSerial();             // restore pull-up + UART0 on GPIO13

  if (high == SAMPLES) g_line_probe = PROBE_HIGH;
  else if (high == 0) g_line_probe = PROBE_LOW;
  else g_line_probe = PROBE_FLOAT;
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

// ---------------- Hit age (protocol bytes 7-9 = frame indices 8-10) --------
// Two 10-bit timing values, 0-999 ms, 0 when that side has no hit:
//   byte7 | 0 LLLLLLL |    high 7 bits of LEFT
//   byte8 | LLL 00 RRR |   low 3 bits of LEFT, then high 3 bits of RIGHT
//   byte9 | RRRRRRR 0 |    low 7 bits of RIGHT
// For a valid hit this is elapsed milliseconds since the hit; for
// short/whipover it is the duration; for a late hit it is time since lockout.
//
// This is what makes overlay/video alignment tractable. The box streams state
// every ~5 ms while this transmitter deliberately throttles to 10-33 Hz, so a
// light's arrival time at the Pi says little about when it actually happened.
// The age travels with the packet and is true regardless of that throttling
// or of Wi-Fi jitter.
uint16_t g_hit_age_l = 0;
uint16_t g_hit_age_r = 0;
unsigned long g_hit_age_at = 0;   // millis() when the two values above were read

void decodeHitAge(byte b7, byte b8, byte b9) {
  g_hit_age_l = (uint16_t)(((b7 & 0x7F) << 3) | ((b8 >> 5) & 0x07));
  g_hit_age_r = (uint16_t)(((b8 & 0x07) << 7) | ((b9 >> 1) & 0x7F));
  g_hit_age_at = millis();
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

        // Track the hit age on EVERY valid packet, not only changed ones. The
        // change detector below deliberately skips indices 8-10 (they tick at
        // 200 Hz and would make every packet look different), but the age has
        // to be current or it is worthless -- decoding it only on change would
        // freeze it at the value it happened to have when the lights latched.
        decodeHitAge(currentPacket[8], currentPacket[9], currentPacket[10]);

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
  // Age the hit forward by however long we sat on it. The box's number was
  // true when IT sent the packet; between then and now this transmitter may
  // have waited up to MIN_TX_GAP_MS, or a whole heartbeat. Adding that wait
  // back is the entire point -- it cancels the throttling delay instead of
  // baking it into the value, leaving only Wi-Fi flight time as error.
  unsigned long held = millis() - g_hit_age_at;
  myData.Left_Hit_Age = g_hit_age_l ? (uint16_t)(g_hit_age_l + held) : 0;
  myData.Right_Hit_Age = g_hit_age_r ? (uint16_t)(g_hit_age_r + held) : 0;

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
// Python unpack: struct.unpack("<B6IBBBBB", data)  -> 30 bytes
//   magic 0xDD, uptime_ms, loop_count, rx_bytes, valid_packets,
//   send_fails, free_heap, stations, rst_reason, serial_reinits (capped 255),
//   line_probe, boot_resets
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
  uint8_t line_probe;      // see "RS-485 line probe" below
  uint8_t boot_resets;     // early-boot link resets that actually fired
  uint16_t edges_d6;       // transitions counted on GPIO12 in the last window
  uint16_t edges_d7;       // transitions counted on GPIO13 in the last window
};

// The logger parses by exact byte count, so a silent size change would drop
// every beacon. Make it a compile error instead (same reasoning as the payload).
static_assert(sizeof(DebugMessage) == 34,
              "beacon size changed - add a format branch in debug_logger.py");

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
  d.line_probe = g_line_probe;
  d.boot_resets = bootResets;
  d.edges_d6 = g_edges_d6;
  d.edges_d7 = g_edges_d7;
  Udp.beginPacket(udpTarget(), DEBUG_PORT);
  Udp.write((uint8_t *)&d, sizeof(d));
  Udp.endPacket();
}

// ---------------- Standard Arduino ----------------

void setup() {
  g_rst_reason = ESP.getResetInfoPtr()->reason;   // why the last boot ended
  // Float D6/D7 briefly before the UART claims D7, then start it. More resets
  // follow at BOOT_RESET_AT_MS if no frames arrive.
  resetBoxLink();
  // (BoxSerial is a reference to Serial, so it is already started + swapped)

#if USE_SOFTAP
  WiFi.mode(WIFI_AP);
  WiFi.softAP(WIFI_SSID, WIFI_PASSWORD, AP_CHANNEL);
  WiFi.macAddress(myData.macAddr);
  Serial.print("SoftAP started: ");
  Serial.print(WIFI_SSID);
  Serial.print("  ch ");
  Serial.print(AP_CHANNEL);
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

  // Early-boot link resets (see BOOT_RESET_AT_MS). Each slot is consumed
  // whether or not it fires, so a link that comes up healthy and then dies is
  // left to the 10 s self-heal rather than being cycled again out of schedule.
  if (bootResetIdx < BOOT_RESET_COUNT && dnow >= BOOT_RESET_AT_MS[bootResetIdx]) {
    if (validPackets == 0) {
      resetBoxLink();
      bootResets++;
      lastRxByte = millis();   // don't let the reset itself age into a self-heal
      dnow = millis();         // resetBoxLink blocked ~50 ms; a stale dnow here
                               // makes dnow - lastRxByte underflow and fire the
                               // self-heal on the very next test
    }
    bootResetIdx++;
  }

  // Pin traffic probe: only while nothing is being decoded. It steals the UART
  // for 20 ms, so it must never run once real frames are arriving -- and once
  // they are, the question it answers is already settled.
  if (validPackets == 0 && dnow - lastEdgeSample >= EDGE_PERIOD_MS) {
    sampleEdges();
    lastRxByte = millis();   // the steal is not evidence of a silent line
    dnow = millis();         // sampleEdges blocked 20 ms -- refresh before any
                             // later `dnow - <timestamp>` test underflows
    lastEdgeSample = dnow;
  }

  // Probe the RS-485 line once valid frames have stopped. While the link is
  // delivering there is nothing to diagnose, and the probe would corrupt the
  // reception it interrupts.
  if (validPackets != lastValidCount) {
    lastValidCount = validPackets;
    lastValidChange = dnow;
    g_line_probe = PROBE_NONE;
  }
  if (dnow - lastValidChange > PROBE_IDLE_MS && dnow - lastProbe > PROBE_PERIOD_MS) {
    probeLine();
    lastProbe = dnow;
  }

  if (dnow - lastDebugTx >= 1000) {
    sendDebug();
    lastDebugTx = dnow;
  }

  // serial self-heal: the box streams constantly, so 10 s of silence means
  // the UART is wedged (or the wire/converter is out) -- reset and retry
  // every 10 s until bytes flow again; harmless if the line is just unplugged.
  // Uses the deep reset now: the plain re-init this used to call recovered 3
  // times in 332 attempts, and the float gap costs 50 ms on an already-dead
  // link.
  if (dnow - lastRxByte >= SERIAL_SILENT_REINIT_MS &&
      dnow - lastReinit >= SERIAL_SILENT_REINIT_MS) {
    resetBoxLink();
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
