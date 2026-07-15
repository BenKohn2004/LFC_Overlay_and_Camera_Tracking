#include <ESP8266WiFi.h>
#include <espnow.h>
#include <Servo.h>
#include <math.h>

Servo myServo;

bool verbose = false;
bool invertServo = false;

const int servoPin = D4;

const float maxSpeed = 50.0;
unsigned long lastUpdateTime = 0;

int initial_positionLeft = 0;
int initial_positionRight = 0;

// Must match the sender struct
typedef struct struct_message {
  uint8_t stripID;
  uint8_t senderID;
  int position;
  float angle;
} struct_message;

const uint8_t MY_STRIP_ID = 1;  // Only respond to this strip

struct_message incomingDataLeft;
struct_message incomingDataRight;

const float strip_center = 182.0;
const float strip_center_meters = 7.0;
const float conversionFactor = strip_center / strip_center_meters;
const int engardePosition = round(5.0 * conversionFactor);
const int positionDelta = round(0.25 * conversionFactor);

float adjacentLeft = 0.0;
float adjacentRight = 0.0;
float desired_angle = 0.0;
const int offset = round(0 * conversionFactor);
float current_angle = 90.0;
const float alpha = 0.1;

const int lowerBound = engardePosition - positionDelta;
const int upperBound = engardePosition + positionDelta;

void moveServo() {
  unsigned long currentTime = millis();
  float elapsedTime = (currentTime - lastUpdateTime) / 1000.0;

  if (adjacentLeft == 0 && adjacentRight != 0) {
    adjacentLeft = adjacentRight;
  } else if (adjacentRight == 0 && adjacentLeft != 0) {
    adjacentRight = adjacentLeft;
  }

  if (adjacentLeft == 0 && adjacentRight == 0) {
    if (verbose) Serial.println("Warning: No valid data from either reel.");
    return;
  }

  float adjusted_positionLeft = incomingDataLeft.position + offset;
  float adjusted_positionRight = 364 - incomingDataRight.position + offset;

  float desired_angleLeft = 90 - atan((strip_center - adjusted_positionLeft) / adjacentLeft) * (180.0 / M_PI);
  float desired_angleRight = 90 - atan((strip_center - adjusted_positionRight) / adjacentRight) * (180.0 / M_PI);

  desired_angle = (desired_angleLeft + desired_angleRight) / 2.0;

  if (invertServo) {
    desired_angle = 180 - desired_angle;
  }

  if (elapsedTime > 0) {
    float maxStep = maxSpeed * elapsedTime;
    float angleDelta = desired_angle - current_angle;

    if (abs(angleDelta) > maxStep) {
      current_angle += (angleDelta > 0 ? maxStep : -maxStep);
    } else {
      current_angle += angleDelta * alpha * (elapsedTime / 0.25);
    }

    myServo.write(current_angle);
    lastUpdateTime = currentTime;

    if (verbose) {
      Serial.print("Moving servo to smoothed and speed-limited angle: ");
      Serial.println(current_angle);
    }
  }
}

void onDataRecv(uint8_t *mac, uint8_t *incomingDataBytes, uint8_t len) {
  struct_message incomingData;
  memcpy(&incomingData, incomingDataBytes, sizeof(incomingData));

  if (incomingData.stripID != MY_STRIP_ID) return;

  if (incomingData.senderID == 1) {
    incomingDataLeft = incomingData;

    if (verbose) {
      Serial.println("Data received from Left reel:");
      Serial.print("Position: ");
      Serial.println(incomingDataLeft.position);
      Serial.print("Angle: ");
      Serial.println(incomingDataLeft.angle);
    }

    if (incomingDataLeft.angle != 0) {
      adjacentLeft = round(7.0 * conversionFactor) / tan(incomingDataLeft.angle * M_PI / 180.0);
    } else {
      adjacentLeft = 0.0;
    }
  }

  if (incomingData.senderID == 2) {
    incomingDataRight = incomingData;

    if (verbose) {
      Serial.println("Data received from Right reel:");
      Serial.print("Position: ");
      Serial.println(incomingDataRight.position);
      Serial.print("Angle: ");
      Serial.println(incomingDataRight.angle);
    }

    if (incomingDataRight.angle != 0) {
      adjacentRight = round(7.0 * conversionFactor) / tan(incomingDataRight.angle * M_PI / 180.0);
    } else {
      adjacentRight = 0.0;
    }
  }
}

void setup() {
  Serial.begin(115200);

  incomingDataLeft.position = initial_positionLeft;
  incomingDataRight.position = initial_positionRight;

  myServo.attach(servoPin, 500, 2500);
  myServo.write(90);
  delay(500);

  if (verbose) {
    Serial.println("Servo initialized to 90 degrees!");
  }

  WiFi.mode(WIFI_STA);

  if (esp_now_init() != 0) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(onDataRecv);

  if (verbose) {
    Serial.println("ESP-NOW Receiver Ready!");
  }
}

void loop() {
  moveServo();
  delay(3);
}
