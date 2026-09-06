# apps/settings/main.py — settings app for Micromate
import os
import json
import time
import buttons
import wifi
from machine import Pin, PWM

SETTINGS_PATH  = "/system/settings.json"
_LOCATION_FILE = "/apps/weather/location.json"

# ===== OWN NON-BLOCKING BUTTON POLL =====
# button_module.py stays untouched - button_input() there is blocking
# (waits for release) which is right for the launcher/carousel, but
# wrong for hold-to-repeat here. Instead we read the same Pin objects
# it already exposes as module attributes and poll them ourselves.
_PINS = {
    "right":  buttons.right,
    "left":   buttons.left,
    "select": buttons.select,
    "alt":    buttons.alt_b,
    "down":   buttons.down,
    "up":     buttons.up,
}

def _raw_state():
    """Non-blocking snapshot: {"right": True/False, ...} - True means
    currently held down. No debounce of its own; the settings run()
    loop's tick rate (see TICK below) is coarse enough in practice."""
    return {name: (pin.value() == 0) for name, pin in _PINS.items()}

# ===== SCHEMA =====
ITEMS = [
    {"type": "slider", "key": "brightness", "label": "Brightness",
     "min": 0, "max": 100, "suffix": "%", "default": 100},
    {"type": "choice", "key": "ui",         "label": "Home UI",
     "options": ["carousel", "text", "grid"], "default": "carousel"},
    {"type": "toggle", "key": "show_labels", "label": "Show App Labels",
     "default": True},
    {"type": "slider", "key": "layout_scale", "label": "Icon Spacing",
     "min": 70, "max": 140, "suffix": "%", "default": 100},
    {"type": "choice", "key": "wallpaper",   "label": "Wallpaper",
     "options": ["black", "navy", "dark_teal", "charcoal",
                 "gradient_blue", "gradient_purple"], "default": "black"},
    {"type": "choice", "key": "clock_format", "label": "Clock Format",
     "options": ["24h", "12h"], "default": "24h"},
    {"type": "toggle", "key": "show_battery", "label": "Show Battery %",
     "default": True},
    {"type": "choice", "key": "wifi_display", "label": "WiFi Icon",
     "options": ["always", "on_change"], "default": "always"},
    {"type": "action", "key": "wifi",        "label": "Wi-Fi Networks",
     "hint": "Select=Open"},
    {"type": "action", "key": "location",   "label": "Weather Location",
     "hint": "Select=Open"},
    {"type": "action", "key": "save",        "label": "Save & Exit",
     "hint": "Select=Save"},
]

# ===== COLOURS =====
BG     = 0x0000
WHITE  = 0xFFFF
RED    = 0xF800
GREEN  = 0x07E0
GREY   = 0x8410
CYAN   = 0x07FF
SELECT = 0x07FF
DIM    = 0x4208

# ===== LAYOUT =====
ITEM_START_Y     = 45
ITEM_HEIGHT      = 55
MAX_VISIBLE      = 3
SLIDER_X         = 60
SLIDER_W         = 200
SLIDER_H         = 10
KNOB_W           = 12
KNOB_H           = 22

# ===== HOLD-TO-REPEAT TUNING (units: loop ticks, see TICK below) =====
TICK               = 0.03   # main loop poll interval, seconds
REPEAT_START_TICKS = 12     # ~360ms held before auto-repeat kicks in
REPEAT_MED_TICKS   = 40     # ~1.2s held before repeat speeds up
REPEAT_FAST_TICKS  = 80     # ~2.4s held before fastest repeat

# ===== TYPE SAFETY =====
def validate_value(item, value):
    if item["type"] == "slider":
        try:   value = int(value)
        except: return item["default"]
        return max(item["min"], min(item["max"], value))
    elif item["type"] == "toggle":
        if isinstance(value, bool): return value
        if isinstance(value, int):  return bool(value)
        return item["default"]
    elif item["type"] == "choice":
        opts = item.get("options", [])
        return value if value in opts else item["default"]
    return value

# ===== LOAD / SAVE =====
def load_settings():
    defaults = {item["key"]: item.get("default") for item in ITEMS}
    try:
        with open(SETTINGS_PATH, "r") as f:
            raw = json.load(f)
            for item in ITEMS:
                key = item["key"]
                if key in raw and item["type"] != "action":
                    defaults[key] = validate_value(item, raw[key])
    except: pass
    return defaults

def save_settings(settings):
    try:
        if "system" not in os.listdir("/"):
            os.mkdir("/system")
    except: pass
    to_save = {k: v for k, v in settings.items()
               if any(i["key"] == k and i["type"] != "action" for i in ITEMS)}
    with open(SETTINGS_PATH, "w") as f:
        json.dump(to_save, f)

# ===== BACKLIGHT =====
_pwm = None
def make_backlight():
    global _pwm
    try:
        _pwm = PWM(Pin(22))
        _pwm.freq(1000)
    except: _pwm = None

make_backlight()

def apply_brightness(value):
    if _pwm:
        _pwm.duty_u16(int((max(0, min(100, value)) / 100) * 65535))
    else:
        try: Pin(22, Pin.OUT).value(1 if value > 0 else 0)
        except: pass

# ===== SAVED LOCATION HELPERS =====

def _load_saved_location():
    try:
        with open(_LOCATION_FILE, "r") as f:
            return json.load(f)
    except:
        return None

def _save_location(city, cc, lat, lon):
    try:
        # Make sure /apps/weather/ exists
        parts = _LOCATION_FILE.rsplit("/", 1)
        if len(parts) == 2:
            folder = parts[0]
            # Try to create each directory level
            path = ""
            for part in folder.split("/"):
                if not part:
                    continue
                path += "/" + part
                try:
                    os.mkdir(path)
                except:
                    pass
        with open(_LOCATION_FILE, "w") as f:
            json.dump({"city": city, "cc": cc, "lat": lat, "lon": lon, "manual": True}, f)
        return True
    except Exception as e:
        print("_save_location error:", e)
        return False

def _clear_saved_location():
    try:
        os.remove(_LOCATION_FILE)
    except:
        pass

# ===== DRAWING =====
def draw_header(disp):
    disp.fill_rectangle(0, 0, 320, 240, BG)
    disp.fill_rectangle(0, 0, 320, 30, 0x2104)
    disp.draw_text8x8(100, 11, "Settings", WHITE)

def draw_scroll_indicator(disp, scroll_offset, total):
    bar_area_h = MAX_VISIBLE * ITEM_HEIGHT
    disp.fill_rectangle(315, ITEM_START_Y, 5, bar_area_h, DIM)
    if total > MAX_VISIBLE:
        bar_h = max(6, (MAX_VISIBLE * bar_area_h) // total)
        bar_y = ITEM_START_Y + (scroll_offset * bar_area_h) // total
        disp.fill_rectangle(315, bar_y, 5, bar_h, WHITE)

def draw_visible_items(disp, values, selected, scroll_offset):
    disp.fill_rectangle(0, ITEM_START_Y, 315, MAX_VISIBLE * ITEM_HEIGHT, BG)
    for slot in range(MAX_VISIBLE):
        idx = scroll_offset + slot
        if idx >= len(ITEMS): break
        draw_item(disp, slot, ITEMS[idx], values.get(ITEMS[idx]["key"]), idx == selected)
    draw_scroll_indicator(disp, scroll_offset, len(ITEMS))

def draw_item(disp, slot, item, value, selected):
    y      = ITEM_START_Y + slot * ITEM_HEIGHT
    lc     = SELECT if selected else WHITE
    row_bg = 0x1082 if selected else BG
    # Single clear at the row's real background - no separate
    # black-then-highlight pass, so nothing downstream can punch a
    # black patch back into a highlighted row.
    disp.fill_rectangle(0, y, 315, ITEM_HEIGHT - 2, row_bg)
    disp.draw_text8x8(10, y + 4, item["label"], lc)
    if item["type"] == "slider":
        _draw_slider(disp, item, value, y + 24, row_bg)
    elif item["type"] == "toggle":
        _draw_toggle(disp, value, y + 24, row_bg)
    elif item["type"] == "choice":
        _draw_choice(disp, item, value, y + 24, row_bg)
    elif item["type"] == "action":
        _draw_action(disp, item, selected, y + 24, item["key"], row_bg)

def _draw_slider(disp, item, value, y, row_bg):
    if value is None: value = item["default"]
    pct   = (value - item["min"]) / (item["max"] - item["min"])
    kx    = SLIDER_X + int(pct * SLIDER_W)
    ky    = y - KNOB_H // 2 + SLIDER_H // 2
    disp.fill_rectangle(SLIDER_X, y, SLIDER_W, SLIDER_H, WHITE)
    disp.fill_rectangle(kx - KNOB_W // 2, ky, KNOB_W, KNOB_H, RED)
    disp.fill_rectangle(262, y - 4, 58, 18, row_bg)
    label = str(value) + item.get("suffix", "")
    disp.draw_text8x8(265, y, label, WHITE)

def _draw_toggle(disp, value, y, row_bg):
    disp.fill_rectangle(SLIDER_X, y - 2, 80, 16, row_bg)
    label = "[ ON  ]" if value else "[ OFF ]"
    color = GREEN if value else RED
    disp.draw_text8x8(SLIDER_X, y, label, color)

def _draw_choice(disp, item, value, y, row_bg):
    # Shows just the current option with < > arrows plus an index
    # (e.g. "3/6") instead of listing every option in a row - a long
    # options list (like Wallpaper's 6 presets) used to run straight
    # off the right edge of the screen with no way to see the rest.
    # This layout always fits regardless of option count.
    opts = item.get("options", [])
    idx  = opts.index(value) if value in opts else 0
    disp.fill_rectangle(SLIDER_X, y - 2, 250, 18, row_bg)
    label = opts[idx].replace("_", " ")[:18] if opts else "?"
    disp.draw_text8x8(SLIDER_X, y, "<", CYAN)
    disp.draw_text8x8(SLIDER_X + 16, y, label, WHITE)
    disp.draw_text8x8(SLIDER_X + 16 + len(label) * 8 + 8, y, ">", CYAN)
    if opts:
        idx_label = str(idx + 1) + "/" + str(len(opts))
        disp.draw_text8x8(270, y, idx_label, GREY)

def _draw_action(disp, item, selected, y, key, row_bg):
    disp.fill_rectangle(SLIDER_X, y - 2, 250, 16, row_bg)
    color = CYAN if selected else GREY

    # For location: show current city name as the sub-label
    if key == "location":
        saved = _load_saved_location()
        if saved and saved.get("manual"):
            city = saved.get("city", "?")
            cc   = saved.get("cc", "")
            sub  = (city + ", " + cc)[:28]
        else:
            sub = "Auto-detect"
        disp.draw_text8x8(SLIDER_X, y, sub, color)
    else:
        hint = item.get("hint", "Select=Open")
        disp.draw_text8x8(SLIDER_X, y, hint, color)

# ===== VALUE LOGIC =====
def adjust_value(item, value, direction):
    if item["type"] == "slider":
        step = 1
        return max(item["min"], min(item["max"], value + direction * step))
    elif item["type"] == "toggle":
        return direction > 0
    elif item["type"] == "choice":
        opts = item.get("options", [])
        idx  = opts.index(value) if value in opts else 0
        return opts[(idx + direction) % len(opts)]
    return value

def on_value_changed(key, value):
    if key == "brightness":
        apply_brightness(value)

# ===== LOCATION SCREENS =====
# (unchanged - still uses buttons.button_input(), which is fine here:
# it's a modal sub-screen that only cares about discrete single presses,
# not hold-to-repeat.)

import urequests
import gc

API_KEY = "72f548955dac66aa7602503ca161cf4f"

def _geocode(query):
    try:
        url = ("http://api.openweathermap.org/geo/1.0/direct"
               "?q=" + query +
               "&limit=5&appid=" + API_KEY)
        r   = urequests.get(url)
        raw = r.json()
        r.close()
        del r
        gc.collect()
        out = []
        for item in raw:
            name  = item.get("name", "")
            cc    = item.get("country", "")
            state = item.get("state", "")
            lat   = item.get("lat", 0.0)
            lon   = item.get("lon", 0.0)
            display = (name + ", " + state + ", " + cc) if state else (name + ", " + cc)
            out.append({"name": name, "cc": cc, "lat": lat, "lon": lon, "display": display})
        return out
    except Exception as e:
        print("geocode error:", e)
        return []

def _draw_loc_main(disp, saved):
    disp.fill_rectangle(0, 0, 320, 240, BG)
    disp.fill_rectangle(0, 0, 320, 30, 0x2104)
    disp.draw_text8x8(80, 11, "Weather Location", WHITE)
    disp.draw_text8x8(10, 40, "Current:", GREY)
    if saved and saved.get("manual"):
        city = saved.get("city", "Unknown")
        cc   = saved.get("cc", "")
        lat  = saved.get("lat", 0)
        lon  = saved.get("lon", 0)
        disp.draw_text8x8(10, 58, (city + ", " + cc)[:36], WHITE)
        disp.draw_text8x8(10, 74, (str(round(lat, 2)) + ", " + str(round(lon, 2)))[:36], GREY)
        disp.draw_text8x8(10, 90, "(manual)", GREY)
    else:
        disp.draw_text8x8(10, 58, "Auto-detect", WHITE)
        disp.draw_text8x8(10, 74, "(IP geolocation)", GREY)
    disp.fill_rectangle(0, 110, 320, 2, GREY)
    disp.draw_text8x8(10, 120, "Select: Search for a city", WHITE)
    disp.draw_text8x8(10, 148, "Alt: Use auto-detect", WHITE)
    disp.draw_text8x8(0,  229, "Down=Back to Settings", GREY)

def _draw_loc_results(disp, results, sel):
    disp.fill_rectangle(0, 0, 320, 240, BG)
    disp.fill_rectangle(0, 0, 320, 30, 0x2104)
    disp.draw_text8x8(100, 11, "Select City", WHITE)
    if not results:
        disp.draw_text8x8(10, 80,  "No results found.", RED)
        disp.draw_text8x8(10, 100, "Try a different name.", GREY)
        disp.draw_text8x8(0,  229, "Alt=Back", GREY)
        return
    for i, r in enumerate(results):
        y = 35 + i * 38
        if i == sel:
            disp.fill_rectangle(0, y - 2, 320, 36, 0x1082)
        c = CYAN if i == sel else WHITE
        d = r["display"]
        if len(d) > 37: d = d[:34] + "..."
        disp.draw_text8x8(8, y,      d, c)
        coord = str(round(r["lat"], 2)) + ",  " + str(round(r["lon"], 2))
        disp.draw_text8x8(8, y + 18, coord, GREY)
    disp.draw_text8x8(0, 229, "Alt=Back  Up/Dn=Move  Select=Confirm", GREY)

def _location_flow(disp):
    """
    Full location sub-screen.
    Returns True if a change was made (city saved or auto-detect cleared), False if cancelled.
    Uses the blocking buttons.button_input() throughout - this is a
    modal picker, not a value slider, so single discrete presses are
    exactly what's wanted here.
    """
    saved = _load_saved_location()
    _draw_loc_main(disp, saved)

    while True:
        btn = buttons.button_input()
        time.sleep(0.02)

        if btn == "down":
            return False

        elif btn == "select":
            # Search flow
            try:
                import keyboard
            except ImportError:
                disp.fill_rectangle(0, 100, 320, 30, BG)
                disp.draw_text8x8(10, 110, "keyboard.py missing!", RED)
                time.sleep(2)
                _draw_loc_main(disp, saved)
                continue

            query = keyboard.get_input(disp, prompt="Search city:")
            if not query or not query.strip():
                _draw_loc_main(disp, saved)
                continue

            query = query.strip()
            disp.fill_rectangle(0, 0, 320, 240, BG)
            disp.draw_text8x8(10, 112, "Searching: " + query[:18] + "...", CYAN)
            gc.collect()

            results = _geocode(query)
            gc.collect()

            sel = 0
            _draw_loc_results(disp, results, sel)

            if not results:
                while True:
                    if buttons.button_input() == "alt":
                        break
                    time.sleep(0.02)
                _draw_loc_main(disp, saved)
                continue

            # Results picker
            confirmed = False
            while True:
                b = buttons.button_input()
                time.sleep(0.02)

                if b == "alt":
                    break  # back to location main

                elif b == "up":
                    sel = (sel - 1) % len(results)
                    _draw_loc_results(disp, results, sel)

                elif b == "down":
                    sel = (sel + 1) % len(results)
                    _draw_loc_results(disp, results, sel)

                elif b == "select":
                    r = results[sel]
                    _save_location(r["name"], r["cc"], r["lat"], r["lon"])
                    # Confirmation flash
                    disp.fill_rectangle(0, 0, 320, 240, BG)
                    disp.draw_text8x8(10, 80,  "Saved!", GREEN)
                    disp.draw_text8x8(10, 100, (r["name"] + ", " + r["cc"])[:36], WHITE)
                    time.sleep(1)
                    confirmed = True
                    break

            if confirmed:
                return True

            saved = _load_saved_location()
            _draw_loc_main(disp, saved)

        elif btn == "alt":
            # Auto-detect
            _clear_saved_location()
            disp.fill_rectangle(0, 0, 320, 240, BG)
            disp.draw_text8x8(10, 112, "Auto-detect enabled.", GREEN)
            time.sleep(1)
            return True

# ===== ACTION HANDLER =====
def fire_action(disp, key, values, settings):
    if key == "wifi":
        wifi.manual_mode(disp)
    elif key == "location":
        _location_flow(disp)
        # Redraw settings after returning
        return False   # don't exit settings, just redraw
    elif key == "save":
        settings.update({k: v for k, v in values.items() if v is not None})
        save_settings(settings)
        disp.fill_rectangle(80, 100, 160, 40, 0x07E0)
        disp.draw_text8x8(100, 116, "Saved!", BG)
        time.sleep(1)
        return True
    return False

def clamp_scroll(selected, scroll_offset):
    if selected < scroll_offset:
        return selected
    if selected >= scroll_offset + MAX_VISIBLE:
        return selected - MAX_VISIBLE + 1
    return scroll_offset

def _repeat_interval(ticks):
    """How many ticks between auto-repeat fires, given how long a
    button has been continuously held. Ramps up (fires more often)
    the longer you hold left/right."""
    if ticks >= REPEAT_FAST_TICKS: return 1
    if ticks >= REPEAT_MED_TICKS:  return 2
    return 4

def _move_selection(disp, values, prev_selected, new_selected, scroll_offset):
    """Redraw just the two rows that changed (old selection, new
    selection) instead of clearing/repainting the whole list - this is
    what actually removes the flash/flicker on every up/down press."""
    new_scroll = clamp_scroll(new_selected, scroll_offset)
    if new_scroll != scroll_offset:
        # Window had to shift - only case that still needs a full redraw.
        draw_visible_items(disp, values, new_selected, new_scroll)
        return new_scroll

    prev_item = ITEMS[prev_selected]
    new_item  = ITEMS[new_selected]
    draw_item(disp, prev_selected - scroll_offset, prev_item,
              values.get(prev_item["key"]), False)
    draw_item(disp, new_selected - scroll_offset, new_item,
              values.get(new_item["key"]), True)
    return scroll_offset

# ===== MAIN =====
def run(disp):
    settings      = load_settings()
    values        = {item["key"]: settings.get(item["key"]) for item in ITEMS}
    selected      = 0
    scroll_offset = 0

    apply_brightness(values.get("brightness", 100))
    draw_header(disp)
    draw_visible_items(disp, values, selected, scroll_offset)

    prev_state = {"right": False, "left": False, "select": False,
                  "alt": False, "down": False, "up": False}
    hold_ticks = {"left": 0, "right": 0}

    while True:
        state = _raw_state()

        def just_pressed(name):
            return state[name] and not prev_state[name]

        item       = ITEMS[selected]
        key        = item["key"]
        adjustable = item["type"] in ("slider", "toggle", "choice")

        # ----- value adjust, left/right, with hold-to-repeat -----
        if adjustable:
            fired = 0  # -1, 0, or +1

            if just_pressed("left"):
                fired = 1
                hold_ticks["left"] = 0
            elif state["left"] and prev_state["left"]:
                hold_ticks["left"] += 1
                t = hold_ticks["left"] - REPEAT_START_TICKS
                if t >= 0 and t % _repeat_interval(hold_ticks["left"]) == 0:
                    fired = 1
            else:
                hold_ticks["left"] = 0

            if fired == 0:
                if just_pressed("right"):
                    fired = -1
                    hold_ticks["right"] = 0
                elif state["right"] and prev_state["right"]:
                    hold_ticks["right"] += 1
                    t = hold_ticks["right"] - REPEAT_START_TICKS
                    if t >= 0 and t % _repeat_interval(hold_ticks["right"]) == 0:
                        fired = -1
                else:
                    hold_ticks["right"] = 0
            else:
                # left owns this tick - keep right's counter honest
                if not state["right"]:
                    hold_ticks["right"] = 0

            if fired != 0:
                values[key] = adjust_value(item, values[key], fired)
                on_value_changed(key, values[key])
                draw_item(disp, selected - scroll_offset, item, values[key], True)
        else:
            hold_ticks["left"]  = 0
            hold_ticks["right"] = 0

        # ----- navigation / actions - single press only, no repeat -----
        if just_pressed("down"):
            prev_selected = selected
            selected      = (selected + 1) % len(ITEMS)
            scroll_offset = _move_selection(disp, values, prev_selected, selected, scroll_offset)

        elif just_pressed("up"):
            prev_selected = selected
            selected      = (selected - 1) % len(ITEMS)
            scroll_offset = _move_selection(disp, values, prev_selected, selected, scroll_offset)

        elif just_pressed("select"):
            if item["type"] == "action":
                if fire_action(disp, key, values, settings):
                    return
                draw_header(disp)
                draw_visible_items(disp, values, selected, scroll_offset)

        elif just_pressed("alt"):
            return  # cancel, no save

        prev_state = state
        time.sleep(TICK)
