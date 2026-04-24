"""Scripted playtest — drives the full TUI via Pilot end-to-end:

  1. boot the app
  2. nudge angle + power
  3. fire (human turn)
  4. let AI turns resolve across a full round
  5. open and dismiss the shop between rounds
  6. quit cleanly

Run:  python -m tests.playtest
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="scorched-play-")

from ballisticarc_tui.app import ScorchedEarthApp  # noqa: E402
from ballisticarc_tui.engine import Engine, Tank  # noqa: E402
from ballisticarc_tui.screens import RoundOverScreen, ShopScreen  # noqa: E402


def _two_tank_app() -> ScorchedEarthApp:
    """1 human + 1 weak AI — fast round resolution."""
    app = ScorchedEarthApp()
    app.engine = Engine(
        tanks=[
            Tank(slot=0, x=20, owner="human", name="You"),
            Tank(slot=1, x=60, owner="ai:moron", name="Bot"),
        ],
        seed=7,
        total_rounds=2,
    )
    app.field_view.engine = app.engine
    app.status_panel.engine = app.engine
    return app


async def _wait_until(pred, timeout: float = 10.0, step: float = 0.05) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


async def run() -> int:
    app = _two_tank_app()
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        e = app.engine
        print(f"[boot] phase={e.phase} round={e.round_no}/{e.total_rounds} "
              f"tanks={len(e.tanks)}")
        assert e.phase == "IDLE"
        assert e.current_tank_slot == 0  # human first

        # Adjust angle + power.
        await pilot.press("right", "right", "right")
        await pilot.press("up", "up")
        print(f"[aim] angle={e.tanks[0].angle} power={e.tanks[0].power}")
        assert e.tanks[0].angle != 90 or e.tanks[0].power != 500

        # Fire.
        await pilot.press("space")
        await pilot.pause()
        assert e.phase in ("FLYING", "SETTLING", "IDLE", "ROUND_OVER"), \
            f"unexpected phase after fire: {e.phase}"
        print(f"[fire] phase={e.phase} projectiles={len(e.projectiles)}")

        # Resolve the round — keep ticking until phase returns to IDLE
        # (AI turn pending) or ROUND_OVER. The sim_tick interval drives
        # AI turns automatically.
        ok = await _wait_until(
            lambda: e.phase == "ROUND_OVER" or
                    any(not t.alive for t in e.tanks),
            timeout=30.0,
        )
        print(f"[resolve] phase={e.phase} alive="
              f"{[t.alive for t in e.tanks]} waited={ok}")

        # If we hit ROUND_OVER a modal may be stacked — push past the
        # round-over screen into the shop.
        if e.phase == "ROUND_OVER":
            await _wait_until(
                lambda: any(isinstance(s, RoundOverScreen)
                            for s in app.screen_stack),
                timeout=5.0,
            )
            if any(isinstance(s, RoundOverScreen) for s in app.screen_stack):
                await pilot.press("enter")
                await pilot.pause()
            # Shop should pop up next (if human still alive).
            await _wait_until(
                lambda: any(isinstance(s, ShopScreen)
                            for s in app.screen_stack) or
                        e.phase == "GAME_OVER" or
                        e.phase == "IDLE",
                timeout=5.0,
            )
            if any(isinstance(s, ShopScreen) for s in app.screen_stack):
                print("[shop] open — dismissing")
                await pilot.press("enter")
                await pilot.pause()

        # Quit cleanly.
        await pilot.press("q")
        await pilot.pause()
        print("[quit] ok")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(run())
    except AssertionError as ex:
        traceback.print_exc()
        print(f"PLAYTEST FAILED: {ex}")
        sys.exit(1)
    except Exception as ex:
        traceback.print_exc()
        print(f"PLAYTEST ERROR: {ex}")
        sys.exit(2)
    print("PLAYTEST OK")
    sys.exit(rc)


if __name__ == "__main__":
    main()
