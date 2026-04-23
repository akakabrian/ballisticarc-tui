"""Glyphs + pre-parsed styles for scorched-earth-tui.

Night-sky background, layered earth (green top-crust, brown interior),
per-tank colours, hot-orange explosions, dim projectile trails.
"""
from __future__ import annotations

from rich.style import Style

# ---- background ------------------------------------------------------

SKY_BG = Style.parse("on rgb(8,10,22)")
SKY_BLANK = " "

# Sparse stars.
STAR_STYLE = Style.parse("rgb(120,120,160)")
STAR_GLYPH = "·"

# ---- terrain ---------------------------------------------------------

# Top-crust (1 row directly at `terrain[x]`) — leafy green.
GRASS_STYLE = Style.parse("bold rgb(90,200,90) on rgb(8,10,22)")
GRASS_GLYPH = "▀"

# Earth — below grass line. Alternates 2 shades by (x+y) & 1 for texture.
EARTH_STYLE_A = Style.parse("rgb(140,90,40) on rgb(80,50,20)")
EARTH_STYLE_B = Style.parse("rgb(110,70,30) on rgb(80,50,20)")
EARTH_GLYPH = "█"

# Deep earth at the very bottom.
DEEP_STYLE = Style.parse("rgb(70,50,30) on rgb(40,26,10)")


# ---- tanks -----------------------------------------------------------

# 3-cell tank body at (x-1, y), (x, y), (x+1, y).
TANK_BODY_GLYPHS = ("▐", "■", "▌")
# Turret marker at (x, y-1) — replaced by aim line for the active tank.
TANK_TURRET_GLYPH = "▀"
# Wreckage glyphs for a dead tank.
TANK_WRECK_GLYPHS = ("▖", "▂", "▗")
WRECK_STYLE = Style.parse("rgb(80,70,70)")

# Health bar glyphs.
HP_BAR_FULL = "█"
HP_BAR_EMPTY = "░"

# Active-tank pulse styles — alternate frames use dim variant.
ACTIVE_GLOW_STYLE = Style.parse("bold rgb(255,240,120)")

# ---- projectile ------------------------------------------------------

PROJECTILE_GLYPHS = {
    "baby":          "•",
    "missile":       "●",
    "nuke":          "☢",
    "mirv":          "◆",
    "mirv_warhead":  "◦",
    "digger":        "▼",
    "dirt":          "◯",
    "napalm":        "✦",
}

PROJECTILE_STYLES = {
    "baby":          Style.parse("bold rgb(250,240,180)"),
    "missile":       Style.parse("bold rgb(240,200,110)"),
    "nuke":          Style.parse("bold rgb(255,120,120)"),
    "mirv":          Style.parse("bold rgb(230,120,230)"),
    "mirv_warhead":  Style.parse("rgb(230,140,230)"),
    "digger":        Style.parse("bold rgb(200,150,90)"),
    "dirt":          Style.parse("bold rgb(180,140,90)"),
    "napalm":        Style.parse("bold rgb(255,140,60)"),
}

PROJECTILE_TRAIL_STYLE = Style.parse("rgb(150,150,170)")
PROJECTILE_TRAIL_GLYPH = "·"

# ---- explosions ------------------------------------------------------

EXPLOSION_STYLES = (
    Style.parse("bold rgb(255,240,120)"),   # new — yellow
    Style.parse("bold rgb(255,180,60)"),    # orange
    Style.parse("bold rgb(255,120,60)"),    # hot orange
    Style.parse("bold rgb(220,80,60)"),     # red
    Style.parse("rgb(170,60,50)"),          # fading
)
EXPLOSION_GLYPHS = ("∘", "○", "◌", "◍", "◉", "●")

# ---- turret aim line -------------------------------------------------

AIM_STYLE = Style.parse("rgb(250,220,120)")
AIM_GLYPH_DOT = "·"
AIM_GLYPH_MARK = "+"

# ---- wind ------------------------------------------------------------

WIND_ARROW_STYLE = Style.parse("bold rgb(150,220,250)")

# ---- helpers ---------------------------------------------------------


def star_at(x: int, y: int) -> bool:
    """Deterministic sparse stars — ~0.4%."""
    return (x * 7 + y * 13 + x * y * 3) % 277 == 0


def earth_style(x: int, y: int) -> Style:
    return EARTH_STYLE_A if ((x + y) & 1) == 0 else EARTH_STYLE_B


def explosion_visuals(radius: float, age: int, max_age: int, frame: int
                      ) -> tuple[str, Style]:
    """Pick (glyph, style) for an explosion of given radius/age/frame."""
    if age < 3:
        s = EXPLOSION_STYLES[0]
    elif age < 6:
        s = EXPLOSION_STYLES[1]
    elif age < 9:
        s = EXPLOSION_STYLES[2]
    elif age < 12:
        s = EXPLOSION_STYLES[3]
    else:
        s = EXPLOSION_STYLES[4]
    g = EXPLOSION_GLYPHS[(age // 2 + frame) % len(EXPLOSION_GLYPHS)]
    return g, s
