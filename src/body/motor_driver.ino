#include "motor_driver.h"

// ---------------------------------------------------------------------------
// MotorState
// ---------------------------------------------------------------------------

MotorState::MotorState() {
    speed = 0;
    changed_at = millis();
}

void MotorState::set_speed(int _speed) {
    speed = constrain(_speed, -128, 127);
    changed_at = millis();
}

int MotorState::get_speed() {
    return speed;
}

unsigned long MotorState::get_changed_at() {
    return changed_at;
}

// ---------------------------------------------------------------------------
// MotorDriver
// ---------------------------------------------------------------------------

MotorDriver::MotorDriver(int _dir_pin, int _speed_pin, int _current_pin, char _name) {
    dir_pin      = _dir_pin;
    speed_pin    = _speed_pin;
    current_pin  = _current_pin;
    name         = _name;
    target_speed = 0;

    pinMode(dir_pin, OUTPUT);
    pinMode(speed_pin, OUTPUT);

    state = new MotorState();
    drive_control();
}

void MotorDriver::loop() {
    // Soft-start: ramp rate = 4 * MAX_SPEED units per 1000ms.
    // dt is clamped to 50ms so a single long delay does not cause a jump.
    const float ramp_rate = 4.0f * MAX_SPEED / 1000.0f;  // units/ms
    long dt = (long)(millis() - state->get_changed_at());
    if (dt > 50) dt = 50;

    int current = state->get_speed();
    int delta   = target_speed - current;

    if (abs(delta) < 10) {
        // Close enough — snap to target immediately.
        state->set_speed(target_speed);
    } else {
        int dir = (delta > 0) ? 1 : -1;
        state->set_speed(current + (int)(ramp_rate * dt * dir));
    }

    drive_control();
}

void MotorDriver::set_speed(int speed) {
    target_speed = speed;
}

void MotorDriver::set_speed_direct(int speed) {
    speed = constrain(speed, -128, 127);
    target_speed = speed;
    state->set_speed(speed);  // snap current == target, bypasses ramp
    drive_control();
}

void MotorDriver::drive_control() {
    int s = state->get_speed();
    digitalWrite(dir_pin, s < 0 ? HIGH : LOW);
    analogWrite(speed_pin, constrain(abs(s), 0, MAX_SPEED));
}

int MotorDriver::get_speed() {
    return state->get_speed();
}

int MotorDriver::get_current() {
    return analogRead(current_pin);
}
