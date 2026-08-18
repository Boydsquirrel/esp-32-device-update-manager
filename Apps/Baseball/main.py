"""
apps/baseball/main.py - Full 9-inning baseball game for MicroMate

Controls (new button_module.py):
    left   = cycle option left
    right  = cycle option right
    select = confirm / swing
    alt    = pause menu (resume / quit to launcher)

You control BOTH sides:
  - When your team bats, you time a swing against a moving meter.
  - When your team is in the field, you pick a pitch zone against the CPU batter.

Simple-shapes rendering for now (rects/lines/text only) - drop-in ready to
swap to sprite.py/.spr assets later without touching game logic, since all
drawing goes through the small draw_* helpers at the top.
"""

import buttons
import utime
import random
import gc

# ===== COLORS =====
BG      = 0x0000
TEXT    = 0xFFFF
ACCENT  = 0x07FF
DIM     = 0x8410
GREEN   = 0x07E0
RED     = 0xF800
YELLOW  = 0xFFE0
ORANGE  = 0xFD20
BLUE    = 0x001F

SW, SH = 320, 240

TEAM_NAMES = ("AWAY", "HOME")


# ===== LOW LEVEL DRAW HELPERS =====
# All screen writes go through these so the rendering approach (raw
# shapes now, .spr sprites later) is swappable in one place.

def clear_region(disp, x, y, w, h):
    disp.fill_rectangle(x, y, w, h, BG)


def draw_text(disp, x, y, s, color=TEXT):
    if not s:
        # ili9341 driver's draw_text8x8 raises ValueError on an empty
        # string - nothing to draw anyway (clear_region already wiped
        # the area), so just skip it.
        return
    disp.draw_text8x8(x, y, s, color)


def draw_text_centered(disp, cx, y, s, color=TEXT):
    x = cx - (len(s) * 4)
    if x < 0:
        x = 0
    draw_text(disp, x, y, s, color)


def fill_circle(disp, cx, cy, r, color):
    # No circle primitive on the display driver - approximate with
    # horizontal fill_rectangle scanlines. Cheap enough at these radii
    # (ball/base markers are small, only called a handful of times).
    for dy in range(-r, r + 1):
        span = int((r * r - dy * dy) ** 0.5)
        if span <= 0:
            continue
        disp.fill_rectangle(cx - span, cy + dy, span * 2 + 1, 1, color)


# ===== LAYOUT =====
STATUS_Y   = 4
SCORE_Y    = 20
DIAMOND_CX = 160
DIAMOND_CY = 100
DIAMOND_R  = 46
COUNT_Y    = 150
MATCHUP_Y  = 168
INTERACT_Y = 195
HINT_Y     = 228

BASE_SIZE = 10


def base_positions():
    # (x, y) of the CENTER of first/second/third base markers, and the
    # home-plate point, going around the diamond.
    home   = (DIAMOND_CX, DIAMOND_CY + DIAMOND_R)
    first  = (DIAMOND_CX + DIAMOND_R, DIAMOND_CY)
    second = (DIAMOND_CX, DIAMOND_CY - DIAMOND_R)
    third  = (DIAMOND_CX - DIAMOND_R, DIAMOND_CY)
    return home, first, second, third


def draw_diamond(disp, bases_occupied):
    # bases_occupied = [first, second, third] booleans
    clear_region(disp, DIAMOND_CX - DIAMOND_R - 14, DIAMOND_CY - DIAMOND_R - 14,
                 (DIAMOND_R + 14) * 2, (DIAMOND_R + 14) * 2)

    home, first, second, third = base_positions()

    disp.draw_line(home[0], home[1], first[0], first[1], DIM)
    disp.draw_line(first[0], first[1], second[0], second[1], DIM)
    disp.draw_line(second[0], second[1], third[0], third[1], DIM)
    disp.draw_line(third[0], third[1], home[0], home[1], DIM)

    def base_square(pt, occupied):
        color = YELLOW if occupied else DIM
        disp.fill_rectangle(pt[0] - BASE_SIZE // 2, pt[1] - BASE_SIZE // 2,
                             BASE_SIZE, BASE_SIZE, color)

    base_square(first, bases_occupied[0])
    base_square(second, bases_occupied[1])
    base_square(third, bases_occupied[2])

    # home plate as a small white square (no occupancy flag - batter box)
    disp.fill_rectangle(home[0] - BASE_SIZE // 2, home[1] - BASE_SIZE // 2,
                         BASE_SIZE, BASE_SIZE, TEXT)


def draw_status(disp, g):
    clear_region(disp, 0, 0, SW, STATUS_Y + 14)
    half = "TOP" if g["half"] == 0 else "BOT"
    inning_str = "{} {}".format(half, g["inning"])
    draw_text_centered(disp, SW // 2, STATUS_Y, inning_str, ACCENT)


def draw_score(disp, g):
    clear_region(disp, 0, SCORE_Y, SW, 16)
    s = "{} {}   {} {}".format(TEAM_NAMES[0], g["score"][0],
                                TEAM_NAMES[1], g["score"][1])
    draw_text_centered(disp, SW // 2, SCORE_Y, s, TEXT)


def draw_count(disp, g):
    clear_region(disp, 0, COUNT_Y, SW, 16)
    s = "B:{}  S:{}  O:{}".format(g["balls"], g["strikes"], g["outs"])
    draw_text_centered(disp, SW // 2, COUNT_Y, s, TEXT)


def draw_matchup(disp, g):
    clear_region(disp, 0, MATCHUP_Y, SW, 16)
    batting_team = g["half"]  # 0 = away batting, 1 = home batting
    you_bat = (batting_team == g["your_team"])
    s = "YOU ARE BATTING" if you_bat else "YOU ARE PITCHING"
    draw_text_centered(disp, SW // 2, MATCHUP_Y, s, GREEN if you_bat else ORANGE)


def draw_hint(disp, s, color=DIM):
    clear_region(disp, 0, HINT_Y, SW, 12)
    draw_text_centered(disp, SW // 2, HINT_Y, s, color)


def clear_interact(disp):
    clear_region(disp, 0, INTERACT_Y, SW, 30)


def redraw_all(disp, g):
    disp.clear(BG)
    draw_status(disp, g)
    draw_score(disp, g)
    draw_diamond(disp, g["bases"])
    draw_count(disp, g)
    draw_matchup(disp, g)


# ===== GAME STATE =====
def new_game():
    return {
        "inning": 1,
        "half": 0,          # 0 = top (away bats), 1 = bottom (home bats)
        "score": [0, 0],
        "outs": 0,
        "balls": 0,
        "strikes": 0,
        "bases": [False, False, False],  # first, second, third
        "your_team": 0,      # you are AWAY; flips each half automatically
        "game_over": False,
    }


def reset_count(g):
    g["balls"] = 0
    g["strikes"] = 0


def reset_half_inning(g):
    g["outs"] = 0
    g["bases"] = [False, False, False]
    reset_count(g)


# ===== BASERUNNING =====
def advance_runners(g, bases_to_advance, batter_reaches):
    """bases_to_advance: how many bases every existing runner (and the
    batter, if batter_reaches) moves. Returns runs scored this play."""
    runs = 0
    first, second, third = g["bases"]
    new_first, new_second, new_third = False, False, False

    # Move existing runners from back to front so we don't double-move.
    if third:
        if bases_to_advance >= 1:
            runs += 1
        else:
            new_third = True
    if second:
        pos = 2 + bases_to_advance
        if pos >= 4:
            runs += 1
        elif pos == 3:
            new_third = True
        elif pos == 2:
            new_second = True
    if first:
        pos = 1 + bases_to_advance
        if pos >= 4:
            runs += 1
        elif pos == 3:
            new_third = True
        elif pos == 2:
            new_second = True
        elif pos == 1:
            new_first = True

    if batter_reaches:
        pos = bases_to_advance
        if pos >= 4:
            runs += 1
        elif pos == 3:
            new_third = True
        elif pos == 2:
            new_second = True
        elif pos == 1:
            new_first = True

    g["bases"] = [new_first, new_second, new_third]
    g["score"][g["half"]] += runs
    return runs


def force_walk_advance(g):
    """Batter walks to first - only forces runners that are themselves
    forced (standard baseball walk rules)."""
    first, second, third = g["bases"]
    new_first, new_second, new_third = True, second, third
    if first:
        new_second = True
        if second:
            new_third = True
            if third:
                g["score"][g["half"]] += 1
    g["bases"] = [new_first, new_second, new_third]


# ===== CONTACT RESOLUTION =====
def contact_outcome(quality):
    r = random.random()
    if quality == "perfect":
        if r < 0.50:
            return "HR"
        elif r < 0.68:
            return "TRIPLE"
        elif r < 0.90:
            return "DOUBLE"
        else:
            return "SINGLE"
    elif quality == "good":
        if r < 0.10:
            return "TRIPLE"
        elif r < 0.45:
            return "DOUBLE"
        elif r < 0.90:
            return "SINGLE"
        else:
            return "FLYOUT"
    elif quality == "ok":
        if r < 0.45:
            return "SINGLE"
        elif r < 0.80:
            return "GROUNDOUT"
        else:
            return "FLYOUT"
    else:  # "weak"
        if r < 0.15:
            return "FOUL"
        elif r < 0.65:
            return "GROUNDOUT"
        else:
            return "FLYOUT"


def apply_outcome(disp, g, outcome):
    if outcome == "HR":
        runs = advance_runners(g, 4, True)
        result_text = "HOME RUN! +{} run{}".format(runs, "" if runs == 1 else "s")
        result_color = YELLOW
        reset_count(g)
        end_at_bat = True
    elif outcome == "TRIPLE":
        runs = advance_runners(g, 3, True)
        result_text = "TRIPLE! +{} run{}".format(runs, "" if runs == 1 else "s")
        result_color = GREEN
        reset_count(g)
        end_at_bat = True
    elif outcome == "DOUBLE":
        runs = advance_runners(g, 2, True)
        result_text = "DOUBLE! +{} run{}".format(runs, "" if runs == 1 else "s")
        result_color = GREEN
        reset_count(g)
        end_at_bat = True
    elif outcome == "SINGLE":
        runs = advance_runners(g, 1, True)
        result_text = "SINGLE! +{} run{}".format(runs, "" if runs == 1 else "s")
        result_color = GREEN
        reset_count(g)
        end_at_bat = True
    elif outcome == "GROUNDOUT":
        g["outs"] += 1
        result_text = "GROUNDOUT"
        result_color = RED
        reset_count(g)
        end_at_bat = True
    elif outcome == "FLYOUT":
        g["outs"] += 1
        result_text = "FLYOUT"
        result_color = RED
        reset_count(g)
        end_at_bat = True
    elif outcome == "FOUL":
        if g["strikes"] < 2:
            g["strikes"] += 1
        result_text = "FOUL BALL"
        result_color = DIM
        end_at_bat = False
    else:
        result_text = outcome
        result_color = TEXT
        end_at_bat = False

    return result_text, result_color, end_at_bat


# ===== SWING TIMING MINIGAME (you are batting) =====
METER_X = 40
METER_Y = INTERACT_Y
METER_W = 240
METER_H = 18


def draw_meter_base(disp, sweet_start, sweet_w):
    disp.fill_rectangle(METER_X, METER_Y, METER_W, METER_H, 0x39C7)  # grey track
    disp.fill_rectangle(METER_X + sweet_start, METER_Y, sweet_w, METER_H, GREEN)
    disp.draw_rectangle(METER_X, METER_Y, METER_W, METER_H, TEXT)


def erase_indicator(disp, x):
    # Redraw whatever should be under the indicator column (track or
    # sweet spot) rather than a full meter repaint every frame.
    pass  # handled by caller redrawing the 3px strip itself


def pitch_to_player(disp, g):
    """CPU pitches, you swing. Returns (quality_or_None, pitch_in_zone)."""
    pitch_in_zone = random.random() < 0.62

    # Sweet spot narrower (harder) for pitches in the zone (real
    # strikes are the ones worth timing well); off-zone pitches still
    # get a sweet spot so a disciplined "take" isn't the only option,
    # but it's shifted so guessing right is harder.
    sweet_w = random.randint(28, 40) if pitch_in_zone else random.randint(18, 26)
    sweet_start = random.randint(0, METER_W - sweet_w)
    step_px = random.randint(6, 11)  # indicator speed per frame

    draw_hint(disp, "SELECT = SWING", TEXT)
    draw_meter_base(disp, sweet_start, sweet_w)

    pos = 0
    direction = 1
    swung = False
    swing_pos = None
    frame_budget = 90  # ~ a couple of full sweeps before it's a "take"

    last_x = None
    while frame_budget > 0:
        btn = buttons.button_input()
        if btn == "select":
            swung = True
            swing_pos = pos
            break
        if btn == "alt":
            return "PAUSE", pitch_in_zone

        # erase old indicator column (restore track or sweet spot color)
        if last_x is not None:
            if sweet_start <= last_x < sweet_start + sweet_w:
                col_color = GREEN
            else:
                col_color = 0x39C7
            disp.fill_rectangle(METER_X + last_x, METER_Y + 1, 3, METER_H - 2, col_color)

        disp.fill_rectangle(METER_X + pos, METER_Y + 1, 3, METER_H - 2, TEXT)
        last_x = pos

        pos += step_px * direction
        if pos >= METER_W - 3:
            pos = METER_W - 3
            direction = -1
        elif pos <= 0:
            pos = 0
            direction = 1

        frame_budget -= 1
        utime.sleep_ms(18)

    clear_interact(disp)

    if not swung:
        return None, pitch_in_zone  # take - resolved as ball/strike by caller

    dist_to_center = abs(swing_pos - (sweet_start + sweet_w / 2))
    half_w = sweet_w / 2

    if dist_to_center <= half_w * 0.35:
        quality = "perfect"
    elif dist_to_center <= half_w * 0.8:
        quality = "good"
    elif dist_to_center <= half_w * 1.6:
        quality = "ok"
    else:
        quality = "weak"

    if not pitch_in_zone:
        # chasing a pitch out of the zone - downgrade contact quality
        downgrade = {"perfect": "good", "good": "ok", "ok": "weak", "weak": "weak"}
        quality = downgrade[quality]

    return quality, pitch_in_zone


# ===== PITCH ZONE SELECT (you are pitching) =====
ZONES = ("INSIDE", "MIDDLE", "OUTSIDE")
ZONE_BOX_W = 76
ZONE_GAP = 6


def draw_zone_selector(disp, selected_idx):
    total_w = ZONE_BOX_W * 3 + ZONE_GAP * 2
    start_x = (SW - total_w) // 2
    for i, name in enumerate(ZONES):
        bx = start_x + i * (ZONE_BOX_W + ZONE_GAP)
        color = ACCENT if i == selected_idx else DIM
        disp.draw_rectangle(bx, INTERACT_Y, ZONE_BOX_W, 24, color)
        disp.fill_rectangle(bx + 2, INTERACT_Y + 2, ZONE_BOX_W - 4, 20, BG)
        draw_text_centered(disp, bx + ZONE_BOX_W // 2, INTERACT_Y + 8, name, color)


def select_pitch_zone(disp, g):
    idx = 1
    draw_hint(disp, "LEFT/RIGHT AIM  SELECT THROW", TEXT)
    draw_zone_selector(disp, idx)
    while True:
        btn = buttons.button_input()
        if btn == "left":
            idx = (idx - 1) % len(ZONES)
            draw_zone_selector(disp, idx)
        elif btn == "right":
            idx = (idx + 1) % len(ZONES)
            draw_zone_selector(disp, idx)
        elif btn == "select":
            clear_interact(disp)
            return idx
        elif btn == "alt":
            clear_interact(disp)
            return "PAUSE"
        utime.sleep_ms(15)


def pitch_from_player(disp, g, zone_idx):
    """You pitch, CPU decides whether to swing. Returns quality_or_None
    (None = CPU took the pitch) and pitch_in_zone."""
    if zone_idx == 1:  # MIDDLE - easier to land, easier to hit
        pitch_in_zone = random.random() < 0.80
    else:  # corners - harder to land, harder to square up if it lands
        pitch_in_zone = random.random() < 0.55

    # simple "ball flight" animation
    draw_hint(disp, "", TEXT)
    bx, by = DIAMOND_CX, DIAMOND_CY + DIAMOND_R - 20
    tx, ty = DIAMOND_CX, DIAMOND_CY + 6
    steps = 6
    prev = None
    for s in range(steps + 1):
        t = s / steps
        x = int(bx + (tx - bx) * t)
        y = int(by + (ty - by) * t)
        if prev:
            disp.fill_rectangle(prev[0] - 3, prev[1] - 3, 6, 6, BG)
        fill_circle(disp, x, y, 3, TEXT)
        prev = (x, y)
        utime.sleep_ms(35)
    disp.fill_rectangle(prev[0] - 3, prev[1] - 3, 6, 6, BG)

    # count pressure makes CPU more/less aggressive
    swing_chance = 0.55
    if g["strikes"] == 2:
        swing_chance += 0.25  # protect the plate
    if g["balls"] == 3:
        swing_chance -= 0.15  # more selective on a full-ish count
    if not pitch_in_zone:
        swing_chance -= 0.30  # less likely to chase

    did_swing = random.random() < max(0.05, min(0.95, swing_chance))

    if not did_swing:
        return None, pitch_in_zone

    r = random.random()
    if zone_idx == 1:  # middle - hittable
        if r < 0.15:
            quality = "perfect"
        elif r < 0.45:
            quality = "good"
        elif r < 0.75:
            quality = "ok"
        else:
            quality = "weak"
    else:  # corners - CPU makes weaker contact
        if r < 0.05:
            quality = "perfect"
        elif r < 0.20:
            quality = "good"
        elif r < 0.55:
            quality = "ok"
        else:
            quality = "weak"

    if not pitch_in_zone:
        downgrade = {"perfect": "good", "good": "ok", "ok": "weak", "weak": "weak"}
        quality = downgrade[quality]

    return quality, pitch_in_zone


# ===== PAUSE MENU =====
def pause_menu(disp, g):
    clear_interact(disp)
    draw_hint(disp, "", TEXT)
    draw_text_centered(disp, SW // 2, INTERACT_Y, "PAUSED", TEXT)
    draw_text_centered(disp, SW // 2, INTERACT_Y + 14, "SELECT=RESUME  ALT=QUIT", DIM)
    while True:
        btn = buttons.button_input()
        if btn == "select":
            clear_interact(disp)
            redraw_all(disp, g)
            return "resume"
        if btn == "alt":
            return "quit"
        utime.sleep_ms(20)


# ===== ONE PITCH =====
def throw_one_pitch(disp, g):
    """Runs a single pitch (batting or pitching, whichever is yours
    this half-inning). Returns 'quit' if the player backed out, else
    None."""
    you_bat = (g["half"] == g["your_team"])

    if you_bat:
        quality, pitch_in_zone = pitch_to_player(disp, g)
    else:
        zone_idx = select_pitch_zone(disp, g)
        if zone_idx == "PAUSE":
            if pause_menu(disp, g) == "quit":
                return "quit"
            return None
        quality, pitch_in_zone = pitch_from_player(disp, g, zone_idx)

    if quality == "PAUSE":
        if pause_menu(disp, g) == "quit":
            return "quit"
        return None

    if quality is None:
        # take
        if pitch_in_zone:
            g["strikes"] += 1
            result_text, result_color = "Called Strike", ORANGE
        else:
            g["balls"] += 1
            result_text, result_color = "Ball", BLUE
        end_at_bat = False
    elif quality == "miss":
        g["strikes"] += 1
        result_text, result_color = "Swinging Strike", ORANGE
        end_at_bat = False
    else:
        outcome = contact_outcome(quality)
        result_text, result_color, end_at_bat = apply_outcome(disp, g, outcome)

    if not end_at_bat:
        if g["strikes"] >= 3:
            g["outs"] += 1
            result_text, result_color = "STRIKEOUT", RED
            reset_count(g)
            end_at_bat = True
        elif g["balls"] >= 4:
            force_walk_advance(g)
            result_text, result_color = "WALK", GREEN
            reset_count(g)
            end_at_bat = True

    draw_score(disp, g)
    draw_diamond(disp, g["bases"])
    draw_count(disp, g)
    draw_hint(disp, result_text, result_color)
    utime.sleep_ms(700)
    draw_hint(disp, "", TEXT)

    if end_at_bat:
        if check_walkoff(g):
            g["game_over"] = True
        elif g["outs"] >= 3:
            advance_half_inning(disp, g)

    return None


def advance_half_inning(disp, g):
    reset_half_inning(g)
    if g["half"] == 0:
        g["half"] = 1
        # Top of inning 9+ just ended - if the home team is already
        # ahead there's no need for them to bat, game's over now.
        if g["inning"] >= 9 and g["score"][1] > g["score"][0]:
            g["game_over"] = True
            return
    else:
        g["half"] = 0
        g["inning"] += 1
        # A full inning 9+ just completed - if it's not tied, the
        # game is decided. If tied, play on into extras.
        if g["inning"] > 9 and g["score"][0] != g["score"][1]:
            g["game_over"] = True
            return

    redraw_all(disp, g)


def check_walkoff(g):
    """True if the home team just took the lead batting in the bottom
    of inning 9+ - game ends immediately, no need to finish the inning
    or wait for 3 outs."""
    return (g["half"] == 1 and g["inning"] >= 9
            and g["score"][1] > g["score"][0])


def game_over_screen(disp, g):
    away, home = g["score"]
    disp.clear(BG)
    draw_text_centered(disp, SW // 2, 60, "GAME OVER", ACCENT)
    draw_text_centered(disp, SW // 2, 90, "{} {}  -  {} {}".format(
        TEAM_NAMES[0], away, TEAM_NAMES[1], home), TEXT)
    if away == home:
        winner = "TIE GAME"
        color = TEXT
    elif (away > home) == (g["your_team"] == 0):
        winner = "YOU WIN!"
        color = GREEN
    else:
        winner = "YOU LOSE"
        color = RED
    draw_text_centered(disp, SW // 2, 120, winner, color)
    draw_text_centered(disp, SW // 2, 160, "SELECT = PLAY AGAIN", DIM)
    draw_text_centered(disp, SW // 2, 176, "ALT = QUIT", DIM)
    while True:
        btn = buttons.button_input()
        if btn == "select":
            return "again"
        if btn == "alt":
            return "quit"
        utime.sleep_ms(20)


# ===== MAIN LOOP =====
def run(disp):
    while True:
        g = new_game()
        redraw_all(disp, g)

        quit_requested = False
        while not g["game_over"] and not quit_requested:
            outcome = throw_one_pitch(disp, g)
            gc.collect()
            if outcome == "quit":
                quit_requested = True

        if quit_requested:
            return

        choice = game_over_screen(disp, g)
        gc.collect()
        if choice == "quit":
            return
        # else loop back and start a new game
