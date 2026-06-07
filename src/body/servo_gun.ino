#include "servo_gun.h"

ServoGun::ServoGun() {
    state        = IDLE;
    phase_end_ms = 0;
}

void ServoGun::attach() {
    servo.attach(SERVO_PIN);
    servo.write(RELEASE_POS);
}

void ServoGun::trigger() {
    if (state != IDLE) return;
    servo.write(FIRE_POS);
    phase_end_ms = millis() + FIRE_HOLD_MS;
    state        = FIRING;
}

void ServoGun::set_pos(int degrees) {
    if (state != IDLE) return;
    servo.write(constrain(degrees, 0, 180));
}

bool ServoGun::loop() {
    if (state == IDLE) return false;

    unsigned long now = millis();
    if (now < phase_end_ms) return false;

    if (state == FIRING) {
        servo.write(RELEASE_POS);
        phase_end_ms = now + RELEASE_SETTLE_MS;
        state        = RELEASING;
        return false;
    }

    // state == RELEASING, settle time elapsed
    state = IDLE;
    return true;
}
