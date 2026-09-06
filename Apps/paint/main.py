# paint.py — simple touchscreen paint app
#
# Touch & drag on canvas: freehand draw
# Touch a sidebar swatch: pick color
# UP/DOWN/LEFT/RIGHT:     move cursor 1px and paint as it moves
# SELECT:                 clear canvas
# BACK:                   quit

import time
from machine import Pin, SPI, PWM
from ili9341 import Display
import ujson

# ===== DISPLAY SETUP =====
spi_disp = SPI(1, baudrate=40000000, sck=Pin(14), mosi=Pin(13), miso=Pin(12))
disp = Display(spi_disp, dc=Pin(2), cs=Pin(15), rst=Pin(0), width=320, height=240)
SCREEN_W = 320
SCREEN_H = 240

# ===== BACKLIGHT =====
pwm = PWM(Pin(21))
pwm.freq(1000)
pwm.duty_u16(65535)

# ===== BUTTONS =====
_BTN_PINS = {
    "UP":     Pin(16, Pin.IN, Pin.PULL_UP),
    "DOWN":   Pin(4,  Pin.IN, Pin.PULL_UP),
    "LEFT":   Pin(17, Pin.IN, Pin.PULL_UP),
    "RIGHT":  Pin(5,  Pin.IN, Pin.PULL_UP),
    "SELECT": Pin(8,  Pin.IN, Pin.PULL_UP),
    "BACK":   Pin(18, Pin.IN, Pin.PULL_UP),
}
_BTN_ORDER = ["UP", "DOWN", "LEFT", "RIGHT", "SELECT", "BACK"]
_last_states = dict((k, 1) for k in _BTN_ORDER)


def read_btn():
    for name in _BTN_ORDER:
        v = _BTN_PINS[name].value()
        if v == 0 and _last_states[name] == 1:
            _last_states[name] = 0
            return name
        _last_states[name] = v
    return None


# ===== TOUCH SETUP =====
spi_touch = SPI(2, baudrate=2000000, polarity=0, phase=0,
                 sck=Pin(7), mosi=Pin(10), miso=Pin(39))
cs = Pin(9, Pin.OUT)
irq = Pin(6, Pin.IN)
cs.value(1)
CAL_FILE = "touch_cal.json"

try:
    with open(CAL_FILE, "r") as f:
        cal = ujson.load(f)
except Exception:
    cal = {"X_MIN": 400, "X_MAX": 3900, "Y_MIN": 200, "Y_MAX": 3900}

TOUCH_SAMPLES = 8


def read_raw(cmd):
    cs.value(0)
    spi_touch.write(bytearray([cmd]))
    data = spi_touch.read(2)
    cs.value(1)
    return ((data[0] << 8) | data[1]) >> 3


def map_value(v, in_min, in_max, out_min, out_max):
    if in_max == in_min:
        return out_min
    v = max(in_min, min(in_max, v))
    return int((v - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


def touch_pixel(samples=TOUCH_SAMPLES):
    if irq.value():
        return None

    xs = []
    ys = []

    for _ in range(samples):
        if irq.value():
            break
        xs.append(read_raw(0xD0))
        ys.append(read_raw(0x90))
        time.sleep(0.01)

    if not xs:
        return None

    if len(xs) >= 5:
        xs_t = sorted(xs)[1:-1]
        ys_t = sorted(ys)[1:-1]
    else:
        xs_t = xs
        ys_t = ys

    avg_x = sum(xs_t) // len(xs_t)
    avg_y = sum(ys_t) // len(ys_t)

    # Same axis swap as keyboard.py — confirmed correct for this panel
    x = map_value(avg_y, cal["Y_MIN"], cal["Y_MAX"], 0, SCREEN_W)
    y = map_value(avg_x, cal["X_MIN"], cal["X_MAX"], 0, SCREEN_H)

    return x, y


# ===== LAYOUT =====
BLACK = 0x0000
WHITE = 0xFFFF

SIDEBAR_W = 40
CANVAS_W = SCREEN_W - SIDEBAR_W

COLORS = [
    ("WHITE",   0xFFFF),
    ("RED",     0xF800),
    ("GREEN",   0x07E0),
    ("BLUE",    0x001F),
    ("YELLOW",  0xFFE0),
    ("CYAN",    0x07FF),
    ("MAGENTA", 0xF81F),
]
color_idx = 0

SWATCH_H = SCREEN_H // len(COLORS)

# Brush used for touch drag-drawing
TOUCH_BRUSH_SIZE = 6

# Brush used for button/cursor pixel-precision drawing
BUTTON_PIXEL_SIZE = 2

last_point = None


def brush_color():
    return COLORS[color_idx][1]


# ===== SIDEBAR =====

def draw_sidebar():
    for i, (name, col) in enumerate(COLORS):
        y = i * SWATCH_H
        disp.fill_rectangle(CANVAS_W, y, SIDEBAR_W, SWATCH_H, col)

        border = WHITE if i == color_idx else BLACK
        disp.draw_rectangle(CANVAS_W, y, SIDEBAR_W, SWATCH_H, border)


def sidebar_hit(x, y):
    if x < CANVAS_W:
        return None
    idx = y // SWATCH_H
    return max(0, min(len(COLORS) - 1, idx))


# ===== DRAWING HELPERS =====

def stamp(x, y, size):
    half = size // 2
    x0 = max(0, min(CANVAS_W - size, x - half))
    y0 = max(0, min(SCREEN_H - size, y - half))
    disp.fill_rectangle(x0, y0, size, size, brush_color())


def draw_line(p0, p1, size):
    x0, y0 = p0
    x1, y1 = p1

    steps = max(abs(x1 - x0), abs(y1 - y0), 1)

    for i in range(steps + 1):
        x = x0 + (x1 - x0) * i // steps
        y = y0 + (y1 - y0) * i // steps
        stamp(x, y, size)


def clear_canvas():
    disp.fill_rectangle(0, 0, CANVAS_W, SCREEN_H, BLACK)


# ===== CURSOR (button-driven pixel painting) =====

cursor_x = CANVAS_W // 2
cursor_y = SCREEN_H // 2


def move_cursor(dx, dy):
    global cursor_x, cursor_y

    cursor_x = max(0, min(CANVAS_W - 1, cursor_x + dx))
    cursor_y = max(0, min(SCREEN_H - 1, cursor_y + dy))

    stamp(cursor_x, cursor_y, BUTTON_PIXEL_SIZE)


# ===== MAIN =====
clear_canvas()
draw_sidebar()

print("Paint ready.")
print("Touch/drag canvas to draw. Touch sidebar to pick color.")
print("UP/DOWN/LEFT/RIGHT move the cursor and paint pixel-by-pixel.")
print("SELECT=clear, BACK=quit.")
print("Color:", COLORS[color_idx][0])

while True:

    # --- touch ---
    tp = touch_pixel()

    if tp:
        tx, ty = tp

        hit = sidebar_hit(tx, ty)

        if hit is not None:
            if hit != color_idx:
                color_idx = hit
                draw_sidebar()
                print("Color:", COLORS[color_idx][0])
            last_point = None

        else:
            if last_point is not None:
                draw_line(last_point, tp, TOUCH_BRUSH_SIZE)
            else:
                stamp(tx, ty, TOUCH_BRUSH_SIZE)
            last_point = tp

    else:
        last_point = None

    # --- buttons ---
    btn = read_btn()

    if btn == "UP":
        move_cursor(0, -1)

    elif btn == "DOWN":
        move_cursor(0, 1)

    elif btn == "LEFT":
        move_cursor(-1, 0)

    elif btn == "RIGHT":
        move_cursor(1, 0)

    elif btn == "SELECT":
        clear_canvas()
        print("Canvas cleared")

    elif btn == "BACK":
        print("Quit")
        break

    time.sleep(0.02)
