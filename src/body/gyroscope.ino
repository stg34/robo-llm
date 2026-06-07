#include "gyroscope.h"

GyroSensor::GyroSensor() : _deg_per_sec_z(0.0f), _bias(0.0f) {
    _gyro.begin();
    _gyro.setRange(RANGE_2000DPS);  // robot can spin fast under load
}

void GyroSensor::calibrate(int n) {
    // L3G4200D requires ~100ms settling after power-on or full-scale change
    // (datasheet: turn-on time from power-down mode, ODR=100Hz).
    // Discard the first 20 samples (~200ms) before averaging to avoid
    // cold-start garbage skewing the bias estimate.
    const int WARMUP = 200;
    for (int i = 0; i < WARMUP; i++) {
        _gyro.readDegPerSecZ();
        delay(10);
    }

    float sum = 0.0f;
    for (int i = 0; i < n; i++) {
        sum += _gyro.readDegPerSecZ();
        delay(10);
    }
    _bias = sum / (float)n;
}

void GyroSensor::loop() {
    _deg_per_sec_z = _gyro.readDegPerSecZ() - _bias;
}
