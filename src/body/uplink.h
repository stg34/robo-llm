#ifndef UPLINK_H
#define UPLINK_H

#include "HardwareSerial.h"
#include "protocol.h"

#ifndef GANGLION_TEST_MODE
#include "magnetometer.h"
#include "voltmeter.h"
#endif

// Uplink manages bidirectional communication over Serial1 (radio module).
//
// TX (Arduino -> host, protocol v2):
//   Framed telemetry packet every ~100ms (---/*** delimiters).
//   Motion done response sent immediately outside frames: D:XX\n
//
// RX (host -> Arduino):
//   L<DDD>\n — left motor speed (DDD = 000..255, 128 = stop)
//   R<DDD>\n — right motor speed
//   P<DDD>\n — piezo beep, DDD = duration in ms (000..999)
//   T+DDDD\n — turn CW by DDDD degrees   (motion controller)
//   T-DDDD\n — turn CCW by DDDD degrees
//   M+DDDD\n — move forward DDDD mm      (motion controller)
//   M-DDDD\n — move backward DDDD mm
//   G±DDDD\n — adjust gyro bias by ±DDDD/10 °/s (Python warm-sensor correction)
//
// Motion commands set a pending flag; body.ino polls and dispatches to MotionController.
class Uplink {
public:
#ifndef GANGLION_TEST_MODE
    Uplink(HardwareSerial *serial, Magnetometer *magnetometer, Voltmeter *voltmeter);
#else
    explicit Uplink(HardwareSerial *serial);
#endif
    // Called from serialEvent1(): drains the Serial1 RX buffer.
    void rx_event();

    // Call every main loop iteration to check telemetry timing and send if due.
    void loop();

    // Setters: called from main loop before uplink->loop().
    void set_speed_left(int speed)   { tx_speed_left  = speed; }
    void set_speed_right(int speed)  { tx_speed_right = speed; }
    void set_current_left(int curr)  { tx_current_left  = curr; }
    void set_current_right(int curr) { tx_current_right = curr; }
    void set_range_mm(int mm)        { tx_range_mm = mm; }
    void set_gyro_z(float dps)       { tx_gyro_z = dps; }

    // Getters: returns the last decoded motor command (-128..127, 0 = stop).
    int get_speed_left()  { return rx_speed_left;  }
    int get_speed_right() { return rx_speed_right; }

    // Motion command pending interface.
    // body.ino calls has_pending_motion() each cycle, dispatches to MotionController,
    // then clears. Exactly one pending command is held at a time.
    bool  has_pending_motion() { return motion_pending; }
    char  get_pending_type()   { return motion_type;    }  // 'T' or 'M'
    float get_pending_value()  { return motion_value;   }  // degrees or mm (signed)
    void  clear_pending_motion() { motion_pending = false; }

    // Gyro bias adjustment pending interface.
    // Python sends G±DDDD after warm calibration; body.ino dispatches to GyroSensor.
    bool  has_pending_bias()  { return bias_pending; }
    float get_pending_bias()  { return bias_delta;   }  // °/s to add to firmware _bias
    void  clear_pending_bias() { bias_pending = false; }

    // Fire command pending interface.
    // Host sends F\n; body.ino dispatches to ServoGun.
    bool has_pending_fire()   { return fire_pending; }
    void clear_pending_fire() { fire_pending = false; }

    // Direct servo position pending interface (calibration).
    // Host sends S<DDD>\n (0-180°); body.ino calls ServoGun::set_pos().
    bool has_pending_servo_pos()    { return servo_pos_pending; }
    int  get_pending_servo_pos()    { return servo_pos_degrees; }
    void clear_pending_servo_pos()  { servo_pos_pending = false; }

    // LED command pending interface.
    // Host sends E<R><B>\n where R and B are '0' or '1' — both LEDs in one atomic command.
    bool has_pending_led()        { return led_pending; }
    int  get_pending_led_red()    { return led_red_val; }   // 0 or 1
    int  get_pending_led_blue()   { return led_blue_val; }  // 0 or 1
    void clear_pending_led()      { led_pending = false; }
    void set_led_state(int r, int b) { tx_led_red = r; tx_led_blue = b; }

    // Send a motion-done response immediately (outside telemetry frame).
    // code: one of MOTION_DONE_* constants from protocol.h ("OK", "TO", etc.)
    // angle: gyro-accumulated degrees for turns (0.0 for moves). Appended as D:code:angle\n
    void send_motion_done(const char *code, float angle = 0.0f);

private:
    void tx_state();
    void process_command();
    void process_motion_command();

    HardwareSerial *serial;
#ifndef GANGLION_TEST_MODE
    Magnetometer   *magnetometer;
    Voltmeter      *voltmeter;
#endif

    // RX state machine (states defined in protocol.h)
    int  fsm_state;
    char command;           // current command char: L/R/P/T/M
    char digits[4];         // 3-digit buffer for L/R/P commands
    int  motion_sign;       // +1 or -1 for T/M commands
    char motion_digits[5];  // 4-digit buffer for T/M commands

    // Beep state
    unsigned long beep_end_ms;

    // TX state
    unsigned long tx_time;
    uint16_t      sq;

    // Telemetry snapshot
    int   tx_speed_left;
    int   tx_speed_right;
    int   tx_current_left;
    int   tx_current_right;
    int   tx_range_mm;
    float tx_gyro_z;

    // Decoded motor commands
    int rx_speed_left;
    int rx_speed_right;

    // Pending motion command
    bool  motion_pending;
    char  motion_type;   // 'T' or 'M'
    float motion_value;  // signed degrees or mm

    // Pending gyro bias adjustment
    bool  bias_pending;
    float bias_delta;    // °/s to add to GyroSensor::_bias

    // Pending fire command
    bool  fire_pending;

    // Pending direct servo position (calibration: S<DDD>\n, degrees 0-180)
    bool  servo_pos_pending;
    int   servo_pos_degrees;

    // Pending LED command (E<R><B>\n — atomic, both LEDs at once)
    bool  led_pending;
    int   led_red_val;   // 0 or 1
    int   led_blue_val;  // 0 or 1

    // LED state for telemetry
    int   tx_led_red;
    int   tx_led_blue;
};

#endif // UPLINK_H
