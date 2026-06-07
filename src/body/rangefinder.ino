#include "rangefinder.h"

Rangefinder::Rangefinder() : _range_mm(0) {
    Wire.begin(); // мастер — без адреса
    // Wire is already initialised in body.ino (for the magnetometer).
    // Nothing extra to do here.
}

void Rangefinder::loop() {
  Wire.requestFrom(RANGEFINDER_I2C_ADDRESS, 2);

  if (Wire.available() == 2) {
    int high = Wire.read();
    int low  = Wire.read();
    _range_mm = ((high << 8) | low) * 10;

    // Serial.print("Body Dist: ");
    // Serial.print(_range_mm);
    // Serial.println(" mm");
  } else {
    Serial.println("I2C: no response");
  }

  // delay(100);
}
