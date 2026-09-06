import gc
import time
import math
import json
from machine import Pin, PWM
from sprite import Scene

piezo = PWM(Pin(11))
piezo.freq(1000)
piezo.duty_u16(0)

SETTINGS_PATH = "/system/settings.json"

# ("solid", color) or ("gradient", top_color, bottom_color) — RGB565
WALLPAPERS = {
    "black":           ("solid", 0x0000),
    "navy":            ("solid", 0x000C),
    "dark_teal":       ("solid", 0x0421),
    "charcoal":        ("solid", 0x2104),
    "gradient_blue":   ("gradient", 0x0000, 0x041F),
    "gradient_purple": ("gradient", 0x1000, 0x780F),
}
DEFAULT_WALLPAPER_KEY = "black"


def _load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            return json.load(f)
    except:
        return {}


def _lerp565(c1, c2, t):
    r1, g1, b1 = (c1 >> 11) & 0x1F, (c1 >> 5) & 0x3F, c1 & 0x1F
    r2, g2, b2 = (c2 >> 11) & 0x1F, (c2 >> 5) & 0x3F, c2 & 0x1F
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return (r << 11) | (g << 5) | b


class Carousel:
    CENTER_X = 160
    CENTER_Y = 120
    BASE_SPACING = 100
    # Number of intermediate frames drawn during a scroll transition
    # (not counting the final resting frame) - lower = faster/snappier
    # scroll, higher = smoother motion. Total scroll wall-time is
    # roughly (ANIM_STEPS+1) x per-frame render cost, so this is the
    # most direct knob for scroll speed. Tune to taste.
    ANIM_STEPS = 4
    ICON_SIZE = 32
    SLOT_OFFSETS = [-2, -1, 0, 1, 2]
    # How many horizontal strips to draw for a gradient wallpaper.
    # More = smoother fade, more fill_rectangle calls per full_clear.
    GRADIENT_BANDS = 24

    def __init__(self, ctx):
        self.disp             = ctx["disp"]
        self.list_apps         = ctx["list_apps"]
        self.launch_app        = ctx["launch_app"]
        self.draw_status_bar   = ctx["draw_status_bar"]
        self.update_clock      = ctx["update_clock"]
        self.draw_wifi_status  = ctx["draw_wifi_status"]
        STATUS_H               = ctx["STATUS_H"]
        self.BG                = ctx["BG"]
        self.TEXT_COLOR        = ctx["TEXT_COLOR"]
        self.ACCENT            = ctx["ACCENT"]
        self.DIM                = ctx["DIM"]

        # Dock hooks - optional via .get() so this still runs even if
        # main.py's ctx doesn't have them (older boot script version)
        self.draw_dock_fn  = ctx.get("draw_dock")
        self.dock_reset_fn = ctx.get("dock_reset")
        self.DOCK_Y        = ctx.get("DOCK_Y", 240)  # falls back to "no dock" if absent

        self.DRAW_Y   = STATUS_H + 2
        # Stop the carousel's own clears above the dock bar so full_clear
        # doesn't paint over it every redraw.
        self.DRAW_H   = self.DOCK_Y - self.DRAW_Y
        self.ICON_Y   = self.CENTER_Y - 16
        self.BORDER_T = self.CENTER_Y - 40
        self.TEXT_Y   = self.CENTER_Y + 44
        self.TEXT_H   = 20
        self.ANIM_Y   = self.BORDER_T - 2
        ICON_W_H      = 4
        self.ANIM_H   = (self.ICON_Y + 32 + ICON_W_H) - self.ANIM_Y
        self.ICON_LOCAL_Y = self.ICON_Y - self.ANIM_Y

        self.apps = []
        self.selected = 0

        # Customization state - populated by apply_settings() below,
        # which is called before the Scene is built so its background
        # color reflects the wallpaper straight away.
        self.SPACING       = self.BASE_SPACING
        self.show_labels   = True
        self.wallpaper_key = DEFAULT_WALLPAPER_KEY
        self.wallpaper     = WALLPAPERS[DEFAULT_WALLPAPER_KEY]
        self._scene_bg     = self.BG
        self._last_scene_bg = None

        self.apply_settings(ctx.get("settings", {}) or {})

        gc.collect()
        print("ANIM_H =", self.ANIM_H,
              "| band buf = 320 *", self.ANIM_H, "* 2 =", 320 * self.ANIM_H * 2, "bytes")
        print("Free heap before Scene:", gc.mem_free())
        # band_height == ANIM_H: this whole strip is one band, so every
        # icon (32px tall, fixed y - only x ever moves during scroll)
        # is guaranteed to land in a single disp.block() call every
        # frame instead of straddling multiple bands. That's what was
        # causing icons to visibly tear into 3 pieces (top/middle/
        # bottom, moving at different apparent speeds) while scrolling.
        # Slot handles are created with sprite=None and repointed later
        # via update_slot_sprites() (h.sprite = app.icon), which
        # bypasses add_sprite()'s automatic whole-piece band growth -
        # so this has to be set explicitly here rather than relying on
        # that. At ~320*ANIM_H*2 bytes this is trivial on this board's
        # free heap either way.
        self._build_scene()

    # ----- customization -----

    def apply_settings(self, settings):
        """Pull the customizable bits out of the settings dict. Safe to
        call again after the first render (e.g. after returning from the
        Settings app) - spacing/labels apply on the next draw for free,
        wallpaper changes trigger a Scene rebuild if the icon-band color
        actually changed."""
        self.show_labels = bool(settings.get("show_labels", True))

        try:
            scale = int(settings.get("layout_scale", 100))
        except (TypeError, ValueError):
            scale = 100
        scale = max(70, min(140, scale))
        self.SPACING = int(round(self.BASE_SPACING * (scale / 100.0)))

        key = settings.get("wallpaper", DEFAULT_WALLPAPER_KEY)
        if key not in WALLPAPERS:
            key = DEFAULT_WALLPAPER_KEY
        self.wallpaper_key = key
        self.wallpaper = WALLPAPERS[key]

        # Scene only draws a flat color behind the icon strip, so for a
        # gradient we sample the color at the strip's vertical middle -
        # it blends into the gradient above/below it instead of a hard
        # solid block, but it won't be a true gradient *through* the
        # icons themselves (that would need a change to sprite.py's
        # Scene class, which isn't available here).
        self._scene_bg = self._wallpaper_color_at(self.ANIM_Y + self.ANIM_H // 2)

    def reload_settings(self):
        """Re-read settings.json and re-apply. Called at the top of
        render_home() so changes made in the Settings app show up as
        soon as you back out, without needing a reboot."""
        self.apply_settings(_load_settings())
        if self._scene_bg != self._last_scene_bg:
            self._build_scene()

    def _build_scene(self):
        self.scene = Scene(self.disp, band_height=self.ANIM_H,
                            screen_width=320, screen_height=self.ANIM_H,
                            background_color=self._scene_bg,
                            screen_y_offset=self.ANIM_Y,
                            invert_colors=False)
        self.slot_handles = [
            self.scene.add_sprite(None, 0, self.ICON_LOCAL_Y, visible=False)
            for _ in self.SLOT_OFFSETS
        ]
        self._last_scene_bg = self._scene_bg
        if self.apps:
            self.update_slot_sprites()

    def _wallpaper_color_at(self, y):
        kind = self.wallpaper[0]
        if kind == "solid":
            return self.wallpaper[1]
        _, top, bottom = self.wallpaper
        t = (y - self.DRAW_Y) / float(max(1, self.DRAW_H))
        t = max(0.0, min(1.0, t))
        return _lerp565(top, bottom, t)

    def draw_wallpaper(self):
        """Paint the full carousel drawing area (excludes status bar and
        dock) with the current wallpaper - solid fill, or banded
        gradient approximation."""
        disp = self.disp
        kind = self.wallpaper[0]
        try:
            if kind == "solid":
                disp.fill_rectangle(0, self.DRAW_Y, 320, self.DRAW_H, self.wallpaper[1])
                return
            _, top, bottom = self.wallpaper
            bands = self.GRADIENT_BANDS
            band_h = max(1, self.DRAW_H // bands)
            y = self.DRAW_Y
            drawn = 0
            for i in range(bands):
                t = i / float(bands - 1) if bands > 1 else 0.0
                color = _lerp565(top, bottom, t)
                h = band_h if i < bands - 1 else (self.DRAW_H - drawn)
                if h <= 0:
                    break
                disp.fill_rectangle(0, y, 320, h, color)
                y += h
                drawn += h
        except:
            pass

    # ----- rendering -----

    def update_slot_sprites(self):
        """Point each of the 5 fixed slot handles at the correct app's
        icon Sprite for the current self.selected index. Call this
        whenever selected changes or the app list is (re)loaded."""
        if not self.apps:
            for h in self.slot_handles:
                h.visible = False
            return
        n = len(self.apps)
        for slot_i, i in enumerate(self.SLOT_OFFSETS):
            idx = (self.selected + i) % n
            app = self.apps[idx]
            h = self.slot_handles[slot_i]
            if app.icon:
                h.sprite = app.icon
                h.visible = True
            else:
                h.visible = False

    def position_slots(self, offset):
        for slot_i, i in enumerate(self.SLOT_OFFSETS):
            x = self.CENTER_X + i * self.SPACING + offset
            self.slot_handles[slot_i].x = x - 16  # 32-wide icon, centered

    def draw_labels_and_border(self, offset, full_clear=False):
        disp = self.disp
        if full_clear:
            self.draw_wallpaper()
        else:
            try:
                disp.fill_rectangle(0, self.TEXT_Y, 320, self.TEXT_H,
                                     self._wallpaper_color_at(self.TEXT_Y))
            except: pass

        for i in range(-2, 3):
            if not self.apps: break
            idx = (self.selected + i) % len(self.apps)
            app = self.apps[idx]
            x   = self.CENTER_X + i * self.SPACING + offset
            if x < -64 or x > 384: continue
            if i == 0 and offset == 0:
                try: disp.draw_rectangle(x-40, self.BORDER_T, 80, 80, self.ACCENT)
                except: pass
            if not self.show_labels:
                continue
            tc = self.TEXT_COLOR if (i == 0 and offset == 0) else self.DIM
            try:
                nc = app.name[:16]
                tx = max(0, min(312, x - len(nc)*4))
                disp.draw_text8x8(int(tx), self.TEXT_Y+4, nc, tc)
            except: pass

    def draw_dock(self, force=False):
        """Push the current selected app's name to the dock. force=True
        also resets the dock's internal dirty-check first, needed after
        anything (full_clear, returning from an app) may have wiped the
        dock area on screen without dock.py knowing about it."""
        if not self.apps or self.draw_dock_fn is None:
            return
        if force and self.dock_reset_fn is not None:
            self.dock_reset_fn()
        try:
            self.draw_dock_fn(self.apps[self.selected].name)
        except:
            pass

    def draw_frame(self, offset, full_clear=False):
        self.position_slots(offset)
        self.scene.render()
        self.draw_labels_and_border(offset, full_clear=full_clear)
        if full_clear:
            # full_clear just wiped the dock's row too - force it back
            self.draw_dock(force=True)

    def animate_scroll(self, direction):
        if not self.apps or len(self.apps) <= 1:
            return
        gc.collect()
        try:
            self.disp.fill_rectangle(0, self.TEXT_Y, 320, self.TEXT_H,
                                      self._wallpaper_color_at(self.TEXT_Y))
        except: pass
        dist = self.SPACING * direction
        for s in range(self.ANIM_STEPS + 1):
            t     = s / self.ANIM_STEPS
            eased = int(round((0.5 - 0.5 * math.cos(math.pi * t)) * dist))
            self.position_slots(eased)
            self.scene.render()

    def render_home(self):
        gc.collect()
        self.reload_settings()
        self.apps = self.list_apps()
        if not self.apps:
            try:
                self.draw_wallpaper()
                self.disp.draw_text8x8(88, self.CENTER_Y, "No apps found",
                                        self.TEXT_COLOR)
            except: pass
            return
        self.selected %= len(self.apps)
        self.update_slot_sprites()
        self.draw_status_bar()
        self.draw_frame(0, full_clear=True)  # draw_frame handles dock via force=True

    def handle_button(self, btn):
        if not self.apps or btn == 0:
            return False

        gc.collect()
        if btn == "right":
            beep(1000)
            self.animate_scroll(1)
            self.selected = (self.selected - 1) % len(self.apps)
            self.update_slot_sprites()
            self.draw_frame(0)
            self.draw_dock()
            
            gc.collect()
        elif btn == "left":
            beep(1000)
            self.animate_scroll(-1)
            self.selected = (self.selected + 1) % len(self.apps)
            self.update_slot_sprites()
            self.draw_frame(0)
            self.draw_dock()
            gc.collect()
            
        elif btn == "select":
            beep(600)
            self.launch_app(self.apps[self.selected])
            import json
            try:
                with open("/system/settings.json") as f:
                    new = json.load(f)
                if new.get("ui", "carousel") != "carousel":
                    return True  # exit to dispatcher
            except: pass
            self.render_home()  # full_clear path re-forces the dock too
            
        return False


def run(ctx):
    import buttons
    import network

    carousel = Carousel(ctx)
    carousel.render_home()

    while True:
        carousel.update_clock()
        try:
            carousel.draw_wifi_status(network.WLAN(network.STA_IF).isconnected())
        except: pass

        btn = buttons.button_input()
        if btn:
            print("raw button_input() returned:", btn)
        should_exit = carousel.handle_button(btn)
        if should_exit:
            return

        gc.collect()
        time.sleep(0.01)
        
def beep(freq):
    piezo.freq(freq)
    piezo.duty_u16(30000)
    time.sleep(0.1)
    piezo.duty_u16(0)
