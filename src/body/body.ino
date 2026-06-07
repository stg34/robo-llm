// Ganglion robot firmware — FRANCY
// Protocol v2 (see docs/protocol.md)
//
// Build modes:
//   Normal:    full hardware (motors + compass + voltmeter + rangefinder + radio telemetry)
//   GANGLION_TEST_MODE: sensors/motors not initialised; dummy telemetry; command echo
//              Compile with: -DTEST_MODE  (Arduino IDE: add to build flags)
//
// Wiring summary:
//   Serial1       — radio module (57600 baud), telemetry TX + command RX
//   Serial (USB)  — debug output only
//   Motor 1 left  — DIR=4, PWM=5, CURR=A1
//   Motor 2 right — DIR=7, PWM=6, CURR=A0
//   Voltmeter     — A3 (resistor divider, see voltmeter.h for calibration)
//   Compass       — I2C (SDA/SCL), Troyka IMU, address COMPASS_ADDRESS_V1
//   Rangefinder   — I2C slave (Arduino Pro Micro, address 0x08, lidar_slave.ino)

#include "protocol.h"

// Uncomment to enable protocol test mode (uses Serial1, dummy telemetry, no motors/sensors):
// #define GANGLION_TEST_MODE

#ifndef GANGLION_TEST_MODE
#include <Wire.h>
#include "motor_driver.h"
#include "magnetometer.h"
#include "voltmeter.h"
#include "rangefinder.h"
#include "gyroscope.h"
#include "motion_controller.h"
#include "servo_gun.h"
#endif

#include "uplink.h"

// ---------------------------------------------------------------------------
// GlobalState: owns all subsystem pointers
// ---------------------------------------------------------------------------

struct GlobalState {
#ifndef GANGLION_TEST_MODE
    MotorDriver      *motor_left;
    MotorDriver      *motor_right;
    Magnetometer     *magnetometer;
    Voltmeter        *voltmeter;
    Rangefinder      *rangefinder;
    GyroSensor       *gyroscope;
    MotionController *motion;
    ServoGun         *servo_gun;
#endif
    Uplink           *uplink;
};

// ---------------------------------------------------------------------------
// Global objects
// ---------------------------------------------------------------------------

#ifndef GANGLION_TEST_MODE
// Compass instance lives in global scope (required by Troyka IMU library).
Compass compass(COMPASS_ADDRESS_V1);
#endif

GlobalState *robot;

// ---------------------------------------------------------------------------
// setup()
// ---------------------------------------------------------------------------

void setup() {
    pinMode(BEEPER_PIN, OUTPUT);
    pinMode(LED_RED_PIN, OUTPUT);
    digitalWrite(LED_RED_PIN, LOW);
    pinMode(LED_BLUE_PIN, OUTPUT);
    digitalWrite(LED_BLUE_PIN, LOW);
    Serial.begin(SERIAL_BAUD);
    // while (!Serial);
    Serial.println("INIT");
#ifdef GANGLION_TEST_MODE
    // In GANGLION_TEST_MODE use Serial1 (radio module) — same path as production.
    Serial.println(F("TEST MODE ACTIVE - do not use in production"));
    robot = new GlobalState();
    robot->uplink = new Uplink(&Serial1);
#else
    robot = new GlobalState();
    robot->motor_left    = new MotorDriver(MotorDriver::DIR1, MotorDriver::SPD1, MotorDriver::CURR1, 'L');
    robot->motor_right   = new MotorDriver(MotorDriver::DIR2, MotorDriver::SPD2, MotorDriver::CURR2, 'R');
    robot->magnetometer  = new Magnetometer(&compass);
    robot->voltmeter     = new Voltmeter();
    robot->rangefinder   = new Rangefinder();
    robot->gyroscope     = new GyroSensor();
    robot->motion        = new MotionController(robot->motor_left, robot->motor_right,
                                                robot->gyroscope, robot->rangefinder);
    robot->uplink        = new Uplink(&Serial1, robot->magnetometer, robot->voltmeter);
    robot->servo_gun     = new ServoGun();
    robot->servo_gun->attach();

    Serial.println("Калибровка гироскопа (~500мс)...");
    robot->gyroscope->calibrate(30);
    Serial.println("Готов.");
#endif
}

// ---------------------------------------------------------------------------
// loop()  20ms period = 50 Hz
// ---------------------------------------------------------------------------

static int loop_count = 0;

void loop() {
    delay(LOOP_DELAY_MS);
    loop_count++;

#ifndef GANGLION_TEST_MODE
    // --- Gyroscope: read every cycle for 50 Hz PD control ---
    robot->gyroscope->loop();

    // --- Gyro bias correction from Python (warm-sensor calibration) ---
    if (robot->uplink->has_pending_bias()) {
        float adj = robot->uplink->get_pending_bias();
        robot->uplink->clear_pending_bias();
        robot->gyroscope->adjust_bias(adj);
        Serial.print(F("BIAS_ADJ "));
        Serial.println(adj);
    }

    // --- LEDs: E<R><B>\n — set both LEDs atomically ---
    if (robot->uplink->has_pending_led()) {
        int r = robot->uplink->get_pending_led_red();
        int b = robot->uplink->get_pending_led_blue();
        robot->uplink->clear_pending_led();
        digitalWrite(LED_RED_PIN,  r ? HIGH : LOW);
        digitalWrite(LED_BLUE_PIN, b ? HIGH : LOW);
        robot->uplink->set_led_state(r, b);
    }

    // --- Servo gun: F command = fire cycle; S command = set position (calibration) ---
    if (robot->uplink->has_pending_fire()) {
        robot->uplink->clear_pending_fire();
        robot->servo_gun->trigger();
    }
    if (robot->uplink->has_pending_servo_pos()) {
        int deg = robot->uplink->get_pending_servo_pos();
        robot->uplink->clear_pending_servo_pos();
        robot->servo_gun->set_pos(deg);
    }
    if (robot->servo_gun->loop()) {
        robot->uplink->send_motion_done(MOTION_DONE_OK, 0.0f);
    }

    // --- Check for new motion commands from host ---
    if (robot->uplink->has_pending_motion()) {
        char type    = robot->uplink->get_pending_type();
        float value  = robot->uplink->get_pending_value();
        robot->uplink->clear_pending_motion();
        if (type == 'T')
            robot->motion->start_turn(value);
        else
            robot->motion->start_move((int)value);
    }

    // --- Motion controller tick (PD loop, exclusive motor control when active) ---
    bool motion_done = robot->motion->loop();
    if (motion_done) {
        const char *code = MOTION_DONE_OK;
        switch (robot->motion->get_result()) {
            case MotionController::RES_TIMEOUT:  code = MOTION_DONE_TO; break;
            case MotionController::RES_STUCK:    code = MOTION_DONE_ST; break;
            case MotionController::RES_OBSTACLE: code = MOTION_DONE_OB; break;
            case MotionController::RES_JUMP:     code = MOTION_DONE_JM; break;
            default: break;
        }
        robot->uplink->send_motion_done(code, robot->motion->get_turn_accumulated());
    }

    // --- Manual motor commands: only when motion controller is idle ---
    if (robot->motion->is_idle()) {
        robot->motor_left->set_speed(robot->uplink->get_speed_left());
        robot->motor_right->set_speed(robot->uplink->get_speed_right());
        robot->motor_left->loop();
        robot->motor_right->loop();
    }

    // --- Telemetry snapshot ---
    robot->uplink->set_speed_left(robot->motor_left->get_speed());
    robot->uplink->set_speed_right(robot->motor_right->get_speed());
    robot->uplink->set_current_left(robot->motor_left->get_current());
    robot->uplink->set_current_right(robot->motor_right->get_current());

    // --- Rangefinder: every 5th cycle = 50ms → 20 Hz ---
    if (loop_count % RANGEFINDER_EVERY_N == 0) {
        robot->rangefinder->loop();
    }

    // --- Telemetry + slow sensors: every 10th cycle = 100ms → 10 Hz ---
    if (loop_count % TELEMETRY_EVERY_N == 0) {
        robot->magnetometer->loop();
        robot->voltmeter->loop();
        robot->uplink->set_range_mm(robot->rangefinder->get_range_mm());
        robot->uplink->set_gyro_z(robot->gyroscope->get_deg_per_sec_z());
        robot->uplink->loop();
    }
#else
    // GANGLION_TEST_MODE: only drive the uplink (telemetry + command parsing).
    robot->uplink->loop();
#endif
}

// ---------------------------------------------------------------------------
// serialEvent1(): called by Arduino runtime when Serial1 RX data is ready.
// Drains the buffer and advances the command FSM in Uplink.
// ---------------------------------------------------------------------------

// serialEvent1() fires when Serial1 (radio module) has data — used in both modes.
void serialEvent1() {
    robot->uplink->rx_event();
}
