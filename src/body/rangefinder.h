#ifndef RANGEFINDER_H
#define RANGEFINDER_H

#include <Wire.h>

// I2C address of the lidar slave (Arduino Pro Micro running lidar_slave.ino)
#define RANGEFINDER_I2C_ADDRESS 0x08

// TFMini / TF-Luna laser rangefinder via I2C slave (lidar_slave board).
//
// Physical wiring:
//   Leonardo pin 2 (SDA) ↔ Pro Micro pin 2 (SDA)
//   Leonardo pin 3 (SCL) ↔ Pro Micro pin 3 (SCL)
//   Common GND
//
// loop() must be called periodically (~100ms). After the first valid
// frame is received, get_range_mm() returns a positive value.
class Rangefinder {
public:
    Rangefinder();

    // Poll the lidar slave once via I2C. Call in the slow subsystem cycle.
    void loop();

    // Last valid distance in mm. Returns 0 if no data received yet.
    int get_range_mm() const { return _range_mm; }

private:
    int _range_mm;
};

#endif // RANGEFINDER_H
