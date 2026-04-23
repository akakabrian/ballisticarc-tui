"""Modal screens — help, pause, shop, round-over, game-over.

Per the tui-game-build skill: priority=True App bindings (movement
arrows, fire) beat ModalScreen bindings. We stick to non-conflicting
keys inside modals (`+`, `-`, letters, numbers).
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .engine import WEAPON_ORDER, WEAPONS, Engine, is_infinite


HELP_TEXT = (
    "[bold rgb(240,200,100)]SCORCHED EARTH — terminal edition[/]\n\n"
    "[bold]Goal[/]  last tank standing wins the round. Best of "
    "[bold]3 rounds[/] takes the match.\n\n"
    "[bold]Turn keys[/]\n"
    "  ← →        turret angle −1°/+1°  (shift: ±5°)\n"
    "  ↑ ↓        muzzle power ±25      (shift: ±100)\n"
    "  w / W      next / prev weapon\n"
    "  e          open equipment picker\n"
    "  space / f  fire\n\n"
    "[bold]Other[/]\n"
    "  p / escape pause\n"
    "  s          toggle synth sounds\n"
    "  ?          this help\n"
    "  n          new game (at GAME OVER / ROUND OVER)\n"
    "  q          quit\n\n"
    "[bold]Weapons[/]\n"
    "  baby missile   free, small blast, unlimited\n"
    "  missile        medium blast\n"
    "  nuke           huge crater, expensive\n"
    "  MIRV           splits into 3 warheads at the apex\n"
    "  digger         carves a vertical shaft\n"
    "  dirt ball      [bold]adds[/] terrain — bury enemies\n"
    "  napalm         burns — small bonus damage\n\n"
    "[bold]Wind[/] is rolled [bold]every turn[/] — watch the arrow.\n"
    "[dim]press any key to dismiss[/]"
)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("q", "close", "close"),
        Binding("question_mark", "close", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-body"):
            yield Static(HELP_TEXT, id="help-content")

    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class PauseScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("p", "close", "resume"),
        Binding("escape", "close", "resume"),
        Binding("space", "close", "resume"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="pause-body"):
            yield Static(
                "[bold rgb(240,200,100)]— PAUSED —[/]\n\n"
                "[dim]any key to resume[/]",
                id="pause-content",
            )

    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class WeaponPickerScreen(ModalScreen[str]):
    """Weapon-picker modal (`e` key during IDLE). Number keys 1..9 pick
    weapons from the owned list in order; escape cancels."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("q",      "cancel", "cancel"),
    ]

    def __init__(self, tank_weapons: dict[str, int]) -> None:
        super().__init__()
        self.owned = [w for w in WEAPON_ORDER if tank_weapons.get(w, 0) > 0]
        self.counts = tank_weapons

    def compose(self) -> ComposeResult:
        lines = ["[bold rgb(240,200,100)]Select weapon[/]\n"]
        for i, w in enumerate(self.owned, start=1):
            spec = WEAPONS[w]
            n = self.counts.get(w, 0)
            n_txt = "∞" if is_infinite(n) else str(n)
            lines.append(
                f"  [bold]{i}[/]  {spec.name:<14} [dim]×{n_txt}[/]"
            )
        lines.append("\n[dim]press a number, esc to cancel[/]")
        with Vertical(id="weapon-body"):
            yield Static("\n".join(lines), id="weapon-content")

    def on_key(self, event) -> None:
        k = event.key
        if k in ("escape", "q"):
            event.stop()
            event.prevent_default()
            self.dismiss("")
            return
        if k.isdigit():
            idx = int(k) - 1
            if 0 <= idx < len(self.owned):
                event.stop()
                event.prevent_default()
                self.dismiss(self.owned[idx])

    def action_cancel(self) -> None:
        self.dismiss("")


class ShopScreen(ModalScreen[None]):
    """Between-round shop. Human player spends their gold. AI tanks
    auto-buy before the screen is shown (App handles that).

    Keys:
      * `1..7`  buy 1 of weapon N (in WEAPON_ORDER)
      * `+/-`   bulk +5 / -5 of the highlighted slot (we just bulk-buy 5)
      * `enter` / `space`  close
      * `escape` close
    """

    BINDINGS = [
        Binding("enter",  "close", "done"),
        Binding("space",  "close", "done"),
        Binding("escape", "close", "done"),
        Binding("q",      "close", "done"),
    ]

    def __init__(self, engine: Engine, buyer_slot: int) -> None:
        super().__init__()
        self.engine = engine
        self.buyer_slot = buyer_slot

    @property
    def tank(self):
        return self.engine.tanks[self.buyer_slot]

    def compose(self) -> ComposeResult:
        with Vertical(id="shop-body"):
            yield Static(self._render_body(), id="shop-content")

    def _render_body(self) -> str:
        t = self.tank
        lines = [
            f"[bold rgb(240,200,100)]Armoury — round {self.engine.round_no}"
            f" complete[/]",
            f"\n[bold]{t.name}[/]  gold [bold rgb(240,200,100)]"
            f"{t.gold:,}[/]  kills {t.kills}\n",
            "Press 1..7 to buy 1 ; +/- to buy/sell 5 ; enter to continue.\n",
        ]
        for i, wid in enumerate(WEAPON_ORDER, start=1):
            spec = WEAPONS[wid]
            n = t.weapons.get(wid, 0)
            n_txt = "∞" if is_infinite(n) else str(n)
            can = t.gold >= spec.cost if spec.cost > 0 else True
            colour = "bold" if can else "dim"
            lines.append(
                f"  [{colour}]{i}[/]  {spec.name:<14} "
                f"[dim]${spec.cost:>6,}[/]  "
                f"×{n_txt:<3}  "
            )
        lines.append(
            "\n[dim]baby missile is always free and unlimited.[/]"
        )
        return "\n".join(lines)

    def _refresh(self) -> None:
        content: Static = self.query_one("#shop-content", Static)  # type: ignore[assignment]
        content.update(self._render_body())

    def on_key(self, event) -> None:
        k = event.key
        if k in ("enter", "space", "escape", "q"):
            return  # handled by BINDINGS → action_close
        t = self.tank
        # Digit → buy 1 of corresponding weapon.
        if k.isdigit():
            idx = int(k) - 1
            if 0 <= idx < len(WEAPON_ORDER):
                wid = WEAPON_ORDER[idx]
                self._buy(wid, 1)
                event.stop()
                event.prevent_default()
                self._refresh()
            return
        if k == "plus":
            # Bulk-buy 5 of the cheapest affordable weapon.
            for wid in WEAPON_ORDER:
                if wid == "baby":
                    continue
                if t.gold >= WEAPONS[wid].cost * 5:
                    self._buy(wid, 5)
                    break
            event.stop()
            self._refresh()
            return
        if k == "minus":
            # Sell 1 of the most-owned non-baby weapon at half price.
            owned = [(wid, t.weapons.get(wid, 0)) for wid in WEAPON_ORDER
                     if wid != "baby" and t.weapons.get(wid, 0) > 0
                     and not is_infinite(t.weapons.get(wid, 0))]
            if owned:
                owned.sort(key=lambda p: -p[1])
                wid, _ = owned[0]
                t.weapons[wid] = max(0, t.weapons.get(wid, 0) - 1)
                t.gold += WEAPONS[wid].cost // 2
            event.stop()
            self._refresh()
            return

    def _buy(self, wid: str, qty: int) -> bool:
        spec = WEAPONS.get(wid)
        if spec is None:
            return False
        t = self.tank
        total = spec.cost * qty
        if spec.cost == 0:
            # Baby is free; just top it up.
            t.weapons[wid] = 9999
            return True
        if t.gold < total:
            return False
        t.gold -= total
        cur = t.weapons.get(wid, 0)
        if is_infinite(cur):
            t.weapons[wid] = 9999
        else:
            t.weapons[wid] = cur + qty
        return True

    def action_close(self) -> None:
        self.dismiss(None)


class RoundOverScreen(ModalScreen[str]):
    """Short between-round splash: who won, then `enter` to open the
    shop (or directly start the next round at round 3)."""

    BINDINGS = [
        Binding("enter",  "proceed", "continue"),
        Binding("space",  "proceed", "continue"),
        Binding("escape", "proceed", "continue"),
    ]

    def __init__(self, winner_name: str, round_no: int,
                 total_rounds: int) -> None:
        super().__init__()
        self.winner_name = winner_name
        self.round_no = round_no
        self.total_rounds = total_rounds

    def compose(self) -> ComposeResult:
        more = self.round_no < self.total_rounds
        msg = (
            f"[bold rgb(120,210,230)]ROUND {self.round_no} COMPLETE[/]\n\n"
            f"[bold]{self.winner_name}[/] wins the round.\n\n"
            + ("[dim]press enter for armoury & next round[/]"
               if more else "[dim]press enter for final results[/]")
        )
        with Vertical(id="roundover-body"):
            yield Static(msg, id="roundover-content")

    def on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        self.dismiss("ok")

    def action_proceed(self) -> None:
        self.dismiss("ok")


class GameOverScreen(ModalScreen[str]):
    """Final results. `n` = new match, `q` = quit."""

    BINDINGS = [
        Binding("n", "new_game", "new"),
        Binding("q", "quit_game", "quit"),
        Binding("escape", "quit_game", "quit"),
    ]

    def __init__(self, standings: list[tuple[str, int, int, int]],
                 winner_name: str, new_record: bool) -> None:
        super().__init__()
        self.standings = standings   # [(name, gold, kills, wins), ...]
        self.winner_name = winner_name
        self.new_record = new_record

    def compose(self) -> ComposeResult:
        record = (" [bold rgb(255,240,120)]NEW HIGH SCORE![/]\n"
                  if self.new_record else "\n")
        lines = [
            f"[bold rgb(240,200,100)]MATCH OVER[/]",
            f"Winner: [bold]{self.winner_name}[/]{record}",
            "",
            "[bold]Final standings[/]",
        ]
        for name, gold, kills, wins in self.standings:
            lines.append(
                f"  {name:<10} gold {gold:>6,}  kills {kills}  wins {wins}"
            )
        lines.append("")
        lines.append(
            "press [bold]n[/] for a new match · [bold]q[/] to quit"
        )
        with Vertical(id="gameover-body"):
            yield Static("\n".join(lines), id="gameover-content")

    def action_new_game(self) -> None:
        self.dismiss("new")

    def action_quit_game(self) -> None:
        self.dismiss("quit")
