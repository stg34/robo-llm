import math


class Compass:
    """Converts raw magnetometer values to heading.

    Calibration coefficients from controls/tty.rb (MagYaw class).
    atan2(x, y) with x as first argument gives 0=north, clockwise.
    """

    def __init__(
        self,
        gain_x: float = 0.525,
        gain_y: float = 0.485,
        shift_x: float = -0.13,
        shift_y: float = 0.25,
    ):
        self._gain_x = gain_x
        self._gain_y = gain_y
        self._shift_x = shift_x
        self._shift_y = shift_y

    def heading(self, mag_x: float, mag_y: float) -> float:
        """Return heading in degrees: 0=north, 90=east, clockwise. Range: [0, 360)."""
        x = (mag_x + self._shift_x) * self._gain_x
        y = (mag_y + self._shift_y) * self._gain_y
        return math.degrees(math.atan2(x, y)) % 360
