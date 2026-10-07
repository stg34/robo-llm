import dataclasses


@dataclasses.dataclass
class TelemetryFrame:
    sq: int = 0               # sequence number 0-65535
    left_speed: int = 0       # -128..127, 0 = stop
    right_speed: int = 0
    left_current: int = 0     # raw ADC 0-1023
    right_current: int = 0
    mag_x: float = 0.0        # milligauss
    mag_y: float = 0.0
    voltage: float = 0.0      # volts
    range_mm: int = 0         # mm, 0 = no data
    gyro_z: float = 0.0       # deg/sec, Z-axis angular rate (positive = CCW)
    timestamp: float = 0.0    # time.monotonic()
    packets_lost: int = 0     # cumulative lost packets
    led_red: bool = False     # decorative red LED state (pin 2)
    led_blue: bool = False    # decorative blue LED state (pin 3)
