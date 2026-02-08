/*
 * Wemos D1 Mini - Dual Hall Effect Magnet Detector
 * ----------------------------------------------
 * This sketch monitors two Hall Effect sensors and triggers LEDs 
 * to help you align your magnets for quadrature encoding.
 * * Connections:
 * Sensor A: Pin D2 (GPIO4) -> Triggers LED A: Pin D7
 * Sensor B: Pin D1 (GPIO5) -> Triggers LED B: Pin D8
 * * Logic:
 * - A3144 Sensors: Output LOW when a magnet is detected.
 * - LEDs: Connected to Ground, so they turn ON when the pin is HIGH.
 */

// Pin Definitions
const int sensorA = D2; 
const int sensorB = D1; 
const int ledA    = D7; 
const int ledB    = D8; 

void setup() {
  // Serial Monitor setup at 115200 baud
  Serial.begin(115200);
  delay(500); // Short pause for Serial to stabilize
  
  Serial.println("");
  Serial.println("======================================");
  Serial.println("  Hall Effect Sensor Alignment Tool   ");
  Serial.println("  Sensor A: D2 | Sensor B: D1         ");
  Serial.println("======================================");

  // Configure Hall Sensors
  // Ensure your 10k ohm pull-up resistors are connected to 3.3V
  pinMode(sensorA, INPUT);
  pinMode(sensorB, INPUT);

  // Configure LED Pins
  pinMode(ledA, OUTPUT);
  pinMode(ledB, OUTPUT);

  // Ensure LEDs start in the OFF state
  digitalWrite(ledA, LOW);
  digitalWrite(ledB, LOW);
}

void loop() {
  // Read the sensors (A3144 is Active LOW)
  bool magnetA = (digitalRead(sensorA) == LOW);
  bool magnetB = (digitalRead(sensorB) == LOW);

  // Turn LEDs ON if a magnet is detected
  digitalWrite(ledA, magnetA ? HIGH : LOW);
  digitalWrite(ledB, magnetB ? HIGH : LOW);

  // Print detection events to the Serial Monitor
  if (magnetA || magnetB) {
    Serial.print("Detection Status: ");
    Serial.print(" [A: ");
    Serial.print(magnetA ? "ON ]" : "OFF]");
    Serial.print(" [B: ");
    Serial.print(magnetB ? "ON ]" : "OFF]");
    Serial.println("");
  }

  // Small delay to keep the Serial Monitor readable
  delay(20);
}