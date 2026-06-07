#ifndef MOTION_CONTROLLER_H
#define MOTION_CONTROLLER_H

#include "motor_driver.h"
#include "gyroscope.h"
#include "rangefinder.h"
#include "protocol.h"

// MotionController — firmware-side PD motion control at 50 Hz.
//
// Turn: integrates bias-corrected gyro_z to track rotation angle.
//   PD output: u = Kp*error - Kd*gyro_z  (gyro IS the derivative of angle)
//   Motors stopped via set_speed_direct(), bypassing soft-start ramp.
//
// Move: uses rangefinder as position feedback with an Alpha-Beta filter
//   to estimate velocity between 10 Hz rangefinder updates.
//   PD output: u = Kp*error + Kd*vel  (vel < 0 when approaching → braking)
//
// When motion completes or fails, result is stored and is_done() returns true
// for exactly one loop() call. body.ino reads the result and sends D: response.
//
// While active, MotionController has exclusive motor control.
// Manual L/R commands from the host are ignored by body.ino.
class MotionController {
public:
    enum Result { RES_NONE, RES_OK, RES_TIMEOUT, RES_STUCK, RES_OBSTACLE, RES_JUMP };

    MotionController(MotorDriver *left, MotorDriver *right,
                     GyroSensor *gyro, Rangefinder *rangefinder);

    // Start a turn. degrees > 0 = CW, degrees < 0 = CCW.
    // Cancels any in-progress motion.
    void start_turn(float degrees);

    // Start a move. mm > 0 = forward, mm < 0 = backward.
    // Cancels any in-progress motion.
    void start_move(int mm);

    // Called every main loop cycle (50 Hz).
    // Returns true for exactly one cycle when motion just completed.
    bool loop();

    bool   is_idle()    { return _mode == MODE_IDLE; }
    Result get_result() { return _result; }

    // Degrees actually accumulated in the last turn (unsigned magnitude).
    // Valid after loop() returns true. Zero if last motion was a move.
    float  get_turn_accumulated() { return _turn_accumulated; }

    // ---------------------------------------------------------------------------
    // Tuning constants — adjust empirically
    // ---------------------------------------------------------------------------

    // Turn PD
    static constexpr float TURN_KP         = 1.5f;   // speed per degree of error
    static constexpr float TURN_KD         = 0.08f;  // damping per °/s of gyro rate
    static constexpr int   TURN_MAX_SPEED  = 70;     // max |PWM| offset
    static constexpr int   TURN_MIN_SPEED  = 55;     // min |PWM| when outside tolerance (stall guard)
    static constexpr float TURN_TOLERANCE  = 3.0f;   // degrees: "close enough"
    static constexpr float TURN_SETTLE_DPS = 15.0f;  // °/s: "stopped" threshold (lenient for fast settle)
    static constexpr float TURN_STALL_DPS  = 25.0f;  // °/s: below this = robot is stalled, apply min speed guard
    static constexpr int   TURN_SETTLE_N   = 5;      // consecutive settled ticks to exit
    // Gyro sign: positive gyro_z = CW on this robot (Z axis points down on this IMU mount).
    // Verified empirically: CW spin → firmware gz > 0; no negation needed.
    static constexpr float GYRO_SIGN       = -1.0f;

    // Move PD
    static constexpr float MOVE_KP         = 0.22f;  // speed per mm of range error
    static constexpr float MOVE_KD         = 0.05f;  // damping per mm/s of velocity
    static constexpr int   MOVE_MAX_SPEED  = 65;
    static constexpr float MOVE_TOLERANCE  = 80.0f;  // mm: "close enough"
    static constexpr float MOVE_SETTLE_VEL = 15.0f;  // mm/s: "stopped" threshold
    static constexpr int   MOVE_SETTLE_N   = 5;
    static constexpr int   MOVE_MIN_RANGE  = 200;    // mm: refuse forward move below this
    static constexpr int   MOVE_MAX_RANGE  = 8000;   // mm: skip out-of-range readings
    static constexpr int   MOVE_JUMP_MM    = 500;    // mm: beam slipped off obstacle
    static constexpr int   MOVE_STUCK_MM   = 8;      // mm: range change below this = stuck
    static constexpr int   MOVE_STUCK_N    = 60;     // ticks ~600ms: allow slow approach to target
    static constexpr int   MOVE_TRIM           = 0;      // static left-right offset (+ = left faster)
    // Heading correction: gyro-integrated angular drift → differential motor correction.
    // KP=1.5: 1° accumulated drift → 1.5 PWM units correction (max 15).
    // Sign: see loop_move comments.
    static constexpr float MOVE_HEADING_KP    = 1.5f;
    static constexpr int   MOVE_MAX_HEADING   = 15;

    // Alpha-Beta filter for move velocity estimation
    static constexpr float AB_ALPHA        = 0.7f;
    static constexpr float AB_BETA         = 0.2f;   // faster velocity convergence
    static constexpr float AB_MAX_VEL      = 600.0f; // mm/s: sanity clamp — noisy rangefinder
                                                      // can produce unrealistic velocity spikes
    // When range is frozen: after AB_FROZEN_N ticks, pin ab_pos and decay ab_vel.
    // Prevents prediction drift from reducing error and killing P-term when robot is stopped.
    static constexpr int   AB_FROZEN_N     = 15;     // ticks before decay starts (~150ms)
    static constexpr float AB_FROZEN_DECAY = 0.92f;  // ab_vel multiplier per tick (~0 in 300ms)

    // Minimum motor speed outside the settle zone.
    // Prevents stall when P-term alone is too weak to overcome static friction.
    static constexpr int   MOVE_MIN_SPEED  = 54;

    static constexpr unsigned long TIMEOUT_MS = 10000UL;

private:
    enum Mode { MODE_IDLE, MODE_TURN, MODE_MOVE };

    void loop_turn();
    void loop_move();
    void finish(Result r);
    void stop_motors();

    MotorDriver *_left;
    MotorDriver *_right;
    GyroSensor  *_gyro;
    Rangefinder *_rangefinder;

    Mode   _mode;
    Result _result;
    bool   _just_done;

    unsigned long _deadline_ms;
    int           _settle_count;

    // Turn state
    float         _turn_target;       // target degrees to rotate (signed)
    float         _turn_accumulated;  // gyro-integrated rotation (always positive)
    int           _turn_direction;    // +1 = CW, -1 = CCW
    unsigned long _turn_prev_ms;

    // Move state
    int           _move_target_range; // target rangefinder reading (mm)
    float         _ab_pos;            // alpha-beta position estimate (mm)
    float         _ab_vel;            // alpha-beta velocity (mm/s), negative = approaching
    int           _ab_prev_range;     // last range used for A-B correction
    unsigned long _move_prev_ms;
    unsigned long _range_prev_ms;     // millis() when last A-B correction was applied
    int           _move_stuck_count;
    int           _move_prev_range_stuck; // for stuck detection
    float         _move_heading_acc;  // gyro-integrated heading deviation during move (degrees)
    int           _ab_frozen_count;  // ticks since last range update (for prediction pinning)
};

#endif // MOTION_CONTROLLER_H
