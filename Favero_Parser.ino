void Favero_Parser() {
  if (Serial.available() > 0) {
    static uint8_t message[MAX_MESSAGE_LENGTH];
    static uint8_t prev_message[MAX_MESSAGE_LENGTH];
    uint8_t inByte = Serial.read();

    // Look for the STARTING_BYTE (255) to align the 10-byte packet [cite: 3, 59]
    if (inByte == STARTING_BYTE) {
      message_pos = 0;
    }

    if (message_pos < MAX_MESSAGE_LENGTH) {
      message[message_pos++] = inByte; [cite: 61]
    }

    // Once a full 10-byte packet is received 
    if (message_pos == MAX_MESSAGE_LENGTH) {
      
      // OPTIONAL: Basic Checksum Validation
      // The 10th byte (index 9) is typically a sum of the previous 9 bytes.
      uint8_t checksum = 0;
      for (int i = 0; i < 9; i++) checksum += message[i];

      if (checksum == message[9]) { // Verify data integrity
        
        // Only process if the data has actually changed [cite: 63]
        bool changed = false;
        for (int i = 1; i < 9; i++) {
          if (message[i] != prev_message[i]) {
            changed = true;
            break;
          }
        }

        if (changed) {
          new_data = true;

          // Map Light States from Byte 5 [cite: 67-68]
          myData.White_Red_Light   = bitRead(message[5], 0); // Off-target Left
          myData.White_Green_Light = bitRead(message[5], 1); // Off-target Right
          myData.Red_Light         = bitRead(message[5], 2); // Valid Left
          myData.Green_Light       = bitRead(message[5], 3); // Valid Right
          myData.Yellow_Green_Light = bitRead(message[5], 4);
          myData.Yellow_Red_Light   = bitRead(message[5], 5);

          // Map Penalty Cards and Priority [cite: 69-70]
          myData.Red_Card_Green    = bitRead(message[8], 0);
          myData.Red_Card_Red      = bitRead(message[8], 1);
          myData.Yellow_Card_Green = bitRead(message[8], 2);
          myData.Yellow_Card_Red   = bitRead(message[8], 3);
          myData.Priority_Right    = bitRead(message[6], 2);
          myData.Priority_Left     = bitRead(message[6], 3);

          // Use the optimized BCD conversion for Score and Time [cite: 70-71]
          myData.Right_Score       = bcdToInt(message[1]);
          myData.Left_Score        = bcdToInt(message[2]);
          myData.Seconds_Remaining = bcdToInt(message[3]);
          myData.Minutes_Remaining = bcdToInt(message[4]);

          // Save state for next comparison [cite: 71]
          memcpy(prev_message, message, MAX_MESSAGE_LENGTH);
        }
      }
      message_pos = 0; // Reset for next packet [cite: 72]
    }
  }
}
