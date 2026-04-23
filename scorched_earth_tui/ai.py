"""AI targeting for scorched-earth-tui — 4 difficulty tiers.

See DECISIONS.md §9 for the ladder:

  Moron  — totally random.
  Lame   — random weapon; angle/power biased toward nearest enemy.
  Poor   — solves ballistics (no wind) for nearest enemy + jitter.
  Good   — solves ballistics (with wind) by sampling candidate angles,
           picks weapon sensibly.

The engine calls ``pick_move(engine, tank)`` and expects back a
tuple ``(weapon_id, angle_degrees, power)``.
"""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

from .engine import (
    ANGLE_MAX,
    ANGLE_MIN,
    FIELD_W,
    GRAVITY,
    POWER_MAX,
    POWER_MIN,
    POWER_TO_VEL,
    WEAPONS,
    WIND_ACCEL_PER_UNIT,
)

if TYPE_CHECKING:
    from .engine import Engine, Tank


def pick_move(engine: "Engine", tank: "Tank") -> tuple[str, int, int]:
    """Dispatch based on difficulty."""
    diff = tank.difficulty
    if diff == "moron":
        return _moron(engine, tank)
    if diff == "lame":
        return _lame(engine, tank)
    if diff == "poor":
        return _poor(engine, tank)
    if diff == "good":
        return _good(engine, tank)
    # Unknown — fall back to lame.
    return _lame(engine, tank)


# ---- helpers ----------------------------------------------------------


def _nearest_enemy(engine: "Engine", tank: "Tank") -> "Tank | None":
    alive = [t for t in engine.tanks if t.alive and t.slot != tank.slot]
    if not alive:
        return None
    return min(alive, key=lambda t: abs(t.x - tank.x))


def _affordable_weapons(tank: "Tank") -> list[str]:
    return [w for w, n in tank.weapons.items() if n > 0 and w in WEAPONS]


def _choose_weapon(tank: "Tank", rng: random.Random,
                   preference: list[str]) -> str:
    """Pick from `preference` in order if owned; fall back to any owned."""
    for w in preference:
        if tank.weapons.get(w, 0) > 0:
            return w
    owned = _affordable_weapons(tank)
    if owned:
        return rng.choice(owned)
    return "baby"


def _simulate_landing(
    engine: "Engine", tank: "Tank", angle_deg: float, power: float,
) -> tuple[int, int] | None:
    """Fast-forward a projectile fired at (angle_deg, power) under
    current wind + gravity. Returns the impact cell, or None if it
    flies off-field. Pure function — does NOT mutate engine state."""
    a = math.radians(angle_deg)
    speed = power / POWER_TO_VEL
    vx = -math.cos(a) * speed
    vy = -math.sin(a) * speed
    x = float(tank.x)
    y = float(engine.terrain[tank.x] - 2)
    wax = engine.wind_strength * WIND_ACCEL_PER_UNIT
    for _ in range(500):
        vy += GRAVITY
        vx += wax
        x += vx
        y += vy
        cx, cy = int(round(x)), int(round(y))
        if cx < 0 or cx >= FIELD_W:
            return None
        if cy < 0:
            continue
        if cy >= len(engine.terrain):
            return (cx, len(engine.terrain) - 1)
        if cy >= engine.terrain[cx]:
            return (cx, engine.terrain[cx])
    return None


# ---- difficulty functions ---------------------------------------------


def _moron(engine: "Engine", tank: "Tank") -> tuple[str, int, int]:
    rng = engine.rng
    owned = _affordable_weapons(tank) or ["baby"]
    weapon = rng.choice(owned)
    angle = rng.randint(ANGLE_MIN + 10, ANGLE_MAX - 10)
    power = rng.randint(200, 900)
    return weapon, angle, power


def _lame(engine: "Engine", tank: "Tank") -> tuple[str, int, int]:
    rng = engine.rng
    enemy = _nearest_enemy(engine, tank)
    owned = _affordable_weapons(tank) or ["baby"]
    weapon = rng.choice(owned)
    if enemy is None:
        return weapon, rng.randint(60, 120), rng.randint(400, 700)
    # Aim roughly toward enemy: 45° to their side.
    direction = "left" if enemy.x < tank.x else "right"
    base_angle = 135 if direction == "left" else 45
    angle = base_angle + rng.randint(-30, 30)
    # Power: farther → more power.
    dist = abs(enemy.x - tank.x)
    base_power = 300 + dist * 8
    power = int(base_power + rng.randint(-100, 100))
    return weapon, angle, max(POWER_MIN, min(POWER_MAX, power))


def _poor(engine: "Engine", tank: "Tank") -> tuple[str, int, int]:
    """Analytic ballistic solution ignoring wind; jittered."""
    rng = engine.rng
    enemy = _nearest_enemy(engine, tank)
    if enemy is None:
        return _lame(engine, tank)
    preference = _poor_weapon_preference(tank, enemy)
    weapon = _choose_weapon(tank, rng, preference)
    # Solve: at power P, angle A — projectile range is
    #   t = 2 * (P sinA) / g         (time to return to same height)
    #   x = P cosA * t = P^2 sin2A / g
    # Target dx = enemy.x - tank.x; dy ignored in this tier.
    dx = enemy.x - tank.x
    # Pick power from a sensible band, then solve for angle. Use P = 500.
    power = 500
    # sin(2a) = dx * g / (P/POWER_TO_VEL)^2 — noting that classic SE
    # has angle measured from horizontal with 90°=vertical. Our 0° points
    # LEFT and 180° points RIGHT. Convert:
    #   physics angle θ = angle from horizontal of the direction-of-fire.
    #   For our convention, θ_classic = angle - 90° when angle >= 90°
    #   (firing right) or 90° - angle when angle < 90° (firing left).
    # To land at dx > 0 (right of us), aim in [0..90°] classic → our
    # angle in [90..180°]. At classic θ, our angle = 90 + sign(dx)*θ.
    vel = power / POWER_TO_VEL
    try:
        sin2 = max(-1.0, min(1.0, abs(dx) * GRAVITY / (vel * vel)))
        theta_classic = math.degrees(math.asin(sin2)) / 2.0
    except ValueError:
        theta_classic = 45.0
    if dx >= 0:
        angle = 90 + theta_classic
    else:
        angle = 90 - theta_classic
    # 15° jitter, 100 power jitter.
    angle += rng.randint(-15, 15)
    power += rng.randint(-100, 100)
    return (
        weapon,
        int(max(ANGLE_MIN, min(ANGLE_MAX, angle))),
        int(max(POWER_MIN, min(POWER_MAX, power))),
    )


def _good(engine: "Engine", tank: "Tank") -> tuple[str, int, int]:
    """Sample candidate (angle, power) pairs, simulate trajectory with
    wind, pick the one whose impact is closest to the nearest enemy."""
    rng = engine.rng
    enemy = _nearest_enemy(engine, tank)
    if enemy is None:
        return _lame(engine, tank)
    preference = _good_weapon_preference(tank, enemy, engine)
    weapon = _choose_weapon(tank, rng, preference)
    # Start from the poor-tier analytical guess, then search around it.
    _, seed_angle, seed_power = _poor(engine, tank)
    best = (seed_angle, seed_power, 10_000.0)
    # Search small grid of candidates.
    angle_offsets = (-20, -10, -5, 0, 5, 10, 20)
    power_offsets = (-200, -100, 0, 100, 200)
    for da in angle_offsets:
        for dp in power_offsets:
            a = seed_angle + da
            p = seed_power + dp
            a = max(ANGLE_MIN, min(ANGLE_MAX, a))
            p = max(POWER_MIN, min(POWER_MAX, p))
            land = _simulate_landing(engine, tank, a, p)
            if land is None:
                continue
            ex = enemy.x
            ey = engine.terrain[enemy.x] - 1
            err = math.hypot(land[0] - ex, land[1] - ey)
            if err < best[2]:
                best = (a, p, err)
    angle, power, _ = best
    # 5° / 40 power jitter.
    angle += rng.randint(-5, 5)
    power += rng.randint(-40, 40)
    return (
        weapon,
        int(max(ANGLE_MIN, min(ANGLE_MAX, angle))),
        int(max(POWER_MIN, min(POWER_MAX, power))),
    )


# ---- weapon preference tables ----------------------------------------


def _poor_weapon_preference(tank: "Tank", enemy: "Tank") -> list[str]:
    """If low HP → use best weapon; else missile."""
    if tank.health <= 30:
        return ["nuke", "mirv", "missile", "baby"]
    return ["missile", "baby", "mirv", "nuke"]


def _good_weapon_preference(
    tank: "Tank", enemy: "Tank", engine: "Engine"
) -> list[str]:
    """Prefer big weapons against low-HP enemies; MIRV when they're far."""
    dist = abs(enemy.x - tank.x)
    if enemy.health <= 30:
        return ["nuke", "missile", "mirv", "baby"]
    if dist > 35:
        return ["mirv", "missile", "baby", "nuke"]
    return ["missile", "mirv", "nuke", "baby"]
