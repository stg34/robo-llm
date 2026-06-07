#ifndef PROTOCOL_H
#define PROTOCOL_H

// Ganglion Serial Protocol v2
// See docs/protocol.md for full specification.

// Baud rate for Serial1 (radio module) and Serial (USB debug)
#define SERIAL_BAUD 57600

// Telemetry frame markers
#define PROTO_FRAME_BEGIN "---"
#define PROTO_FRAME_END   "***"

// Loop timing
#define LOOP_DELAY_MS          10   // main loop period:   100 Hz
#define RANGEFINDER_EVERY_N     5   //  5 * 10ms =  50ms → rangefinder at 20 Hz
#define TELEMETRY_EVERY_N      10   // 10 * 10ms = 100ms → telemetry at 10 Hz

// Motor encoding: 0-255 over UART, 128 = stop
// Internal range after conversion: -128..127
#define MOTOR_NEUTRAL 128

// Active buzzer (активный зуммер): HIGH = on, LOW = off
#define BEEPER_PIN  8

// ---------------------------------------------------------------------------
// Motion controller commands (host -> Arduino)
// T+DDDD\n — turn CW by DDDD degrees  (0–360)
// T-DDDD\n — turn CCW by DDDD degrees
// M+DDDD\n — move forward DDDD mm     (0–9999)
// M-DDDD\n — move backward DDDD mm
//
// Motion done response (Arduino -> host, outside telemetry frame)
// D:OK\n   — completed successfully
// D:TO\n   — timeout
// D:ST\n   — stuck (wheel caught / obstacle)
// D:OB\n   — obstacle (range too close to start forward move)
// D:JM\n   — rangefinder beam jumped (slipped off obstacle)
// ---------------------------------------------------------------------------
#define MOTION_DONE_OK  "OK"
#define MOTION_DONE_TO  "TO"
#define MOTION_DONE_ST  "ST"
#define MOTION_DONE_OB  "OB"
#define MOTION_DONE_JM  "JM"

// FSM states for Uplink command parser
#define UL_STATE_READY        0
#define UL_STATE_CMD          1   // L/R/P: reading first digit
#define UL_STATE_DIG1         2
#define UL_STATE_DIG2         3
#define UL_STATE_DIG3         4   // awaiting \n; triggers process_command
#define UL_STATE_MOTION_SIGN  5   // T/M: reading sign (+ or -)
#define UL_STATE_MOTION_D1    6   // reading motion digits 1-4
#define UL_STATE_MOTION_D2    7
#define UL_STATE_MOTION_D3    8
#define UL_STATE_MOTION_D4    9
#define UL_STATE_MOTION_NL   10   // awaiting \n; triggers process_motion_command
#define UL_STATE_FIRE_NL     11   // F command: awaiting \n; sets fire_pending

// Servo gun
#define SERVO_PIN 9

// Decorative LEDs
#define LED_RED_PIN  10
#define LED_BLUE_PIN 11

// Additional FSM states for LED command (E<R><B>\n — atomic, both LEDs at once)
#define UL_STATE_LED_CH   12   // reading red value: '0' or '1'
#define UL_STATE_LED_VAL  13   // reading blue value: '0' or '1'
#define UL_STATE_LED_NL   14   // awaiting \n

#endif // PROTOCOL_H
