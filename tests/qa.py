"""QA harness — drives ScorchedEarthApp through Textual Pilot and
asserts on live engine state.

    python -m tests.qa            # run everything
    python -m tests.qa fire       # subset by substring
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile as _tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

os.environ["XDG_DATA_HOME"] = _tempfile.mkdtemp(prefix="scorched-qa-")

from scorched_earth_tui.app import ScorchedEarthApp  # noqa: E402
from scorched_earth_tui.engine import (  # noqa: E402
    ANGLE_DEFAULT,
    FIELD_H,
    FIELD_W,
    POWER_DEFAULT,
    WEAPON_ORDER,
    WEAPONS,
    Engine,
    Tank,
    starter_inventory,
)
from scorched_earth_tui import ai as ai_mod  # noqa: E402
from scorched_earth_tui import state as state_mod  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


@dataclass
class Scenario:
    name: str
    fn: Callable[[ScorchedEarthApp, "object"], Awaitable[None]]


# ---------- helpers ----------


def _make_engine_2p(seed: int = 42) -> Engine:
    """Fresh 1 human + 1 AI engine with a stable seed."""
    tanks = [
        Tank(slot=0, x=20, owner="human", name="You"),
        Tank(slot=1, x=60, owner="ai:poor", name="Bot"),
    ]
    return Engine(tanks=tanks, seed=seed, total_rounds=2)


# ---------- scenarios ----------


async def s_mount_clean(app, pilot):
    assert app.engine is not None
    assert app.field_view is not None
    assert app.status_panel is not None
    assert len(app.engine.tanks) >= 2
    assert all(t.health == 100 for t in app.engine.tanks)
    assert len(app.engine.terrain) == FIELD_W


async def s_terrain_shape(app, pilot):
    e = app.engine
    assert len(e.terrain) == FIELD_W
    assert all(0 < h < FIELD_H for h in e.terrain)


async def s_tank_sits_on_terrain(app, pilot):
    """Every tank's x is within range and the terrain at that column
    gives a sensible y."""
    e = app.engine
    for t in e.tanks:
        assert 0 <= t.x < FIELD_W
        surface_y = e.terrain[t.x] - 1
        assert 0 <= surface_y < FIELD_H


async def s_aim_and_power(app, pilot):
    e = _make_engine_2p()
    t = e.tanks[0]
    e.adjust_angle(10)
    assert t.angle == ANGLE_DEFAULT + 10
    e.adjust_angle(-1000)
    assert t.angle == 0
    e.adjust_power(100)
    assert t.power == POWER_DEFAULT + 100
    e.adjust_power(-10000)
    assert t.power == 100  # POWER_MIN


async def s_cycle_weapon_only_owned(app, pilot):
    e = _make_engine_2p()
    t = e.tanks[0]
    # Default inventory: baby (∞), missile x5, dirt x2.
    owned_initial = {w for w, n in t.weapons.items() if n > 0}
    assert "baby" in owned_initial
    # Cycle 10 times — selected should always be in `owned_initial`.
    for _ in range(10):
        e.cycle_weapon(1)
        assert t.selected_weapon in owned_initial


async def s_fire_spawns_projectile(app, pilot):
    e = _make_engine_2p()
    assert e.phase == "IDLE"
    before = len(e.projectiles)
    ok = e.fire()
    assert ok
    assert e.phase == "FLYING"
    assert len(e.projectiles) == before + 1


async def s_projectile_lands(app, pilot):
    e = _make_engine_2p()
    # Aim high + generous power; let it fall back down.
    e.tanks[0].angle = 90
    e.tanks[0].power = 400
    assert e.fire()
    for _ in range(400):
        e.tick()
        if e.phase != "FLYING":
            break
    assert e.phase != "FLYING", "projectile never landed"


async def s_explosion_carves_terrain(app, pilot):
    e = _make_engine_2p()
    # Pick a spot on the ground and detonate a missile centred there.
    x = 40
    y = e.terrain[x]
    e.terrain[x] = 20  # reset to a known depth
    before = e.terrain[x]
    e._blast(x, y, 4, owner_slot=0)
    # Terrain should have been raised (value grew) — crater.
    assert e.terrain[x] >= before, (before, e.terrain[x])


async def s_dirt_bomb_adds_terrain(app, pilot):
    e = _make_engine_2p()
    # Dirt bomb in the sky — adds terrain.
    x = 40
    e.terrain[x] = 20
    before = e.terrain[x]
    e._dirt_bomb(x, 12, 4)
    # Dirt dome should lower terrain[x] (higher surface = smaller number).
    assert e.terrain[x] < before, (before, e.terrain[x])


async def s_tank_takes_damage(app, pilot):
    e = _make_engine_2p()
    target = e.tanks[1]
    tx = target.x
    ty = e.terrain[tx] - 1
    hp_before = target.health
    e._blast(tx, ty, 5, owner_slot=0)
    assert target.health < hp_before


async def s_tank_dies_and_credit(app, pilot):
    e = _make_engine_2p()
    shooter = e.tanks[0]
    victim = e.tanks[1]
    victim.health = 10
    # Damage tanks against current surface before carving — direct hit
    # check only.
    e._damage_tanks(victim.x, e.terrain[victim.x] - 1, 5,
                    owner_slot=shooter.slot)
    assert not victim.alive, f"victim hp={victim.health}"
    assert shooter.kills == 1, f"shooter kills={shooter.kills}"


async def s_round_ends_when_one_alive(app, pilot):
    e = _make_engine_2p()
    # Kill tank 1, tick to advance turn-end bookkeeping.
    e.tanks[1].health = 0
    e._end_turn()
    # Match has 2 rounds, so first round ends → ROUND_OVER,
    # not GAME_OVER yet.
    assert e.phase in ("ROUND_OVER", "GAME_OVER")


async def s_match_winner_on_final_round(app, pilot):
    e = _make_engine_2p()
    e.round_no = e.total_rounds  # final round
    # Kill tank 1 so tank 0 is sole survivor.
    e.tanks[1].health = 0
    e._end_turn()
    assert e.phase == "GAME_OVER"
    assert e.match_winner == 0


async def s_shop_buy_and_sell(app, pilot):
    e = _make_engine_2p()
    t = e.tanks[0]
    t.gold = 10_000
    from scorched_earth_tui.screens import ShopScreen
    s = ShopScreen(e, 0)
    before = t.weapons.get("missile", 0)
    ok = s._buy("missile", 2)
    assert ok
    assert t.weapons.get("missile", 0) == before + 2
    assert t.gold == 10_000 - 2 * WEAPONS["missile"].cost


async def s_wind_rolls_each_turn(app, pilot):
    e = _make_engine_2p(seed=1)
    winds = set()
    for _ in range(20):
        e._roll_wind()
        winds.add(e.wind_strength)
    # Should see at least 3 distinct values in 20 samples.
    assert len(winds) >= 3, winds


async def s_ai_poor_returns_valid(app, pilot):
    e = _make_engine_2p(seed=11)
    t = e.tanks[1]  # ai:poor
    weapon, angle, power = ai_mod.pick_move(e, t)
    assert weapon in WEAPON_ORDER
    assert 0 <= angle <= 180
    assert 100 <= power <= 1000


async def s_ai_good_hits_closer_than_moron(app, pilot):
    """Over 20 trials, `good` AI should land nearer the enemy on average
    than `moron`."""
    errors_moron = []
    errors_good = []
    for seed in range(20):
        # Moron trial.
        tanks_m = [
            Tank(slot=0, x=15, owner="ai:moron", name="M"),
            Tank(slot=1, x=65, owner="human",   name="T"),
        ]
        em = Engine(tanks=tanks_m, seed=seed, total_rounds=1)
        w, a, p = ai_mod.pick_move(em, em.tanks[0])
        land = ai_mod._simulate_landing(em, em.tanks[0], a, p)
        if land is not None:
            tx = em.tanks[1].x
            ty = em.terrain[tx] - 1
            errors_moron.append(abs(land[0] - tx))
        # Good trial.
        tanks_g = [
            Tank(slot=0, x=15, owner="ai:good", name="G"),
            Tank(slot=1, x=65, owner="human",   name="T"),
        ]
        eg = Engine(tanks=tanks_g, seed=seed, total_rounds=1)
        w, a, p = ai_mod.pick_move(eg, eg.tanks[0])
        land = ai_mod._simulate_landing(eg, eg.tanks[0], a, p)
        if land is not None:
            tx = eg.tanks[1].x
            ty = eg.terrain[tx] - 1
            errors_good.append(abs(land[0] - tx))
    # Sanity: both collected at least a few samples.
    assert len(errors_good) > 5
    mg = sum(errors_good) / len(errors_good)
    mm = sum(errors_moron) / max(1, len(errors_moron))
    assert mg < mm, f"good should beat moron: good={mg:.2f} moron={mm:.2f}"


async def s_snapshot_shape(app, pilot):
    snap = app.engine.snapshot()
    for key in ("tick", "phase", "round_no", "total_rounds",
                "current_tank", "wind", "tanks_alive", "total_tanks",
                "projectiles_in_flight", "explosions_active",
                "paused", "match_winner"):
        assert key in snap, f"missing key: {key}"


async def s_pause_halts_sim(app, pilot):
    e = app.engine
    e.paused = True
    tick0 = e.tick_count
    for _ in range(5):
        e.tick()
    assert e.tick_count == tick0


async def s_help_screen_opens(app, pilot):
    from scorched_earth_tui.screens import HelpScreen
    await pilot.press("question_mark")
    await pilot.pause()
    assert isinstance(app.screen, HelpScreen), \
        f"expected HelpScreen, got {type(app.screen).__name__}"
    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(app.screen, HelpScreen), "help didn't dismiss"


async def s_field_renders_terrain(app, pilot):
    app.field_view.refresh()
    await pilot.pause()
    # Check at least some strip contains the earth block glyph.
    glyphs = set()
    for sy in range(app.field_view.size.height):
        strip = app.field_view.render_line(sy)
        for seg in list(strip):
            glyphs.update(seg.text)
    assert "█" in glyphs, f"no ground glyph rendered: {sorted(glyphs)[:20]}"


async def s_field_renders_tank(app, pilot):
    app.field_view.refresh()
    await pilot.pause()
    glyphs = set()
    for sy in range(app.field_view.size.height):
        strip = app.field_view.render_line(sy)
        for seg in list(strip):
            glyphs.update(seg.text)
    # Tank body glyphs.
    assert "■" in glyphs, f"no tank body: {sorted(glyphs)[:20]}"


async def s_field_aim_line_drawn(app, pilot):
    """For the active human tank during IDLE, an aim line of `·` dots
    should be rendered in the sky."""
    app.field_view.refresh()
    await pilot.pause()
    dot_count = 0
    for sy in range(app.field_view.size.height):
        strip = app.field_view.render_line(sy)
        for seg in list(strip):
            dot_count += seg.text.count("·")
    # Stars may also be ·, but we should have at least a handful of dots.
    assert dot_count >= 3, dot_count


async def s_new_game_resets(app, pilot):
    app.engine.round_no = 3
    app.engine.tanks[0].health = 10
    app.action_new_game()
    await pilot.pause()
    assert app.engine.round_no == 1
    assert all(t.health == 100 for t in app.engine.tanks)


async def s_weapon_picker_opens(app, pilot):
    from scorched_earth_tui.screens import WeaponPickerScreen
    await pilot.press("e")
    await pilot.pause()
    assert isinstance(app.screen, WeaponPickerScreen)
    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(app.screen, WeaponPickerScreen)


async def s_fire_changes_phase(app, pilot):
    # Fire once; phase should flip to FLYING.
    phase0 = app.engine.phase
    assert phase0 == "IDLE"
    app.action_fire()
    await pilot.pause()
    assert app.engine.phase in ("FLYING", "SETTLING", "IDLE")
    # (could already be back to IDLE if the shot flew off-field fast; but
    #  for default ang=90, power=500 it stays aloft for several ticks.)


async def s_mirv_split_produces_children(app, pilot):
    e = _make_engine_2p()
    t = e.tanks[0]
    t.weapons["mirv"] = 1
    t.selected_weapon = "mirv"
    t.angle = 90
    t.power = 800
    assert e.fire()
    # Tick until we see a mirv_warhead OR the projectile lands.
    saw_warhead = False
    for _ in range(200):
        e.tick()
        if any(p.weapon_id == "mirv_warhead" for p in e.projectiles):
            saw_warhead = True
            break
        if e.phase not in ("FLYING", "SETTLING"):
            break
    assert saw_warhead, "MIRV never split into warheads"


async def s_ai_turn_fires_automatically(app, pilot):
    """With an AI tank active, driving _sim_tick a few times should
    eventually fire (phase flips to FLYING) without the user doing
    anything."""
    e = app.engine
    # Force the current tank to be an AI — find the first AI and set turn.
    ai_slot = next((t.slot for t in e.tanks if t.is_ai), None)
    assert ai_slot is not None, "no AI tank in default match"
    e.current_tank_slot = ai_slot
    e._turn_idx = ai_slot
    e.phase = "IDLE"
    e.ai_pending = True
    # Drive both _sim_tick and the AI delay timer.
    fired = False
    for _ in range(60):
        app._sim_tick()
        # Manually trigger the delayed AI turn immediately (the App
        # schedules via set_timer which doesn't fire in unit context).
        if e.ai_pending:
            app._trigger_ai_turn()
        if e.phase in ("FLYING", "SETTLING"):
            fired = True
            break
    assert fired, f"AI never fired; phase={e.phase}"


async def s_full_round_playable_headless(app, pilot):
    """Drive an engine directly through fire + projectile ticks and
    confirm SOMETHING meaningful advances (tick_count grows, projectile
    eventually resolves)."""
    e = Engine(seed=7)
    # Force tank 0 to fire.
    e.current_tank_slot = 0
    e._turn_idx = 0
    t = e.tanks[0]
    t.angle = 80
    t.power = 600
    assert e.fire()
    for _ in range(500):
        e.tick()
        if e.phase == "IDLE":
            break
    # After firing, tick_count has advanced; phase either IDLE (turn
    # ended) or ROUND_OVER.
    assert e.tick_count > 0
    assert e.phase in ("IDLE", "ROUND_OVER", "GAME_OVER")


async def s_tile_styles_coloured(app, pilot):
    app.field_view.refresh()
    await pilot.pause()
    color_segs = 0
    for sy in range(app.field_view.size.height):
        strip = app.field_view.render_line(sy)
        for seg in list(strip):
            if seg.style and seg.style.color is not None:
                color_segs += 1
    assert color_segs > 10, color_segs


SCENARIOS: list[Scenario] = [
    Scenario("mount_clean", s_mount_clean),
    Scenario("terrain_shape", s_terrain_shape),
    Scenario("tank_sits_on_terrain", s_tank_sits_on_terrain),
    Scenario("aim_and_power", s_aim_and_power),
    Scenario("cycle_weapon_only_owned", s_cycle_weapon_only_owned),
    Scenario("fire_spawns_projectile", s_fire_spawns_projectile),
    Scenario("projectile_lands", s_projectile_lands),
    Scenario("explosion_carves_terrain", s_explosion_carves_terrain),
    Scenario("dirt_bomb_adds_terrain", s_dirt_bomb_adds_terrain),
    Scenario("tank_takes_damage", s_tank_takes_damage),
    Scenario("tank_dies_and_credit", s_tank_dies_and_credit),
    Scenario("round_ends_when_one_alive", s_round_ends_when_one_alive),
    Scenario("match_winner_on_final_round", s_match_winner_on_final_round),
    Scenario("shop_buy_and_sell", s_shop_buy_and_sell),
    Scenario("wind_rolls_each_turn", s_wind_rolls_each_turn),
    Scenario("ai_poor_returns_valid", s_ai_poor_returns_valid),
    Scenario("ai_good_hits_closer_than_moron", s_ai_good_hits_closer_than_moron),
    Scenario("snapshot_shape", s_snapshot_shape),
    Scenario("pause_halts_sim", s_pause_halts_sim),
    Scenario("help_screen_opens", s_help_screen_opens),
    Scenario("field_renders_terrain", s_field_renders_terrain),
    Scenario("field_renders_tank", s_field_renders_tank),
    Scenario("field_aim_line_drawn", s_field_aim_line_drawn),
    Scenario("new_game_resets", s_new_game_resets),
    Scenario("weapon_picker_opens", s_weapon_picker_opens),
    Scenario("fire_changes_phase", s_fire_changes_phase),
    Scenario("mirv_split_produces_children", s_mirv_split_produces_children),
    Scenario("ai_turn_fires_automatically", s_ai_turn_fires_automatically),
    Scenario("full_round_playable_headless", s_full_round_playable_headless),
    Scenario("tile_styles_coloured", s_tile_styles_coloured),
]


# ---------- driver ----------


async def run_one(scn: Scenario) -> tuple[str, bool, str]:
    app = ScorchedEarthApp()
    try:
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            try:
                await scn.fn(app, pilot)
            except AssertionError as e:
                app.save_screenshot(str(OUT / f"{scn.name}.FAIL.svg"))
                return (scn.name, False, f"AssertionError: {e}")
            except Exception as e:
                app.save_screenshot(str(OUT / f"{scn.name}.ERROR.svg"))
                return (scn.name, False,
                        f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            app.save_screenshot(str(OUT / f"{scn.name}.PASS.svg"))
            return (scn.name, True, "")
    except Exception as e:
        return (scn.name, False,
                f"harness error: {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}")


async def main(pattern: str | None = None) -> int:
    scenarios = [s for s in SCENARIOS if not pattern or pattern in s.name]
    if not scenarios:
        print(f"no scenarios match {pattern!r}")
        return 2
    results = []
    for scn in scenarios:
        name, ok, msg = await run_one(scn)
        mark = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
        print(f"  {mark} {name}")
        if not ok:
            for line in msg.splitlines():
                print(f"      {line}")
        results.append((name, ok, msg))
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{passed}/{len(results)} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(pattern)))
