#ifndef MAGNETOMETER_H
#define MAGNETOMETER_H

#include <Wire.h>
#include <TroykaIMU.h>

// Magnetometer wraps the Troyka IMU Compass and caches the last reading.
// Values are in milligauss (raw output from readGaussX/Y multiplied by 1000).
//
// Protocol v2: GX and GY are transmitted in milligauss as floating-point.
// The pilot-side Compass class applies calibration (gain/shift) to compute heading.
class Magnetometer {
public:
    explicit Magnetometer(Compass *_compass);

    // Read new values from the compass hardware. Call once per telemetry cycle.
    void loop();

    // Last cached reading in milligauss.
    float get_gauss_x() { return gauss_x; }
    float get_gauss_y() { return gauss_y; }
    float get_gauss_z() { return gauss_z; }

private:
    Compass *compass;
    float gauss_x;
    float gauss_y;
    float gauss_z;
};

#endif // MAGNETOMETER_H
