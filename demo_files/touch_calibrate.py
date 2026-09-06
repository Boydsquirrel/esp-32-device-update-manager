# ===== IMPORTS =====
import time
from machine import Pin, SPI
from ili9341 import Display
import ujson
from machine import PWM
# ===== DISPLAY SETUP =====
spi_disp = SPI(
    1,
    baudrate=40000000,
    sck=Pin(14),
    mosi=Pin(13),
    miso=Pin(12)
)
disp = Display(
    spi_disp,
    dc=Pin(2),
    cs=Pin(15),
    rst=Pin(0),
    width=320,
    height=240
)
SCREEN_W = 320
SCREEN_H = 240
# ===== BACKLIGHT SETUP =====
boot_brightness = 100
_pwm_backlight = None
_backlight_pin = None
try:
    _pwm = PWM(Pin(21))
    _pwm.freq(1000)
    duty = int((boot_brightness / 100) * 65535)
    _pwm.duty_u16(duty)
    _pwm_backlight = _pwm
except Exception:
    try:
        _backlight_pin = Pin(22, Pin.OUT)
        _backlight_pin.value(
            1 if boot_brightness > 0 else 0
        )
    except Exception:
        _backlight_pin = None
# ===== TOUCH SETUP =====
# Current touch pins:
# T_CLK = 7
# T_CS  = 9
# T_DIN = 10
# T_DO  = 39
# T_IRQ = 6
spi_touch = SPI(
    2,
    baudrate=2000000,
    polarity=0,
    phase=0,
    sck=Pin(7),
    mosi=Pin(10),
    miso=Pin(39)
)
cs = Pin(9, Pin.OUT)
irq = Pin(6, Pin.IN)
cs.value(1)
CAL_FILE = "touch_cal.json"

# Number of taps to average per calibration target.
# Higher = more accurate, but takes longer to calibrate.
TAPS_PER_POINT = 5


# ===== RAW TOUCH READ =====
def read(cmd):
    cs.value(0)
    spi_touch.write(bytearray([cmd]))
    data = spi_touch.read(2)
    cs.value(1)
    return ((data[0] << 8) | data[1]) >> 3


# ===== TOUCH READ =====
def read_touch():
    if irq.value():
        return None
    x = read(0xD0)
    y = read(0x90)
    return x, y


# ===== WAIT FOR SINGLE TOUCH (press + release) =====
def wait_touch():
    while True:
        t = read_touch()
        if t:
            # Wait for release
            while read_touch():
                time.sleep(0.01)
            return t
        time.sleep(0.01)


# ===== WAIT FOR AND AVERAGE MULTIPLE TAPS =====
def wait_touch_avg(taps=TAPS_PER_POINT):
    xs = []
    ys = []

    for i in range(taps):
        x, y = wait_touch()
        xs.append(x)
        ys.append(y)

        print(
            "  tap %d/%d -> raw (%d, %d)" % (i + 1, taps, x, y)
        )

        # small pause so the next tap isn't caught as a bounce
        # off the previous release
        time.sleep(0.15)

    avg_x = sum(xs) // len(xs)
    avg_y = sum(ys) // len(ys)

    return avg_x, avg_y


# ===== DRAW TARGET =====
def draw_target(x, y, tap_num, taps_total):
    disp.fill_rectangle(
        0,
        0,
        SCREEN_W,
        SCREEN_H,
        0x0000
    )
    disp.draw_rectangle(
        x - 10,
        y - 10,
        20,
        20,
        0xFFFF
    )


# ===== CALIBRATION POINTS =====
points = [
    (20, 20),                       # top left
    (SCREEN_W - 20, 20),            # top right
    (SCREEN_W - 20, SCREEN_H - 20), # bottom right
    (20, SCREEN_H - 20)             # bottom left
]

raw_points = []

print("Touch the 4 targets, %d taps each..." % TAPS_PER_POINT)

# ===== COLLECT TOUCH POINTS =====
for x, y in points:
    draw_target(x, y, 0, TAPS_PER_POINT)

    print("Target (%d, %d): tap it %d times" % (x, y, TAPS_PER_POINT))

    raw = wait_touch_avg(TAPS_PER_POINT)

    print("Target:", x, y, "Averaged raw:", raw)

    raw_points.append(raw)

    time.sleep(0.5)

# ===== COMPUTE CALIBRATION =====
xs = [p[0] for p in raw_points]
ys = [p[1] for p in raw_points]

cal = {
    "X_MIN": min(xs),
    "X_MAX": max(xs),
    "Y_MIN": min(ys),
    "Y_MAX": max(ys)
}

# ===== SAVE CALIBRATION =====
with open(CAL_FILE, "w") as f:
    ujson.dump(cal, f)

# ===== FINISH =====
disp.fill_rectangle(
    0,
    0,
    SCREEN_W,
    SCREEN_H,
    0x0000
)

print("Calibration saved:", cal)
print("Restart your main program.")
