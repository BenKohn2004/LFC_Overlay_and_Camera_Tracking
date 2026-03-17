#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <ESP8266WiFi.h>
#include <espnow.h>
#include <math.h>

bool verbose = false;

// --- CONFIGURATION CONSTANTS ---
const float center_line = 182.0; 
// -------------------------------

// Pin definitions (Updated per your request)
const int pinA = D5;        // Hall Effect Sensor A
const int pinB = D6;        // Hall Effect Sensor B
const int buttonReset = D7; // Reset position
const int buttonSave  = D0; // Save hypotenuse

// Position tracking
volatile int position = 0;
int lastPrintedPosition = 0;
volatile int lastState = 0; 

// Hypotenuse and angle
int hypotenuse = 250;
float angle = 25.0;

// Auto Zero Variables
unsigned long lastPositionChange = 0;
const unsigned long autoZeroDelay = 300000; 
bool enableAutoZero = true;

// OLED Display Setup
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ESPNOW Setup
uint8_t receiverMac[] = { 0xA4, 0xCF, 0x12, 0xF3, 0x04, 0x42 };
typedef struct struct_message {
  uint8_t senderID;
  int position;
  float angle;
} struct_message;
struct_message outgoingData;

// Quadrature lookup table
const int8_t quadratureTable[4][4] = {
  { 0, +1, -1, 0 },
  { -1, 0, 0, +1 },
  { +1, 0, 0, -1 },
  { 0, -1, +1, 0 }
};

// Interrupt function for Hall Effect (Active LOW)
void ICACHE_RAM_ATTR updatePosition() {
  // A3144: LOW = Magnet Detected (1), HIGH = No Magnet (0)
  int valA = (digitalRead(pinA) == LOW) ? 1 : 0;
  int valB = (digitalRead(pinB) == LOW) ? 1 : 0;
  
  int currentState = (valA << 1) | valB;
  int change = quadratureTable[lastState][currentState];
  
  if (change != 0) {
    position += change;
    lastState = currentState;
  }
}

void setup() {
  Serial.begin(115200);

  // Pin setup for Hall Sensors
  pinMode(pinA, INPUT);
  pinMode(pinB, INPUT);
  
  pinMode(buttonReset, INPUT_PULLUP);
  pinMode(buttonSave, INPUT_PULLUP);

  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("OLED failed"));
    for(;;);
  }
  display.display();

  // Initial state capture
  int initA = (digitalRead(pinA) == LOW) ? 1 : 0;
  int initB = (digitalRead(pinB) == LOW) ? 1 : 0;
  lastState = (initA << 1) | initB;

  // Interrupts on both pins for maximum resolution
  attachInterrupt(digitalPinToInterrupt(pinA), updatePosition, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pinB), updatePosition, CHANGE);

  // ESPNOW Initialization
  WiFi.mode(WIFI_STA);
  if (esp_now_init() == 0) {
    esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
    esp_now_add_peer(receiverMac, ESP_NOW_ROLE_SLAVE, 1, NULL, 0);
  }
}

void loop() {
  unsigned long currentMillis = millis();

  // Auto-zero logic
  if (enableAutoZero && position != 0 && (currentMillis - lastPositionChange > autoZeroDelay)) {
    position = 0;
    lastPositionChange = currentMillis;
  }

  // Reset Button logic
  if (digitalRead(buttonReset) == LOW) {
    position = 0;
    delay(200); 
  }

  // Calculate Angle logic
  if (digitalRead(buttonSave) == LOW) {
    hypotenuse = position;
    if (hypotenuse != 0) {
      // Use abs() to ensure angle remains valid regardless of rotation direction
      angle = asin(center_line / abs(hypotenuse)) * (180.0 / M_PI);
    }
    delay(200);
  }

  // Package and Transmit
  outgoingData.senderID = 1; 
  outgoingData.position = position;
  outgoingData.angle = angle;
  esp_now_send(receiverMac, (uint8_t *)&outgoingData, sizeof(outgoingData));

  // OLED Refresh
  if (position != lastPrintedPosition) {
    lastPositionChange = currentMillis;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0,0);
    display.print("Pos: "); display.println(position);
    display.print("Hyp: "); display.println(hypotenuse);
    display.print("Ang: "); display.println(angle);
    display.display();
    lastPrintedPosition = position;
  }
}
