#ifndef VOLTMETER_H
#define VOLTMETER_H

// Voltmeter reads the battery voltage via a resistor divider on pin A3.
//
// Legacy formula:  sensorValue * (5.0 / 1024.0) * 1000  → transmitted as millivolts
// Protocol v2:     sensorValue * (5.0 / 1024.0) * DIVIDER_RATIO  → transmitted as volts
//
// The legacy code multiplied by 1000 and the host divided by 1000 back,
// so the net effect was: voltage_on_pin = sensorValue * (5.0 / 1024.0).
// DIVIDER_RATIO accounts for the hardware voltage divider between the battery
// and the ADC pin. Calibrate this value against a known reference (e.g. multimeter).
// Default: 1.0 (transmits voltage measured at pin, not battery terminal).
class Voltmeter {
public:
    Voltmeter();

    // Read and update the cached voltage. Call once per telemetry cycle.
    void loop();

    // Returns battery voltage in volts (protocol v2 unit).
    float get_voltage() { return voltage; }

private:
    // ADC pin connected to the voltage divider (from legacy).
    static const int VOLT_PIN = A3;

    // Resistor divider scale factor: multiply the ADC-derived voltage by this
    // to get actual battery voltage. Set to 1.0 until hardware is calibrated.
    static constexpr float DIVIDER_RATIO = 5.8f;

    float voltage;
};

#endif // VOLTMETER_H
