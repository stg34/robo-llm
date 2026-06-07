#include "uplink.h"

// ---------------------------------------------------------------------------
// Constructors
// ---------------------------------------------------------------------------

#ifndef GANGLION_TEST_MODE

Uplink::Uplink(HardwareSerial *_serial, Magnetometer *_magnetometer, Voltmeter *_voltmeter) {
    serial       = _serial;
    magnetometer = _magnetometer;
    voltmeter    = _voltmeter;

    serial->begin(57600);

    fsm_state = UL_STATE_READY;
    command   = 0;
    digits[0] = digits[1] = digits[2] = digits[3] = 0;
    motion_sign = 1;
    memset(motion_digits, 0, sizeof(motion_digits));

    tx_time          = 0;
    sq               = 0;
    tx_speed_left    = 0;
    tx_speed_right   = 0;
    tx_current_left  = 0;
    tx_current_right = 0;
    tx_range_mm      = 0;
    tx_gyro_z        = 0.0f;
    rx_speed_left    = 0;
    rx_speed_right   = 0;
    beep_end_ms      = 0;
    motion_pending   = false;
    motion_type      = 0;
    motion_value     = 0;
    bias_pending       = false;
    bias_delta         = 0.0f;
    fire_pending       = false;
    servo_pos_pending  = false;
    servo_pos_degrees  = 0;
    led_pending        = false;
    led_red_val        = 0;
    led_blue_val       = 0;
    tx_led_red         = 0;
    tx_led_blue        = 0;
}

#else  // GANGLION_TEST_MODE

Uplink::Uplink(HardwareSerial *_serial) {
    serial = _serial;
    serial->begin(57600);

    fsm_state = UL_STATE_READY;
    command   = 0;
    digits[0] = digits[1] = digits[2] = digits[3] = 0;
    motion_sign = 1;
    memset(motion_digits, 0, sizeof(motion_digits));

    tx_time          = 0;
    sq               = 0;
    tx_speed_left    = 0;
    tx_speed_right   = 0;
    tx_current_left  = 0;
    tx_current_right = 0;
    tx_range_mm      = 0;
    tx_gyro_z        = 0.0f;
    rx_speed_left    = 0;
    rx_speed_right   = 0;
    beep_end_ms      = 0;
    motion_pending     = false;
    motion_type        = 0;
    motion_value       = 0;
    fire_pending       = false;
    servo_pos_pending  = false;
    servo_pos_degrees  = 0;
    led_pending        = false;
    led_red_val        = 0;
    led_blue_val       = 0;
    tx_led_red         = 0;
    tx_led_blue        = 0;
}

#endif  // GANGLION_TEST_MODE

// ---------------------------------------------------------------------------
// loop(): check telemetry timer and send if 100ms have elapsed
// ---------------------------------------------------------------------------

void Uplink::loop() {
    unsigned long now = millis();

    // Активный зуммер: выключить по истечении времени
    if (beep_end_ms > 0 && now >= beep_end_ms) {
        digitalWrite(BEEPER_PIN, LOW);
        beep_end_ms = 0;
    }

    if (now - tx_time >= 100UL) {
        tx_state();
        tx_time = now;
    }
}

// ---------------------------------------------------------------------------
// tx_state(): emit one protocol-v2 telemetry packet
// ---------------------------------------------------------------------------

void Uplink::tx_state() {
    serial->println(F("---"));

    serial->print(F("SQ:"));
    serial->println(sq);
    sq++;  // uint16_t wraps naturally at 65535 -> 0

    serial->print(F("LS:"));
    serial->println(tx_speed_left);

    serial->print(F("RS:"));
    serial->println(tx_speed_right);

    serial->print(F("LC:"));
    serial->println(tx_current_left);

    serial->print(F("RC:"));
    serial->println(tx_current_right);

#ifndef GANGLION_TEST_MODE
    serial->print(F("GX:"));
    serial->println(magnetometer->get_gauss_x(), 2);

    serial->print(F("GY:"));
    serial->println(magnetometer->get_gauss_y(), 2);

    serial->print(F("V:"));
    serial->println(voltmeter->get_voltage(), 2);

    serial->print(F("RF:"));
    serial->println(tx_range_mm);

    // if (tx_range_mm > 0) {
    //     serial->print(F("RF:"));
    //     serial->println(tx_range_mm);
    // }

    serial->print(F("GZ:"));
    serial->println(tx_gyro_z, 2);
#else
    // GANGLION_TEST_MODE: emit fixed dummy values so the Python parser can be exercised
    // without real sensors attached.
    serial->println(F("GX:-123.45"));
    serial->println(F("GY:456.78"));
    serial->println(F("V:4.20"));
    serial->println(F("RF:1500"));
    serial->println(F("GZ:0.00"));
#endif

    serial->print(F("LR:"));
    serial->println(tx_led_red);
    serial->print(F("LB:"));
    serial->println(tx_led_blue);

    serial->println(F("***"));
}

// ---------------------------------------------------------------------------
// send_motion_done(): emit D:<code>\n immediately (between telemetry frames)
// ---------------------------------------------------------------------------

void Uplink::send_motion_done(const char *code, float angle) {
    serial->print(F("D:"));
    serial->print(code);
    serial->print(':');
    serial->println(angle, 1);
}

// ---------------------------------------------------------------------------
// rx_event(): drain Serial1 RX buffer and advance the FSM
// ---------------------------------------------------------------------------

void Uplink::rx_event() {
    while (serial->available()) {
        char ch = (char)serial->read();

        switch (fsm_state) {
            // ---- L / R / P commands (3-digit fixed width) ----
            case UL_STATE_READY:
                if (ch == 'L' || ch == 'R' || ch == 'P' || ch == 'S') {
                    command   = ch;
                    fsm_state = UL_STATE_CMD;
                } else if (ch == 'T' || ch == 'M' || ch == 'G') {
                    command   = ch;
                    fsm_state = UL_STATE_MOTION_SIGN;
                } else if (ch == 'F') {
                    fsm_state = UL_STATE_FIRE_NL;
                } else if (ch == 'E') {
                    fsm_state = UL_STATE_LED_CH;
                }
                break;

            case UL_STATE_CMD:
                if (ch >= '0' && ch <= '9') { digits[0] = ch; fsm_state = UL_STATE_DIG1; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_DIG1:
                if (ch >= '0' && ch <= '9') { digits[1] = ch; fsm_state = UL_STATE_DIG2; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_DIG2:
                if (ch >= '0' && ch <= '9') { digits[2] = ch; fsm_state = UL_STATE_DIG3; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_DIG3:
                digits[3] = '\0';
                if (ch == '\n') process_command();
                fsm_state = UL_STATE_READY;
                break;

            // ---- T / M commands (sign + 4-digit fixed width) ----
            case UL_STATE_MOTION_SIGN:
                if (ch == '+' || ch == '-') {
                    motion_sign = (ch == '+') ? 1 : -1;
                    memset(motion_digits, 0, sizeof(motion_digits));
                    fsm_state = UL_STATE_MOTION_D1;
                } else {
                    fsm_state = UL_STATE_READY;
                }
                break;

            case UL_STATE_MOTION_D1:
                if (ch >= '0' && ch <= '9') { motion_digits[0] = ch; fsm_state = UL_STATE_MOTION_D2; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_MOTION_D2:
                if (ch >= '0' && ch <= '9') { motion_digits[1] = ch; fsm_state = UL_STATE_MOTION_D3; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_MOTION_D3:
                if (ch >= '0' && ch <= '9') { motion_digits[2] = ch; fsm_state = UL_STATE_MOTION_D4; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_MOTION_D4:
                if (ch >= '0' && ch <= '9') { motion_digits[3] = ch; fsm_state = UL_STATE_MOTION_NL; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_MOTION_NL:
                motion_digits[4] = '\0';
                if (ch == '\n') process_motion_command();
                fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_FIRE_NL:
                if (ch == '\n') fire_pending = true;
                fsm_state = UL_STATE_READY;
                break;

            // ---- E command: E<R><B>\n — set both LEDs atomically ----
            case UL_STATE_LED_CH:   // reading red value: '0' or '1'
                if (ch == '0' || ch == '1') { led_red_val = ch - '0'; fsm_state = UL_STATE_LED_VAL; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_LED_VAL:  // reading blue value: '0' or '1'
                if (ch == '0' || ch == '1') { led_blue_val = ch - '0'; fsm_state = UL_STATE_LED_NL; }
                else fsm_state = UL_STATE_READY;
                break;

            case UL_STATE_LED_NL:
                if (ch == '\n') led_pending = true;
                fsm_state = UL_STATE_READY;
                break;

            default:
                fsm_state = UL_STATE_READY;
                break;
        }
    }
}

// ---------------------------------------------------------------------------
// process_command(): decode a complete L/R<DDD>\n command
// ---------------------------------------------------------------------------

void Uplink::process_command() {
    if (command == 'P') {
        // Beep command: P<DDD>\n — duration in ms (0-999).
        // Активный зуммер: просто HIGH/LOW, без tone() чтобы не конфликтовать с PWM таймерами.
        int duration_ms = constrain(atoi(digits), 0, 999);
        if (duration_ms > 0) {
            digitalWrite(BEEPER_PIN, HIGH);
            beep_end_ms = millis() + (unsigned long)duration_ms;
        }
        return;
    }

    int raw   = constrain(atoi(digits), 0, 255);
    int speed = raw - 128;  // map 0..255 -> -128..127, 128 = stop

    if (command == 'S') {
        // Direct servo position: S<DDD>\n, degrees 0-180 (calibration).
        servo_pos_degrees = constrain(atoi(digits), 0, 180);
        servo_pos_pending = true;
        return;
    }

    if (command == 'L') {
        rx_speed_left  = speed;
#ifdef GANGLION_TEST_MODE
        tx_speed_left  = speed;
#endif
    } else if (command == 'R') {
        rx_speed_right = speed;
#ifdef GANGLION_TEST_MODE
        tx_speed_right = speed;
#endif
    }
}

// ---------------------------------------------------------------------------
// process_motion_command(): decode a complete T/M command
// ---------------------------------------------------------------------------

void Uplink::process_motion_command() {
    int magnitude = atoi(motion_digits);  // 4-digit value, 0-9999
    float value   = (float)(magnitude * motion_sign);

    if (command == 'G') {
        // Gyro bias correction: value is in tenths of °/s, convert to °/s
        bias_delta   = value / 10.0f;
        bias_pending = true;
        return;
    }

    motion_type    = command;  // 'T' or 'M'
    motion_value   = value;
    motion_pending = true;

#ifdef GANGLION_TEST_MODE
    Serial.print(F("PARSED:"));
    Serial.print(command);
    Serial.print(motion_sign > 0 ? '+' : '-');
    Serial.println(magnitude);
#endif
}
