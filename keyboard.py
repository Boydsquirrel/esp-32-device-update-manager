# keyboard.py — shared keyboard utility for MicroMate
# Usage:
#   import keyboard
#   result = keyboard.get_input(disp, prompt="Enter name:")
#   # Returns the typed string on Select+DONE, or None if Back was pressed
#   # to cancel.

# Buttons (same 6-button scheme as the rest of MicroMate):
#   LEFT / RIGHT  -> move the highlight within the current row
#   UP / DOWN     -> move the highlight to the row above / below (wraps)
#   SELECT        -> press the highlighted key
#   BACK          -> cancel and return None

import time
import gc
from machine import Pin


# ============================================================
# BUTTONS
# ============================================================

try:
    _BTN_PINS = {
        "UP":     Pin(16, Pin.IN, Pin.PULL_UP),
        "DOWN":   Pin(4,  Pin.IN, Pin.PULL_UP),
        "LEFT":   Pin(17, Pin.IN, Pin.PULL_UP),
        "RIGHT":  Pin(5,  Pin.IN, Pin.PULL_UP),
        "SELECT": Pin(8,  Pin.IN, Pin.PULL_UP),
        "BACK":   Pin(18, Pin.IN, Pin.PULL_UP),
    }
except Exception as e:
    raise RuntimeError(
        "keyboard.py: failed to init button pins (%s). "
        "Check for a pin conflict with buttons.py / launcher." % e
    )

_BTN_ORDER = ["UP", "DOWN", "LEFT", "RIGHT", "SELECT", "BACK"]

_last_states = dict((k, 1) for k in _BTN_ORDER)


def _btn():
    """Returns one of 'UP','DOWN','LEFT','RIGHT','SELECT','BACK'
    on a fresh press, or None if nothing new was pressed.
    """
    global _last_states

    for name in _BTN_ORDER:
        v = _BTN_PINS[name].value()

        if v == 0 and _last_states[name] == 1:
            _last_states[name] = 0
            return name

        _last_states[name] = v

    return None


# ============================================================
# TOUCH
# ============================================================

_touch_ok = False
_spi_t = None
_cs = None
_irq = None

_cal = {
    "X_MIN": 400,
    "X_MAX": 3900,
    "Y_MIN": 200,
    "Y_MAX": 3900
}


# Current touch wiring:
#
# T_CLK = 7
# T_CS  = 9
# T_DIN = 10
# T_DO  = 39
# T_IRQ = 6

try:
    from machine import SPI

    _spi_t = SPI(
        2,
        baudrate=2000000,
        polarity=0,
        phase=0,
        sck=Pin(7),
        mosi=Pin(10),
        miso=Pin(39)
    )

    _cs = Pin(9, Pin.OUT)
    _irq = Pin(6, Pin.IN)

    _cs.value(1)
    _touch_ok = True

except Exception:
    _touch_ok = False


# ============================================================
# TOUCH CALIBRATION
# ============================================================

try:
    import ujson

    with open("touch_cal.json", "r") as f:
        _cal = ujson.load(f)

except Exception:
    pass


def _read_raw(cmd):
    _cs.value(0)

    _spi_t.write(bytearray([cmd]))
    d = _spi_t.read(2)

    _cs.value(1)

    return ((d[0] << 8) | d[1]) >> 3


def _map(v, mn, mx, omn, omx):
    if mx == mn:
        return omn

    return max(
        omn,
        min(
            omx,
            int((v - mn) * (omx - omn) / (mx - mn) + omn)
        )
    )


# Number of raw touch samples averaged into one reading.
# Higher = steadier but a bit more latency per poll
# (each sample costs ~10ms). 8 adds ~80ms worst case.
TOUCH_SAMPLES = 8


def _touch_pixel(samples=TOUCH_SAMPLES):
    if not _touch_ok or _irq.value():
        return None

    xs = []
    ys = []

    for _ in range(samples):

        # bail early if the finger lifted mid-sample
        if _irq.value():
            break

        xs.append(_read_raw(0xD0))
        ys.append(_read_raw(0x90))

        time.sleep(0.01)

    if not xs:
        return None

    # Trimmed mean: drop the single highest and lowest reading
    # (per axis) before averaging, so one noisy spike sample
    # doesn't skew the result. Falls back to a plain mean if we
    # don't have enough samples to trim.
    if len(xs) >= 5:
        xs_t = sorted(xs)[1:-1]
        ys_t = sorted(ys)[1:-1]
    else:
        xs_t = xs
        ys_t = ys

    avg_x = sum(xs_t) // len(xs_t)
    avg_y = sum(ys_t) // len(ys_t)

    # Confirmed correct for this panel: axes are swapped relative
    # to the raw command names — 0x90 (Y range) maps to screen X,
    # 0xD0 (X range) maps to screen Y.
    return (
        _map(avg_y, _cal["Y_MIN"], _cal["Y_MAX"], 0, 320),
        _map(avg_x, _cal["X_MIN"], _cal["X_MAX"], 0, 240)
    )


# ============================================================
# COLOURS
# ============================================================

BLACK = 0x0000
WHITE = 0xFFFF
RED   = 0xF800
CYAN  = 0x07FF
KEY_BG = 0x4208


# ============================================================
# LAYOUT CONSTANTS
# ============================================================

# Screen: 320x240
# Textbox: 0-80 (80px)
# Keys:    84-205
# Slack:   205-240

TEXTBOX_H = 80

KB_Y0 = TEXTBOX_H + 4

KEY_W = 28
KEY_H = 28
KEY_SP = 3

CHARS_LINE = 38
LINE_H = 11

TEXT_Y0 = 18


# ============================================================
# KEYBOARD LAYOUTS
# ============================================================

_LAYOUTS = {
    "ABC": [
        "QWERTYUIOP",
        "ASDFGHJKL",
        "ZXCVBNM"
    ],

    "123": [
        "1234567890",
        "!@#$%^&*()",
        "+-=.,?;:_"
    ],
}

_MODE_ORDER = ["ABC", "123"]


# ============================================================
# VALUE HANDLER
# ============================================================

def _handle_val(val, text, mode_idx):
    """
    Returns:
        new text string  -> character added/deleted
        None             -> mode switch requested
        False            -> DONE
    """

    if val == "DONE":
        return False

    if val == "MODE":
        return None

    if val == "DEL":
        return text[:-1]

    if val == " ":
        return text + " "

    return text + val


# ============================================================
# BUILD KEYS
# ============================================================

def _build_keys(mode):
    keys = []

    rows = _LAYOUTS[mode]

    for r, row in enumerate(rows):
        n = len(row)

        total_w = n * KEY_W + (n - 1) * KEY_SP

        x0 = (320 - total_w) // 2
        y = KB_Y0 + r * (KEY_H + KEY_SP)

        for c, ch in enumerate(row):
            keys.append((
                x0 + c * (KEY_W + KEY_SP),
                y,
                KEY_W,
                KEY_H,
                ch,
                r,
                c
            ))

    # Control row
    br = len(rows)

    cy = KB_Y0 + br * (KEY_H + KEY_SP)

    keys.append((4,   cy, 58,  KEY_H, "MODE", br, 0))
    keys.append((66,  cy, 58,  KEY_H, "DEL",  br, 1))
    keys.append((128, cy, 72,  KEY_H, "DONE", br, 2))
    keys.append((204, cy, 112, KEY_H, " ",    br, 3))

    return keys


# ============================================================
# TEXT WRAP
# ============================================================

def _wrap_lines(text):
    """Split text into wrapped lines of CHARS_LINE width."""

    lines = []

    while len(text) > CHARS_LINE:
        lines.append(text[:CHARS_LINE])
        text = text[CHARS_LINE:]

    lines.append(text)

    return lines


# ============================================================
# DRAW TEXTBOX
# ============================================================

def _draw_textbox(disp, prompt, text):

    disp.fill_rectangle(
        0, 0, 320, TEXTBOX_H, BLACK
    )

    disp.draw_rectangle(
        2, 2, 316, TEXTBOX_H - 4, WHITE
    )

    # Prompt
    if prompt:
        p = prompt[:CHARS_LINE]

        if p:
            disp.draw_text8x8(
                8, 5, p, CYAN
            )

    # Text with wrapping
    lines = _wrap_lines(text) if text else [""]

    max_vis = (
        TEXTBOX_H - TEXT_Y0 - 4
    ) // LINE_H

    vis = lines[-max_vis:]

    for i, line in enumerate(vis):

        y = TEXT_Y0 + i * LINE_H

        # draw_text8x8 crashes on empty strings
        if line:
            disp.draw_text8x8(
                8, y, line, WHITE
            )

    # Cursor indicator
    last = vis[-1] if vis else ""

    cur_x = 8 + len(last) * 8
    cur_y = TEXT_Y0 + max(0, len(vis) - 1) * LINE_H

    if 8 <= cur_x < 316 and cur_y < TEXTBOX_H:
        disp.fill_rectangle(
            cur_x, cur_y, 4, 8, CYAN
        )


# ============================================================
# DRAW KEY
# ============================================================

def _draw_key(disp, k, highlighted=False):

    x, y, w, h, val, _, _ = k

    disp.fill_rectangle(
        x,
        y,
        w,
        h,
        RED if highlighted else KEY_BG
    )

    disp.draw_rectangle(
        x,
        y,
        w,
        h,
        WHITE
    )

    label = {
        "MODE": "MODE",
        "DEL": "DEL",
        "DONE": "DONE",
        " ": "SPC"
    }.get(val, val)

    lx = max(
        x,
        x + (w // 2) - len(label) * 4
    )

    ly = y + (h // 2) - 4

    if label:
        disp.draw_text8x8(
            lx, ly, label, WHITE
        )


# ============================================================
# DRAW KEYBOARD
# ============================================================

def _draw_keyboard(disp, keys, cidx):

    disp.fill_rectangle(
        0,
        TEXTBOX_H,
        320,
        240 - TEXTBOX_H,
        BLACK
    )

    for i, k in enumerate(keys):
        _draw_key(
            disp,
            k,
            highlighted=(i == cidx)
        )


# ============================================================
# MOVE CURSOR
# ============================================================

def _move_cursor(disp, keys, old_idx, new_idx):

    if old_idx == new_idx:
        return

    if old_idx is not None and 0 <= old_idx < len(keys):
        _draw_key(
            disp,
            keys[old_idx],
            highlighted=False
        )

    if new_idx is not None and 0 <= new_idx < len(keys):
        _draw_key(
            disp,
            keys[new_idx],
            highlighted=True
        )


# ============================================================
# CURSOR HELPERS
# ============================================================

def _cursor_index(keys, crow, ccol):

    row_keys = [
        (i, k)
        for i, k in enumerate(keys)
        if k[5] == crow
    ]

    if not row_keys:
        return 0

    ccol = min(
        ccol,
        len(row_keys) - 1
    )

    return row_keys[ccol][0]


def _hit_key(keys, tx, ty):

    for i, k in enumerate(keys):

        if (
            k[0] <= tx <= k[0] + k[2]
            and
            k[1] <= ty <= k[1] + k[3]
        ):
            return i

    return None


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def get_input(disp, prompt="", prefill=""):
    """
    Display keyboard.

    Returns:
        typed string when DONE is selected
        None if Back was pressed
    """

    gc.collect()

    mode_idx = 0
    mode = _MODE_ORDER[mode_idx]

    keys = _build_keys(mode)

    text = prefill

    crow = 0
    ccol = 0

    cidx = _cursor_index(
        keys,
        crow,
        ccol
    )

    _draw_textbox(
        disp,
        prompt,
        text
    )

    _draw_keyboard(
        disp,
        keys,
        cidx
    )

    hi_on_screen = cidx

    _touch_down = False

    _keypress_count = 0


    # ========================================================
    # MAIN LOOP
    # ========================================================

    while True:

        # ----------------------------------------------------
        # TOUCH
        # ----------------------------------------------------

        tp = _touch_pixel()

        if tp and not _touch_down:

            # Lock in this press using the position from the
            # first frame the finger was detected — ignore any
            # coordinate drift for the rest of the hold, so a
            # wobble near a key boundary can't fire the neighbor.
            _touch_down = True

            hi = _hit_key(
                keys,
                tp[0],
                tp[1]
            )

            if hi is not None:

                _draw_key(
                    disp,
                    keys[hi],
                    highlighted=True
                )

                time.sleep(0.07)

                val = keys[hi][4]

                changed = _handle_val(
                    val,
                    text,
                    mode_idx
                )

                # Sync button cursor
                crow = keys[hi][5]
                ccol = keys[hi][6]
                cidx = hi

                # DONE
                if changed is False:
                    gc.collect()
                    return text

                # MODE switch
                elif changed is None:

                    mode_idx = (
                        mode_idx + 1
                    ) % len(_MODE_ORDER)

                    mode = _MODE_ORDER[mode_idx]

                    keys = _build_keys(mode)

                    crow = 0
                    ccol = 0

                    cidx = _cursor_index(
                        keys,
                        crow,
                        ccol
                    )

                    _draw_keyboard(
                        disp,
                        keys,
                        cidx
                    )

                    hi_on_screen = cidx

                else:

                    text = changed

                    _keypress_count += 1

                    _move_cursor(
                        disp,
                        keys,
                        hi_on_screen,
                        cidx
                    )

                    hi_on_screen = cidx

                    _draw_textbox(
                        disp,
                        prompt,
                        text
                    )

        elif not tp:
            _touch_down = False


        # ----------------------------------------------------
        # PHYSICAL BUTTONS
        # ----------------------------------------------------

        btn = _btn()

        if btn:

            row_keys = [
                k for k in keys
                if k[5] == crow
            ]


            # RIGHT
            if btn == "RIGHT":

                ccol = (
                    ccol + 1
                ) % len(row_keys)

                new_cidx = _cursor_index(
                    keys,
                    crow,
                    ccol
                )

                _move_cursor(
                    disp,
                    keys,
                    hi_on_screen,
                    new_cidx
                )

                cidx = new_cidx
                hi_on_screen = cidx


            # LEFT
            elif btn == "LEFT":

                ccol = (
                    ccol - 1
                ) % len(row_keys)

                new_cidx = _cursor_index(
                    keys,
                    crow,
                    ccol
                )

                _move_cursor(
                    disp,
                    keys,
                    hi_on_screen,
                    new_cidx
                )

                cidx = new_cidx
                hi_on_screen = cidx


            # UP
            elif btn == "UP":

                rows = sorted(
                    set(
                        k[5]
                        for k in keys
                    )
                )

                ri = rows.index(crow)

                crow = rows[
                    (ri - 1) % len(rows)
                ]

                ccol = min(
                    ccol,
                    len([
                        k for k in keys
                        if k[5] == crow
                    ]) - 1
                )

                new_cidx = _cursor_index(
                    keys,
                    crow,
                    ccol
                )

                _move_cursor(
                    disp,
                    keys,
                    hi_on_screen,
                    new_cidx
                )

                cidx = new_cidx
                hi_on_screen = cidx


            # DOWN
            elif btn == "DOWN":

                rows = sorted(
                    set(
                        k[5]
                        for k in keys
                    )
                )

                ri = rows.index(crow)

                crow = rows[
                    (ri + 1) % len(rows)
                ]

                ccol = min(
                    ccol,
                    len([
                        k for k in keys
                        if k[5] == crow
                    ]) - 1
                )

                new_cidx = _cursor_index(
                    keys,
                    crow,
                    ccol
                )

                _move_cursor(
                    disp,
                    keys,
                    hi_on_screen,
                    new_cidx
                )

                cidx = new_cidx
                hi_on_screen = cidx


            # SELECT
            elif btn == "SELECT":

                val = keys[cidx][4]

                changed = _handle_val(
                    val,
                    text,
                    mode_idx
                )

                # DONE
                if changed is False:
                    gc.collect()
                    return text

                # MODE
                elif changed is None:

                    mode_idx = (
                        mode_idx + 1
                    ) % len(_MODE_ORDER)

                    mode = _MODE_ORDER[mode_idx]

                    keys = _build_keys(mode)

                    crow = 0
                    ccol = 0

                    cidx = _cursor_index(
                        keys,
                        crow,
                        ccol
                    )

                    _draw_keyboard(
                        disp,
                        keys,
                        cidx
                    )

                    hi_on_screen = cidx

                    continue

                else:

                    text = changed

                    _keypress_count += 1

                    _draw_textbox(
                        disp,
                        prompt,
                        text
                    )


            # BACK
            elif btn == "BACK":

                gc.collect()

                return None


        # ----------------------------------------------------
        # PERIODIC GARBAGE COLLECTION
        # ----------------------------------------------------

        if _keypress_count >= 20:

            gc.collect()

            _keypress_count = 0


        time.sleep(0.02)
