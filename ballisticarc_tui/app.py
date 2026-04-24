"""Textual app for ballisticarc-tui.

Layout: BattlefieldView fills most of the screen; a right-rail
StatusPanel shows the current tank, angle/power, wind, weapon, and
per-tank HP bars. A flash bar below surfaces transient messages.

Keys documented in DECISIONS.md §11.
"""
from __future__ import annotations

import math
from typing import Optional

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Footer, Header, Static

from . import sprites
from . import state as state_mod
from .engine import (
    ANGLE_MAX,
    ANGLE_MIN,
    FIELD_H,
    FIELD_W,
    POWER_MAX,
    POWER_MIN,
    WEAPONS,
    WEAPON_ORDER,
    Engine,
    Tank,
    is_infinite,
)
from .screens import (
    GameOverScreen,
    HelpScreen,
    PauseScreen,
    RoundOverScreen,
    ShopScreen,
    WeaponPickerScreen,
)
from .sounds import Sounds


# ---- battlefield renderer ---------------------------------------------


class BattlefieldView(Widget):
    """Renders the playfield — sky, terrain, tanks, projectile trails,
    projectiles, explosions, turret-aim overlay for the active tank.

    Strategy: build a WxH grid of (glyph, style) per frame, then emit
    row-by-row strips with run-length compression. Simple enough for
    80×30 and the tick rate we target.
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self._frame = 0
        self._blank_cache: dict[int, str] = {}
        self._matrix_cache: Optional[tuple[object, list[list[tuple[str, Style]]]]] = None

    def field_pixel_size(self) -> tuple[int, int]:
        return FIELD_W, FIELD_H

    def _blank(self, n: int) -> str:
        s = self._blank_cache.get(n)
        if s is None:
            s = " " * n
            self._blank_cache[n] = s
        return s

    def _sky_cell(self, x: int, y: int) -> tuple[str, Style]:
        if sprites.star_at(x, y):
            return sprites.STAR_GLYPH, sprites.STAR_STYLE
        return sprites.SKY_BLANK, sprites.SKY_BG

    def _tank_colour_style(self, t: Tank, *, active: bool) -> Style:
        spec = f"bold {t.colour}" if active else t.colour
        return Style.parse(spec)

    def _build_matrix(self) -> list[list[tuple[str, Style]]]:
        W, H = FIELD_W, FIELD_H
        e = self.engine
        terrain = e.terrain
        frame = self._frame

        grid: list[list[tuple[str, Style]]] = [
            [self._sky_cell(x, y) for x in range(W)] for y in range(H)
        ]

        # Terrain: for each column, paint from terrain[x] to H-1.
        for x in range(W):
            top = terrain[x]
            if top < 0:
                top = 0
            if top >= H:
                top = H  # no ground at all
            if top < H:
                # Grass strip at top row.
                grid[top][x] = (sprites.GRASS_GLYPH, sprites.GRASS_STYLE)
                for y in range(top + 1, H):
                    if y >= H - 2:
                        grid[y][x] = (sprites.EARTH_GLYPH, sprites.DEEP_STYLE)
                    else:
                        grid[y][x] = (sprites.EARTH_GLYPH,
                                      sprites.earth_style(x, y))

        # Tanks.
        active_slot = e.current_tank_slot
        for t in e.tanks:
            tx = t.x
            ty = terrain[tx] - 1
            if not (0 <= ty < H):
                continue
            is_active = (t.alive and t.slot == active_slot and e.phase == "IDLE")
            if not t.alive:
                # Wreckage.
                for dx, glyph in zip((-1, 0, 1), sprites.TANK_WRECK_GLYPHS):
                    fx = tx + dx
                    if 0 <= fx < W:
                        grid[ty][fx] = (glyph, sprites.WRECK_STYLE)
                continue
            style = self._tank_colour_style(t, active=is_active)
            for dx, glyph in zip((-1, 0, 1), sprites.TANK_BODY_GLYPHS):
                fx = tx + dx
                if 0 <= fx < W:
                    grid[ty][fx] = (glyph, style)
            # Turret — 1 row above.
            tur_y = ty - 1
            if 0 <= tur_y < H:
                tur_style = (sprites.ACTIVE_GLOW_STYLE
                             if is_active and (frame & 1) == 0 else style)
                grid[tur_y][tx] = (sprites.TANK_TURRET_GLYPH, tur_style)

        # Aim line for the active human tank during IDLE.
        active = e.current_tank
        if (active is not None and active.alive and e.phase == "IDLE"
                and not e.paused):
            self._draw_aim_line(grid, active)

        # Projectile trails.
        for p in e.projectiles:
            style = sprites.PROJECTILE_TRAIL_STYLE
            for (tx, ty) in p.trail[:-1]:
                if 0 <= tx < W and 0 <= ty < H:
                    # Don't overwrite terrain — trails only in sky.
                    if ty < terrain[tx]:
                        grid[ty][tx] = (sprites.PROJECTILE_TRAIL_GLYPH, style)

        # Projectile heads.
        for p in e.projectiles:
            cx, cy = p.cell()
            if 0 <= cx < W and 0 <= cy < H:
                glyph = sprites.PROJECTILE_GLYPHS.get(p.weapon_id, "•")
                style = sprites.PROJECTILE_STYLES.get(
                    p.weapon_id, Style.parse("bold rgb(250,240,180)")
                )
                grid[cy][cx] = (glyph, style)

        # Explosions — disc pattern.
        for ex in e.explosions:
            glyph, style = sprites.explosion_visuals(
                ex.radius, ex.age, ex.lifetime, frame
            )
            r = ex.radius
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    d = math.hypot(dx, dy)
                    if d > r:
                        continue
                    px = ex.x + dx
                    py = ex.y + dy
                    if 0 <= px < W and 0 <= py < H:
                        grid[py][px] = (glyph, style)

        return grid

    def _draw_aim_line(
        self, grid: list[list[tuple[str, Style]]], t: Tank
    ) -> None:
        """Stub out a dotted line from the turret in the direction of
        `t.angle`, length proportional to `t.power`."""
        a = math.radians(t.angle)
        dx = -math.cos(a)
        dy = -math.sin(a)
        length = max(4, min(16, t.power // 60))
        x0 = t.x
        y0 = self.engine.terrain[t.x] - 2
        for i in range(1, length + 1):
            px = int(round(x0 + dx * i))
            py = int(round(y0 + dy * i))
            if not (0 <= px < FIELD_W and 0 <= py < FIELD_H):
                break
            # Stop the aim line if it enters terrain.
            if py >= self.engine.terrain[px]:
                break
            glyph = sprites.AIM_GLYPH_MARK if i == length else sprites.AIM_GLYPH_DOT
            grid[py][px] = (glyph, sprites.AIM_STYLE)

    # --- render_line --------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        height = self.size.height
        field_w, field_h = self.field_pixel_size()
        off_x = max(0, (width - field_w) // 2)
        off_y = max(0, (height - field_h) // 2)

        if y < off_y or y >= off_y + field_h:
            return Strip([Segment(self._blank(width), sprites.SKY_BG)], width)

        key = (
            self._frame, self.engine.tick_count,
            self.engine.phase,
            self.engine.current_tank_slot,
            self.engine.wind_strength,
            len(self.engine.projectiles), len(self.engine.explosions),
            tuple(self.engine.terrain),
            tuple((t.x, t.health, t.angle, t.power, t.selected_weapon)
                  for t in self.engine.tanks),
            field_w, field_h,
        )
        cache = self._matrix_cache
        if cache is None or cache[0] != key:
            matrix = self._build_matrix()
            self._matrix_cache = (key, matrix)
        else:
            matrix = cache[1]

        local_y = y - off_y
        row = matrix[local_y]

        segs: list[Segment] = []
        if off_x > 0:
            segs.append(Segment(self._blank(off_x), sprites.SKY_BG))
        run_style = row[0][1]
        run_text = [row[0][0]]
        for i in range(1, field_w):
            glyph, style = row[i]
            if style is run_style:
                run_text.append(glyph)
            else:
                segs.append(Segment("".join(run_text), run_style))
                run_style = style
                run_text = [glyph]
        segs.append(Segment("".join(run_text), run_style))
        right_pad = width - off_x - field_w
        if right_pad > 0:
            segs.append(Segment(self._blank(right_pad), sprites.SKY_BG))
        return Strip(segs, width)


# ---- status panel -----------------------------------------------------


class StatusPanel(Static):
    """Side panel: current tank, angle/power, wind, weapon, per-tank HP."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.border_title = "STATUS"

    def refresh_panel(self) -> None:
        e = self.engine
        t = e.current_tank
        text = Text()
        text.append(f"Round {e.round_no}/{e.total_rounds}\n", style="bold")
        text.append("\n")
        # Wind arrow.
        wind = e.wind_strength
        mag = abs(wind)
        arrow_len = max(1, mag)
        if wind < 0:
            arrow = "◀" + "━" * arrow_len
        elif wind > 0:
            arrow = "━" * arrow_len + "▶"
        else:
            arrow = "·"
        text.append("Wind   ", style="bold")
        text.append(f"{wind:>+3}  {arrow}\n",
                    style="bold rgb(150,220,250)")
        text.append("\n")
        if t is None:
            text.append("— no current tank —\n", style="dim")
        else:
            owner_tag = (f" [dim]({t.difficulty})[/]"
                         if t.is_ai else " [bold]YOU[/]")
            text.append("Turn   ", style="bold")
            text.append_text(Text.from_markup(
                f"[bold {t.colour}]{t.name}[/]{owner_tag}\n"))
            text.append("Angle  ", style="bold")
            text.append(f"{t.angle:>3}°\n",
                        style="bold rgb(240,200,100)")
            text.append("Power  ", style="bold")
            text.append(f"{t.power:>4}\n",
                        style="bold rgb(240,200,100)")
            # Weapon.
            spec = WEAPONS[t.selected_weapon]
            cnt = t.weapons.get(t.selected_weapon, 0)
            cnt_txt = "∞" if is_infinite(cnt) else str(cnt)
            text.append("Weapon ", style="bold")
            text.append(f"{spec.name} ×{cnt_txt}\n",
                        style=f"bold {t.colour}")
            text.append(f"Gold   ${t.gold:,}\n",
                        style="rgb(240,200,100)")

        text.append("\n")
        text.append("Tanks\n", style="bold")
        for tk in e.tanks:
            glyph = "■" if tk.alive else "×"
            bar_cells = 10
            hp = max(0, tk.health)
            full = int(round(bar_cells * hp / 100))
            bar = "█" * full + "░" * (bar_cells - full)
            owner = "YOU" if not tk.is_ai else tk.difficulty
            line = f" {glyph} {tk.name:<7} {bar} {hp:>3}  {owner}\n"
            style = tk.colour if tk.alive else "rgb(120,90,90)"
            text.append(line, style=style)

        text.append("\n")
        if e.phase == "FLYING":
            text.append("  — FLYING —  \n", style="bold rgb(255,180,60)")
        elif e.phase == "SETTLING":
            text.append("  — SETTLING —  \n", style="bold rgb(200,200,200)")
        elif e.phase == "ROUND_OVER":
            text.append("  — ROUND OVER —  \n", style="bold rgb(120,210,230)")
        elif e.phase == "GAME_OVER":
            text.append("  — GAME OVER —  \n", style="bold white on rgb(180,40,40)")
        elif e.paused:
            text.append("  — PAUSED —  \n", style="bold black on rgb(240,200,100)")
        else:
            text.append("←→ aim · ↑↓ power\n", style="dim")
            text.append("w weapon · e picker\n", style="dim")
            text.append("space fire · ? help\n", style="dim")

        self.update(text)


class FlashBar(Static):
    def set_message(self, msg: str) -> None:
        self.update(Text.from_markup(msg))


# ---- app --------------------------------------------------------------


class ScorchedEarthApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "Scorched Earth — Terminal"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("n", "new_game", "New"),
        Binding("s", "toggle_sound", "Sound"),
        Binding("question_mark", "help", "Help"),
        Binding("p", "pause", "Pause"),
        Binding("escape", "pause", "Pause", show=False),
        # Aim / power — priority so scroll panels don't eat them.
        Binding("left",        "aim(-1)",   "←", show=False, priority=True),
        Binding("right",       "aim(1)",    "→", show=False, priority=True),
        Binding("shift+left",  "aim(-5)",   "shift+←", show=False, priority=True),
        Binding("shift+right", "aim(5)",    "shift+→", show=False, priority=True),
        Binding("up",          "power(25)", "↑", show=False, priority=True),
        Binding("down",        "power(-25)","↓", show=False, priority=True),
        Binding("shift+up",    "power(100)","shift+↑", show=False, priority=True),
        Binding("shift+down",  "power(-100)","shift+↓", show=False, priority=True),
        Binding("w",           "next_weapon", "w", show=False, priority=True),
        Binding("shift+w",     "prev_weapon", "W", show=False, priority=True),
        Binding("e",           "weapon_picker", "e", show=False, priority=True),
        Binding("space",       "fire", "fire", show=False, priority=True),
        Binding("f",           "fire", "fire", show=False, priority=True),
    ]

    SIM_TICK = 0.040
    ANIM_TICK = 0.160
    AI_TURN_DELAY = 0.600

    def __init__(self) -> None:
        super().__init__()
        self._state = state_mod.load()
        self.engine = Engine()
        self.sounds = Sounds(
            enabled=bool(self._state.get("sound_enabled", False))
        )
        self.field_view = BattlefieldView(self.engine)
        self.status_panel = StatusPanel(self.engine)
        self.flash_bar = FlashBar(" ", id="flash-bar")

        # Engine → sound + flash wiring.
        self.engine.on("fire",         lambda t, p: self.sounds.play("fire"))
        self.engine.on("explosion",    lambda ex:    self.sounds.play("explode"))
        self.engine.on("tank_hit",     lambda t, d:  self.sounds.play("hit"))
        self.engine.on("tank_killed",  lambda t, k:  self.sounds.play("killed"))
        self.engine.on("round_over",   lambda w:     self.sounds.play("round"))
        self.engine.on("match_over",   lambda w:     self.sounds.play("game_over"))
        self.engine.on("turn_start",   lambda t:     self.sounds.play("turn"))

        self._round_handled_for: int = -1
        self._game_over_shown = False
        self._ai_pending_scheduled = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="field-col"):
                yield self.field_view
                yield self.flash_bar
            with Vertical(id="side"):
                yield self.status_panel
        yield Footer()

    async def on_mount(self) -> None:
        self._update_header()
        self.field_view.border_title = self._field_title()
        self.status_panel.refresh_panel()
        self._show_hint()
        self.set_interval(self.SIM_TICK, self._sim_tick)
        self.set_interval(self.ANIM_TICK, self._anim_pulse)

    # --- timers -------------------------------------------------------

    def _sim_tick(self) -> None:
        e = self.engine
        # If an AI turn is queued, schedule it — but don't fire inline so
        # the user can see "Cyan's turn" before it moves.
        if e.ai_pending and not self._ai_pending_scheduled and e.phase == "IDLE":
            self._ai_pending_scheduled = True
            self.set_timer(self.AI_TURN_DELAY, self._trigger_ai_turn)
        e.tick()
        self.field_view.refresh()
        self.status_panel.refresh_panel()
        self._update_header()
        self.field_view.border_title = self._field_title()

        # Round-end handling — show splash, then shop, then start next.
        if e.phase == "ROUND_OVER" and self._round_handled_for != e.round_no:
            self._round_handled_for = e.round_no
            self._show_round_over()
        # Match-over handling — final screen.
        if e.phase == "GAME_OVER" and not self._game_over_shown:
            self._game_over_shown = True
            self._save_state()
            self._show_game_over()

    def _anim_pulse(self) -> None:
        self.field_view._frame += 1
        self.field_view.refresh()

    def _trigger_ai_turn(self) -> None:
        self._ai_pending_scheduled = False
        if self.engine.ai_pending and self.engine.phase == "IDLE":
            self.engine.run_ai_turn()

    # --- actions ------------------------------------------------------

    def action_aim(self, delta: str) -> None:
        self.engine.adjust_angle(int(delta))

    def action_power(self, delta: str) -> None:
        self.engine.adjust_power(int(delta))

    def action_next_weapon(self) -> None:
        self.engine.cycle_weapon(1)

    def action_prev_weapon(self) -> None:
        self.engine.cycle_weapon(-1)

    def action_weapon_picker(self) -> None:
        t = self.engine.current_tank
        if t is None or t.is_ai or self.engine.phase != "IDLE":
            return
        if len(self.screen_stack) > 1:
            return

        def _after(wid: str | None) -> None:
            if wid:
                self.engine.select_weapon(wid)
                self.status_panel.refresh_panel()

        self.push_screen(WeaponPickerScreen(t.weapons), _after)

    def action_fire(self) -> None:
        if self.engine.fire():
            self.flash_bar.set_message("[dim]firing…[/]")
        else:
            if self.engine.phase != "IDLE":
                self.flash_bar.set_message(
                    "[dim]wait for projectile to land[/]"
                )

    def action_pause(self) -> None:
        if self.engine.phase == "GAME_OVER":
            return
        if any(isinstance(s, PauseScreen) for s in self.screen_stack):
            return
        if not self.engine.paused:
            self.engine.toggle_pause()
            self.status_panel.refresh_panel()
            self.push_screen(PauseScreen(), lambda _r: self._resume_pause())
        else:
            self._resume_pause()

    def _resume_pause(self) -> None:
        if self.engine.paused:
            self.engine.toggle_pause()
        self.status_panel.refresh_panel()
        self.flash_bar.set_message("[dim]resumed[/]")

    def action_new_game(self) -> None:
        self.engine.restart()
        self._game_over_shown = False
        self._round_handled_for = -1
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.field_view._matrix_cache = None
        self.field_view.refresh()
        self.status_panel.refresh_panel()
        self.flash_bar.set_message("[bold green]NEW MATCH[/]")
        self._update_header()

    def action_toggle_sound(self) -> None:
        if not self.sounds.available:
            self.flash_bar.set_message(
                "[red]no audio player[/] (install paplay / aplay / afplay)"
            )
            return
        on = self.sounds.toggle()
        self._state["sound_enabled"] = on
        state_mod.save(self._state)
        self.flash_bar.set_message(
            f"[bold {'green' if on else 'yellow'}]sound {'on' if on else 'off'}[/]"
        )

    def action_help(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self.push_screen(HelpScreen())

    # --- round / shop / game-over -----------------------------------

    def _show_round_over(self) -> None:
        e = self.engine
        alive = [t for t in e.tanks if t.alive]
        winner_name = alive[0].name if alive else "Draw"

        def _after_round(choice: str | None) -> None:
            # Match-over case — the engine already flipped phase to
            # GAME_OVER in _end_round(). Don't push the shop.
            if e.phase == "GAME_OVER":
                return
            # Otherwise, push the shop for the human.
            human = next((t for t in e.tanks if not t.is_ai), None)
            if human is None or not human.alive:
                # No human left — auto-skip shop, advance round.
                self._after_shop()
                return
            # AI tanks auto-shop before we show the modal.
            self._ai_auto_shop()
            self.push_screen(
                ShopScreen(e, human.slot),
                lambda _r: self._after_shop(),
            )

        self.push_screen(
            RoundOverScreen(winner_name, e.round_no, e.total_rounds),
            _after_round,
        )

    def _after_shop(self) -> None:
        if self.engine.phase == "GAME_OVER":
            return
        self.engine.start_next_round()
        self._ai_pending_scheduled = False
        self.field_view._matrix_cache = None
        self.field_view.refresh()
        self.status_panel.refresh_panel()
        self.flash_bar.set_message(
            f"[bold rgb(120,210,230)]ROUND {self.engine.round_no}[/]"
        )

    def _ai_auto_shop(self) -> None:
        """Each AI greedily buys what it can afford based on difficulty."""
        for t in self.engine.tanks:
            if not t.is_ai or t.gold <= 0:
                continue
            diff = t.difficulty
            if diff == "moron":
                # Dump gold randomly on anything affordable.
                while True:
                    affordable = [
                        w for w in WEAPON_ORDER
                        if WEAPONS[w].cost > 0 and t.gold >= WEAPONS[w].cost
                    ]
                    if not affordable:
                        break
                    w = self.engine.rng.choice(affordable)
                    t.gold -= WEAPONS[w].cost
                    cur = t.weapons.get(w, 0)
                    t.weapons[w] = (9999 if is_infinite(cur)
                                    else cur + 1)
                continue
            priority = {
                "lame":  ["missile", "dirt", "digger"],
                "poor":  ["missile", "mirv", "digger", "nuke"],
                "good":  ["nuke", "mirv", "missile", "digger"],
            }.get(diff, ["missile"])
            # Buy priority weapons while affordable.
            for w in priority:
                spec = WEAPONS[w]
                while t.gold >= spec.cost:
                    t.gold -= spec.cost
                    cur = t.weapons.get(w, 0)
                    if is_infinite(cur):
                        t.weapons[w] = 9999
                        break
                    t.weapons[w] = cur + 1
                    if t.weapons[w] >= 10:
                        break

    def _save_state(self) -> None:
        # Score for persistence: winner's total gold + kills bonus.
        if self.engine.match_winner is not None and self.engine.match_winner >= 0:
            t = self.engine.tanks[self.engine.match_winner]
            score = t.gold + t.kills * 500
        else:
            score = 0
        new_high = state_mod.record_high_score(self._state, score)
        self._state["sound_enabled"] = self.sounds.enabled
        state_mod.save(self._state)
        self._new_high = new_high

    def _show_game_over(self) -> None:
        e = self.engine
        standings = sorted(
            [(t.name, t.gold, t.kills, t.last_round_wins) for t in e.tanks],
            key=lambda r: -(r[1] + r[2] * 500),
        )
        winner_name = (standings[0][0] if standings else "nobody")

        def _after(choice: str | None) -> None:
            if choice == "new":
                self.action_new_game()
            elif choice == "quit":
                self.exit()

        self.push_screen(
            GameOverScreen(
                standings=standings,
                winner_name=winner_name,
                new_record=getattr(self, "_new_high", False),
            ),
            _after,
        )

    # --- small helpers -----------------------------------------------

    def _update_header(self) -> None:
        e = self.engine
        t = e.current_tank
        turn = t.name if t else "—"
        self.sub_title = (
            f"round {e.round_no}/{e.total_rounds}  ·  turn: {turn}  ·  "
            f"wind {e.wind_strength:+d}  ·  "
            f"alive {sum(1 for t in e.tanks if t.alive)}/{len(e.tanks)}"
        )

    def _field_title(self) -> str:
        e = self.engine
        return f"ROUND {e.round_no} — WIND {e.wind_strength:+d}"

    def _show_hint(self) -> None:
        self.flash_bar.set_message(
            "[dim]←→ aim · ↑↓ power · w weapon · space fire · p pause · ? help[/]"
        )


def run() -> None:
    app = ScorchedEarthApp()
    try:
        app.run()
    finally:
        import sys
        sys.stdout.write(
            "\033[?1000l\033[?1002l\033[?1003l"
            "\033[?1006l\033[?1015l\033[?25h"
        )
        sys.stdout.flush()
