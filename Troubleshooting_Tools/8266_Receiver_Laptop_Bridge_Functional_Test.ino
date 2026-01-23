/*
  Favero Serial Test Generator
  ----------------------------
  Simulates Favero scoring data over Serial
  for testing Python Bridge + OBS without ESPNOW.
*/

unsigned long lastUpdate = 0;
const unsigned long UPDATE_INTERVAL = 1000; // ms

// Simulated state
bool redLight = false;
bool greenLight = false;
bool whiteRed = false;
bool whiteGreen = false;

int leftScore = 0;
int rightScore = 0;

int minutesRemaining = 3;
int secondsRemaining = 0;

bool yellowRed = false;
bool yellowGreen = false;
bool redCardRed = false;
bool redCardGreen = false;

bool priorityLeft = true;
bool priorityRight = false;

int step = 0;

void sendData() {
  Serial.print("DATA,");
  Serial.print(redLight);        Serial.print(",");
  Serial.print(greenLight);      Serial.print(",");
  Serial.print(whiteRed);        Serial.print(",");
  Serial.print(whiteGreen);      Serial.print(",");

  Serial.print(leftScore);       Serial.print(",");
  Serial.print(rightScore);      Serial.print(",");
  Serial.print(minutesRemaining);Serial.print(",");
  Serial.print(secondsRemaining);Serial.print(",");

  Serial.print(yellowRed);       Serial.print(",");
  Serial.print(yellowGreen);     Serial.print(",");
  Serial.print(redCardRed);      Serial.print(",");
  Serial.print(redCardGreen);    Serial.print(",");

  Serial.print(priorityLeft);    Serial.print(",");
  Serial.println(priorityRight);
}

void advanceState() {
  // Reset lights
  redLight = greenLight = whiteRed = whiteGreen = false;

  switch (step) {
    case 0:
      greenLight = true;
      leftScore++;
      break;

    case 1:
      redLight = true;
      rightScore++;
      break;

    case 2:
      whiteGreen = true;
      break;

    case 3:
      whiteRed = true;
      break;

    case 4:
      yellowGreen = !yellowGreen;
      break;

    case 5:
      yellowRed = !yellowRed;
      break;

    case 6:
      priorityLeft = !priorityLeft;
      priorityRight = !priorityRight;
      break;
  }

  step = (step + 1) % 7;
}

void updateClock() {
  if (minutesRemaining == 0 && secondsRemaining == 0) {
    minutesRemaining = 3;
    secondsRemaining = 0;
    return;
  }

  if (secondsRemaining == 0) {
    minutesRemaining--;
    secondsRemaining = 59;
  } else {
    secondsRemaining--;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("Favero Serial Test Generator Started");
}

void loop() {
  if (millis() - lastUpdate >= UPDATE_INTERVAL) {
    lastUpdate = millis();

    advanceState();
    updateClock();
    sendData();
  }
}
