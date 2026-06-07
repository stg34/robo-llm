#include "magnetometer.h"

Magnetometer::Magnetometer(Compass *_compass) {
    compass = _compass;
    compass->begin();
    compass->setRange(RANGE_4_GAUSS);

    gauss_x = 0.0f;
    gauss_y = 0.0f;
    gauss_z = 0.0f;
}

void Magnetometer::loop() {
    // readGaussX/Y/Z returns Tesla-derived floats; multiply by 1000 for milligauss.
    gauss_x = compass->readGaussX() * 1000.0f;
    gauss_y = compass->readGaussY() * 1000.0f;
    gauss_z = compass->readGaussZ() * 1000.0f;
}
