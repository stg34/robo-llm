#include "voltmeter.h"

Voltmeter::Voltmeter() {
    pinMode(VOLT_PIN, INPUT);
    voltage = 0.0f;
}

void Voltmeter::loop() {
    int raw = analogRead(VOLT_PIN);
    // Convert ADC reading to volts on the pin, then scale by divider ratio.
    voltage = raw * (5.0f / 1024.0f) * DIVIDER_RATIO;
}
