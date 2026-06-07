#include "motion_controller.h"

MotionController::MotionController(MotorDriver *left, MotorDriver *right,
                                   GyroSensor *gyro, Rangefinder *rangefinder)
    : _left(left), _right(right), _gyro(gyro), _rangefinder(rangefinder),
      _mode(MODE_IDLE), _result(RES_NONE), _just_done(false),
      _deadline_ms(0), _settle_count(0),
      _turn_target(0), _turn_accumulated(0), _turn_direction(0), _turn_prev_ms(0),
      _move_target_range(0), _ab_pos(0), _ab_vel(0),
      _ab_prev_range(0), _move_prev_ms(0), _range_prev_ms(0),
      _move_stuck_count(0), _move_prev_range_stuck(0)
{}

// ---------------------------------------------------------------------------
// Public interface
// ---------------------------------------------------------------------------

void MotionController::start_turn(float degrees) {
    stop_motors();
    _mode             = MODE_TURN;
    _result           = RES_NONE;
    _just_done        = false;
    _turn_target      = abs(degrees);
    _turn_direction   = (degrees >= 0) ? 1 : -1;
    _turn_accumulated = 0.0f;
    _turn_prev_ms     = millis();
    _settle_count     = 0;
    _deadline_ms      = millis() + TIMEOUT_MS;
    Serial.print(F("TURN "));
    Serial.println(degrees);
}

void MotionController::start_move(int mm) {
    _rangefinder->loop();  // get a fresh range before starting
    int initial_range = _rangefinder->get_range_mm();

    if (mm > 0 && (initial_range == 0 || initial_range < MOVE_MIN_RANGE)) {
        finish(RES_OBSTACLE);
        return;
    }

    stop_motors();
    _mode                  = MODE_MOVE;
    _result                = RES_NONE;
    _just_done             = false;
    _move_target_range     = initial_range - mm;  // target: initial minus displacement
    _ab_pos                = (float)initial_range;
    _ab_vel                = 0.0f;
    _ab_prev_range         = initial_range;
    _move_prev_ms          = millis();
    _range_prev_ms         = millis();
    _move_heading_acc      = 0.0f;
    _ab_frozen_count       = 0;
    _move_stuck_count      = 0;
    _move_prev_range_stuck = initial_range;
    _settle_count          = 0;
    _deadline_ms           = millis() + TIMEOUT_MS;
    Serial.print(F("MOVE mm="));
    Serial.print(mm);
    Serial.print(F(" init="));
    Serial.print(initial_range);
    Serial.print(F(" target="));
    Serial.println(_move_target_range);
}

bool MotionController::loop() {
    _just_done = false;

    if (_mode == MODE_TURN) loop_turn();
    else if (_mode == MODE_MOVE) loop_move();

    return _just_done;
}

// ---------------------------------------------------------------------------
// Turn PD loop — called at 50 Hz
// ---------------------------------------------------------------------------

void MotionController::loop_turn() {
    unsigned long now = millis();

    if (now >= _deadline_ms) {
        finish(RES_TIMEOUT);
        return;
    }

    float dt = (now - _turn_prev_ms) / 1000.0f;
    _turn_prev_ms = now;
    if (dt <= 0) dt = 0.001f;

    // Accumulate rotation. Signed: increases when turning toward target,
    // decreases when correcting overshoot. The guard `if (delta > 0)` was
    // removed because it caused runaway: on overshoot the correction motion
    // produced delta < 0, accumulation froze, error stayed negative forever.
    float gz      = _gyro->get_deg_per_sec_z();
    float delta   = _turn_direction * GYRO_SIGN * gz * dt;
    _turn_accumulated += delta;

    float error     = _turn_target - _turn_accumulated;  // remaining degrees
    float signed_gz = (float)_turn_direction * GYRO_SIGN * gz;  // positive when spinning right way

    // PD: P drives toward target, D damps angular velocity
    float u = TURN_KP * error - TURN_KD * signed_gz;
    int speed = constrain((int)u, -TURN_MAX_SPEED, TURN_MAX_SPEED);

    // Minimum speed guard: prevent stall when driving toward target.
    // Only when robot is not already spinning (|gz| < TURN_STALL_DPS): if it's spinning fast,
    // it's decelerating from an overshoot — boosting to min speed here would cause oscillations
    // on low-friction surfaces (laminate). Guard only helps when robot is genuinely stalled.
    if (abs(error) > TURN_TOLERANCE && speed > 0 && speed < TURN_MIN_SPEED
            && fabsf(gz) < TURN_STALL_DPS)
        speed = TURN_MIN_SPEED;

    // Check completion: within tolerance AND spinning slowly
    if (abs(error) < TURN_TOLERANCE && abs(gz) < TURN_SETTLE_DPS) {
        _settle_count++;
        if (_settle_count >= TURN_SETTLE_N) {
            finish(RES_OK);
            return;
        }
    } else {
        _settle_count = 0;
    }

    // Apply motors: positive speed = CW (left forward, right reverse)
    int left_cmd  = +speed * _turn_direction;
    int right_cmd = -speed * _turn_direction;
    _left->set_speed_direct(left_cmd);
    _right->set_speed_direct(right_cmd);
}

// ---------------------------------------------------------------------------
// Move PD loop with Alpha-Beta filter — called at 50 Hz
// Rangefinder updates at ~10 Hz; A-B filter interpolates between readings.
// ---------------------------------------------------------------------------

void MotionController::loop_move() {
    unsigned long now = millis();

    if (now >= _deadline_ms) {
        finish(RES_TIMEOUT);
        return;
    }

    float dt = (now - _move_prev_ms) / 1000.0f;
    _move_prev_ms = now;
    if (dt <= 0) dt = 0.001f;

    // Alpha-Beta filter: predict
    _ab_pos += _ab_vel * dt;

    // Correct if rangefinder has a new valid reading
    int range = _rangefinder->get_range_mm();
    bool range_valid   = (range > 0 && range < MOVE_MAX_RANGE);
    bool range_updated = range_valid && (range != _ab_prev_range);

    if (range_updated) {
        // Beam-slip check: moving forward but range jumped sharply upward
        int fwd = (_move_target_range < _ab_prev_range) ? 1 : -1;
        if (fwd > 0 && (range - _ab_prev_range) > MOVE_JUMP_MM) {
            finish(RES_JUMP);
            return;
        }
        float dt_range = (now - _range_prev_ms) / 1000.0f;
        if (dt_range < 0.001f) dt_range = 0.001f;

        float residual = (float)range - _ab_pos;
        _ab_pos += AB_ALPHA * residual;
        _ab_vel += (AB_BETA / dt_range) * residual;
        _ab_vel = constrain(_ab_vel, -AB_MAX_VEL, AB_MAX_VEL);  // guard against noisy spikes
        _ab_prev_range = range;
        _range_prev_ms = now;
        _ab_frozen_count = 0;
    } else {
        // Range not updated: either sensor frozen or invalid (range=0 / I2C glitch).
        // After AB_FROZEN_N ticks: decay velocity to let P-term drive.
        // Only pin ab_pos if reading is valid (range>0); never pin to 0.
        _ab_frozen_count++;
        if (_ab_frozen_count >= AB_FROZEN_N) {
            _ab_vel *= AB_FROZEN_DECAY;
            if (range_valid) {
                _ab_pos = (float)range;  // pin to stale-but-valid range
            }
            // if range invalid, prediction keeps running — better than pinning to 0
        }
    }

    float error = _ab_pos - (float)_move_target_range;  // positive = too far → move forward

    // Stuck detection: only when range is valid AND we are far from the target.
    // Within tolerance the settle mechanism takes over — suppressing stuck prevents
    // it from firing while velocity is still decaying toward the settle threshold.
    if (range_valid && abs(error) > MOVE_TOLERANCE) {
        if (abs(range - _move_prev_range_stuck) < MOVE_STUCK_MM) {
            _move_stuck_count++;
            if (_move_stuck_count >= MOVE_STUCK_N) {
                finish(RES_STUCK);
                return;
            }
        } else {
            _move_stuck_count = 0;
            _move_prev_range_stuck = range;
        }
    }

    // PD: P proportional to distance error, D damps velocity
    // _ab_vel is negative when moving forward (range decreasing) → damping effect correct
    float u = MOVE_KP * error + MOVE_KD * _ab_vel;
    int speed = constrain((int)u, -MOVE_MAX_SPEED, MOVE_MAX_SPEED);

    // Minimum speed guard: prevent stall when far from target.
    // P-term alone may be too weak to overcome static friction at small errors.
    if (error >= MOVE_TOLERANCE && speed > 0 && speed < MOVE_MIN_SPEED) speed = MOVE_MIN_SPEED;
    if (error <= -MOVE_TOLERANCE && speed < 0 && speed > -MOVE_MIN_SPEED) speed = -MOVE_MIN_SPEED;

    // Debug: print filter state every 20 ticks (~400ms) to USB Serial
    static uint8_t dbg_tick = 0;
    if (++dbg_tick >= 20) {
        dbg_tick = 0;
        Serial.print(F("MV rng=")); Serial.print(range);
        Serial.print(F(" pos="));   Serial.print((int)_ab_pos);
        Serial.print(F(" vel="));   Serial.print((int)_ab_vel);
        Serial.print(F(" err="));   Serial.print((int)error);
        Serial.print(F(" spd="));   Serial.print(speed);
        Serial.print(F(" frz="));   Serial.print(_ab_frozen_count);
        Serial.print(F(" stk="));   Serial.print(_move_stuck_count);
        Serial.print(F(" set="));   Serial.println(_settle_count);
    }

    // Check completion: within tolerance AND velocity low
    if (abs(error) < MOVE_TOLERANCE && abs(_ab_vel) < MOVE_SETTLE_VEL) {
        _settle_count++;
        if (_settle_count >= MOVE_SETTLE_N) {
            finish(RES_OK);
            return;
        }
    } else {
        _settle_count = 0;
    }

    // Heading correction: integrate gyro to detect angular drift during straight move.
    // GYRO_SIGN * gyro_z > 0 means CW rotation.
    // CW drift (heading_acc > 0): correct by turning CCW → right motor faster, left slower.
    // CCW drift (heading_acc < 0): correct by turning CW  → left motor faster, right slower.
    _move_heading_acc += GYRO_SIGN * _gyro->get_deg_per_sec_z() * dt;
    int heading_corr = constrain((int)(MOVE_HEADING_KP * _move_heading_acc),
                                 -MOVE_MAX_HEADING, MOVE_MAX_HEADING);

    // Apply motors: heading_corr > 0 = CW drift → right faster, left slower (CCW correction)
    _left->set_speed_direct(speed + MOVE_TRIM - heading_corr);
    _right->set_speed_direct(speed - MOVE_TRIM + heading_corr);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

void MotionController::finish(Result r) {
    stop_motors();
    _mode      = MODE_IDLE;
    _result    = r;
    _just_done = true;
    Serial.print(F("DONE "));
    switch (r) {
        case RES_OK:       Serial.println(MOTION_DONE_OK); break;
        case RES_TIMEOUT:  Serial.println(MOTION_DONE_TO); break;
        case RES_STUCK:    Serial.println(MOTION_DONE_ST); break;
        case RES_OBSTACLE: Serial.println(MOTION_DONE_OB); break;
        case RES_JUMP:     Serial.println(MOTION_DONE_JM); break;
        default:           Serial.println('?'); break;
    }
}

void MotionController::stop_motors() {
    _left->set_speed_direct(0);
    _right->set_speed_direct(0);
}
