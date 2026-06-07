#ifndef SERVO_GUN_H
#define SERVO_GUN_H

// ServoGun — non-blocking servo control for the pneumatic gun trigger.
//
// Hardware: servo on SERVO_PIN (9), active-low trigger pulled to FIRE_POS.
// Cycle: IDLE → FIRING (hold FIRE_POS for FIRE_HOLD_MS)
//              → RELEASING (return to RELEASE_POS, wait RELEASE_SETTLE_MS)
//              → IDLE
//
// Timer note: Servo.h uses Timer1 on most AVR boards. On Leonardo/Micro
// Timer1 is shared with PWM on pins 9/10 only — motor PWM is on pins 5/6
// (Timer3/Timer4), so there is no conflict.

#include <Servo.h>
#include "protocol.h"

class ServoGun {
public:
    // Servo positions (degrees)
    static const int RELEASE_POS        = 45;
    static const int FIRE_POS           = 150;

    // Timing
    static const unsigned long FIRE_HOLD_MS       = 600UL;
    static const unsigned long RELEASE_SETTLE_MS  = 600UL;

    ServoGun();

    // Call once in setup(): attach servo to SERVO_PIN and move to release position.
    void attach();

    // Begin a fire cycle. Ignored if a cycle is already in progress.
    void trigger();

    // Call every main loop iteration.
    // Returns true exactly once when a full fire cycle completes (IDLE reached).
    bool loop();

    // Move to an arbitrary position (only when idle — calibration use).
    void set_pos(int degrees);

    // True when no cycle is in progress.
    bool is_idle() { return state == IDLE; }

private:
    enum State { IDLE, FIRING, RELEASING };

    Servo         servo;
    State         state;
    unsigned long phase_end_ms;
};

#endif // SERVO_GUN_H
