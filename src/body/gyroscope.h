#ifndef GYROSCOPE_H
#define GYROSCOPE_H

#include <Wire.h>
#include "l3g4200d.h"

// Gyroscope wraps the Troyka IMU L3G4200D and caches the last reading.
//
// Protocol v2: GZ is transmitted in degrees/second (float).
// Positive = counter-clockwise (right-hand rule, Z-axis up).
// The pilot-side Navigator negates the sign if needed.
class GyroSensor {
public:
    GyroSensor();

    // Measure gyro bias while robot is stationary. Call once after setup().
    // Averages n readings (~10ms apart); subtracts bias on every subsequent loop().
    void calibrate(int n = 30);

    // Read new value from gyro hardware. Call every main loop iteration (50 Hz).
    void loop();

    // Last cached Z-axis angular rate in degrees/second (bias-corrected).
    float get_deg_per_sec_z() { return _deg_per_sec_z; }

    // Adjust bias by delta_dps. Called when Python sends a warm-sensor calibration
    // correction to fix the cold-start thermal over-subtraction.
    void adjust_bias(float delta_dps) { _bias += delta_dps; }

private:
    L3G4200D_TWI _gyro;
    float        _deg_per_sec_z;
    float        _bias;
};

#endif // GYROSCOPE_H
