#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <ESP8266WiFi.h>
#include <espnow.h>
#include <math.h>  // Include for trigonometric functions

bool verbose = false;  // Enable serial output

// Pin definitions
const int pinA = D5;
const int pinB = D6;
const int buttonPin1 = D7;  // Reset position
const int buttonPin2 = D0;  // Save position to hypotenuse

// Position tracking
volatile int position = 0;
int lastPrintedPosition = 0;
int lastState = 0;  // Stores last encoder state

// Hypotenuse and angle
int hypotenuse = 0;
float angle = 0.0;

// Auto Zero Variables
unsigned long lastPositionChange = 0;
const unsigned long autoZeroDelay = 300000;  // 5 minutes in milliseconds
bool enableAutoZero = true;  // Set to false to disable auto-zeroing

// OLED Display Setup
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Button debounce
unsigned long lastButtonPress1 = 0;
unsigned long lastButtonPress2 = 0;
const unsigned long debounceDelay = 200;

// ESPNOW Setup
uint8_t receiverMac[] = { 0xA4, 0xCF, 0x12, 0xF3, 0x04, 0x42 };

// Data structure for ESPNOW
typedef struct struct_message {
  uint8_t senderID;  // Unique ID for the sender
  int position;
  float angle;
} struct_message;

struct_message outgoingData;

// Quadrature lookup table
const int8_t quadratureTable[4][4] = {
  { 0, +1, -1, 0 },  // 00 -> (00, 01, 10, 11)
  { -1, 0, 0, +1 },  // 01 -> (00, 01, 10, 11)
  { +1, 0, 0, -1 },  // 10 -> (00, 01, 10, 11)
  { 0, -1, +1, 0 }   // 11 -> (00, 01, 10, 11)
};

// Interrupt function for encoder
void ICACHE_RAM_ATTR updatePosition() {
  int currentState = (digitalRead(pinA) << 1) | digitalRead(pinB);
  int change = quadratureTable[lastState][currentState];
  position += change;
  lastState = currentState;  // Update state
}

// ESPNOW send callback
void onDataSent(uint8_t *macAddr, uint8_t sendStatus) {
  if (verbose) {
    Serial.print("Send status: ");
    Serial.println(sendStatus == 0 ? "Success" : "Fail");
  }
}

void autoZeroIfNeeded(unsigned long currentMillis) {
  if (!enableAutoZero) return;  // Skip if disabled

  if (position != 0 && currentMillis - lastPositionChange > autoZeroDelay) {
    position = 0;
    Serial.println("Auto-zeroed after 5 minutes of inactivity.");
    lastPositionChange = currentMillis;  // Prevent repeated zeroing
  }
}

void setup() {
  Serial.begin(115200);

  // Pin setup
  pinMode(pinA, INPUT);
  pinMode(pinB, INPUT);
  pinMode(buttonPin1, INPUT_PULLUP);
  pinMode(buttonPin2, INPUT_PULLUP);

  // OLED Display setup
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    while (true)
      ;
  }
  display.display();
  delay(2000);

  // Initial encoder state
  lastState = (digitalRead(pinA) << 1) | digitalRead(pinB);

  // Attach interrupts
  attachInterrupt(digitalPinToInterrupt(pinA), updatePosition, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pinB), updatePosition, CHANGE);

  // ESPNOW Setup
  WiFi.mode(WIFI_STA);
  if (esp_now_init() != 0) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER);
  esp_now_add_peer(receiverMac, ESP_NOW_ROLE_SLAVE, 1, NULL, 0);
  esp_now_register_send_cb(onDataSent);

  Serial.println("Optical Encoder with ESPNOW Ready");
}

void loop() {
  unsigned long currentMillis = millis();

  // Check for auto-zero condition
  autoZeroIfNeeded(currentMillis);

  // Button 1: Reset position
  if (digitalRead(buttonPin1) == LOW && currentMillis - lastButtonPress1 > debounceDelay) {
    position = 0;
    lastButtonPress1 = currentMillis;
    Serial.println("Position reset to 0");
  }

  // Button 2: Save position as hypotenuse and calculate angle
  if (digitalRead(buttonPin2) == LOW && currentMillis - lastButtonPress2 > debounceDelay) {
    hypotenuse = position;
    lastButtonPress2 = currentMillis;

    if (hypotenuse != 0) {
      angle = asin(182.0 / hypotenuse) * (180.0 / M_PI);
      Serial.print("Hypotenuse: ");
      Serial.println(hypotenuse);
      Serial.print("Angle: ");
      Serial.println(angle);
    } else {
      Serial.println("Error: Hypotenuse is zero. Cannot calculate angle.");
    }
  }

  // Send data via ESPNOW
  outgoingData.senderID = 1;  // ID of 1 is the Left Reel, ID of 2 is the Right Reel
  outgoingData.position = position;
  outgoingData.angle = angle;
  esp_now_send(receiverMac, (uint8_t *)&outgoingData, sizeof(outgoingData));

  // Update OLED only if position changes
  if (position != lastPrintedPosition) {
    lastPositionChange = currentMillis;  // Track last change time
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.print("Position: ");
    display.println(position);
    display.print("Hypotenuse: ");
    display.println(hypotenuse);
    display.print("Angle: ");
    display.println(hypotenuse != 0 ? angle : 0);
    display.display();
    lastPrintedPosition = position;
  }

  if (verbose) {
    delay(50);  // Small delay for readability
  }
}
