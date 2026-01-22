#include <ESP8266WiFi.h>
#include <espnow.h>
#include <Servo.h>
#include <math.h>

Servo myServo;

bool verbose = false;
bool invertServo = false;

const int servoPin = D4;

// ---------------- Motion Control ----------------
const float TARGET_ALPHA = 0.15;
const float MAX_SPEED = 90.0;      // deg/sec
const float MAX_ACCEL = 300.0;     // deg/sec^2
const unsigned long SERVO_INTERVAL_MS = 20;

float current_angle = 90.0;
float target_angle = 90.0;
float servo_velocity = 0.0;

unsigned long lastServoUpdate = 0;

// ---------------- Geometry ----------------
const float strip_center = 182.0;
const float strip_center_meters = 7.0;
const float conversionFactor = strip_center / strip_center_meters;
const int offset = 0;

// ---------------- ESPNOW Data ----------------
typedef struct struct_message {
  uint8_t senderID;
  int position;
  float angle;
} struct_message;

struct_message incomingDataLeft;
struct_message incomingDataRight;

float adjacentLeft = 0.0;
float adjacentRight = 0.0;

// ---------------- Servo Motion ----------------
void updateServo() {
  unsigned long now = millis();
  float dt = (now - lastServoUpdate) / 1000.0;
  if (dt <= 0) return;

  // If only one reel valid, mirror it
  if (adjacentLeft == 0 && adjacentRight != 0) adjacentLeft = adjacentRight;
  if (adjacentRight == 0 && adjacentLeft != 0) adjacentRight = adjacentLeft;
  if (adjacentLeft == 0 && adjacentRight == 0) return;

  // Adjust positions
  float posL = incomingDataLeft.position + offset;
  float posR = 364 - incomingDataRight.position + offset;

  // Desired angles
  float angleL = 90 - atan((strip_center - posL) / adjacentLeft) * 180.0 / M_PI;
  float angleR = 90 - atan((strip_center - posR) / adjacentRight) * 180.0 / M_PI;
  float desired_angle = (angleL + angleR) * 0.5;

  if (invertServo) desired_angle = 180 - desired_angle;

  // ---- Target filtering ----
  target_angle += (desired_angle - target_angle) * TARGET_ALPHA;

  // ---- Velocity control ----
  float angle_error = target_angle - current_angle;
  float desired_velocity = constrain(angle_error * 5.0, -MAX_SPEED, MAX_SPEED);

  float max_dv = MAX_ACCEL * dt;
  servo_velocity += constrain(desired_velocity - servo_velocity, -max_dv, max_dv);

  current_angle += servo_velocity * dt;
  current_angle = constrain(current_angle, 0, 180);

  myServo.write(current_angle);
  lastServoUpdate = now;

  if (verbose) {
    Serial.print("Angle: ");
    Serial.print(current_angle);
    Serial.print("  Vel: ");
    Serial.println(servo_velocity);
  }
}

// ---------------- ESPNOW Receive ----------------
void onDataRecv(uint8_t *mac, uint8_t *incomingDataBytes, uint8_t len) {
  struct_message msg;
  memcpy(&msg, incomingDataBytes, sizeof(msg));

  if (msg.senderID == 1) {
    incomingDataLeft = msg;
    adjacentLeft = (msg.angle != 0)
      ? round(7.0 * conversionFactor) / tan(msg.angle * M_PI / 180.0)
      : 0.0;
  }

  if (msg.senderID == 2) {
    incomingDataRight = msg;
    adjacentRight = (msg.angle != 0)
      ? round(7.0 * conversionFactor) / tan(msg.angle * M_PI / 180.0)
      : 0.0;
  }
}

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);

  myServo.attach(servoPin, 500, 2500);
  myServo.write(90);
  delay(500);

  WiFi.mode(WIFI_STA);

  if (esp_now_init() != 0) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(onDataRecv);

  lastServoUpdate = millis();
}

// ---------------- Loop ----------------
void loop() {
  unsigned long now = millis();
  if (now - lastServoUpdate >= SERVO_INTERVAL_MS) {
    updateServo();
  }
}
