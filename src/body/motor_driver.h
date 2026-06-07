#ifndef MOTOR_DRIVER_H
#define MOTOR_DRIVER_H

// Maximum PWM speed value written to analogWrite().
// Soft-start ramps toward this value over ~250ms.
#define MAX_SPEED 100

// MotorState tracks the currently applied (actual) speed
// and the timestamp of the last change, used for soft-start.
class MotorState {
public:
    MotorState();
    void set_speed(int speed);
    int get_speed();
    unsigned long get_changed_at();

private:
    int speed;
    unsigned long changed_at;
};

// MotorDriver controls a single motor via DIR + PWM pins,
// reads current via an ADC pin, and applies soft-start ramping.
//
// Speed convention (internal):
//   -128..127  negative = reverse, positive = forward, 0 = stop
//
// Pin assignments (Arduino Uno / Nano compatible):
//   Motor 1 (left):  DIR=4, SPD=5 (PWM), CURR=A1
//   Motor 2 (right): DIR=7, SPD=6 (PWM), CURR=A0
class MotorDriver {
public:
    MotorDriver(int _dir_pin, int _speed_pin, int _current_pin, char _name);

    // Called every main loop iteration to advance soft-start ramp.
    void loop();

    // Set target speed (-128..127). Actual speed ramps toward target.
    void set_speed(int speed);

    // Set speed instantly, bypassing the soft-start ramp.
    // Used by MotionController so the PD output is applied without delay.
    void set_speed_direct(int speed);

    // Returns the currently applied (ramped) speed.
    int get_speed();

    // Returns raw ADC reading (0-1023) from the current sense pin.
    int get_current();

    // Pin constants
    static const int SPD1  = 5;
    static const int DIR1  = 4;
    static const int CURR1 = A1;

    static const int SPD2  = 6;
    static const int DIR2  = 7;
    static const int CURR2 = A0;

private:
    MotorState *state;

    // Apply the current state->speed to hardware pins.
    void drive_control();

    int target_speed;  // commanded target speed
    int dir_pin;
    int speed_pin;
    int current_pin;
    char name;
};

#endif // MOTOR_DRIVER_H
