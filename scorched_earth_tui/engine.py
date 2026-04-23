"""Scorched Earth engine — pure Python, turn-driven with a short
animation tick for in-flight projectiles.

See DECISIONS.md §2 for the phase model:

    IDLE       current tank is aiming / choosing weapon
    FLYING     one or more projectiles in the air; tick at 25 Hz
    SETTLING   explosions / dirt / tank-falls still resolving
    ROUND_OVER only one side has tanks left
    GAME_OVER  match is done

Coordinates are (x, y) floats where y grows DOWN (row 0 = sky top,
row 29 = ground bottom). ``terrain[x]`` is the integer row where
the ground surface starts at column x. All columns [0..FIELD_W).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


# ---- field dimensions --------------------------------------------------

FIELD_W = 80
FIELD_H = 30
SKY_MIN = 8          # terrain can't rise above this row (keeps tanks visible)
SKY_MAX = 26         # terrain can't drop below this row (keeps floor visible)

# ---- physics -----------------------------------------------------------

GRAVITY = 0.08                 # cells/tick², downward (+y)
WIND_ACCEL_PER_UNIT = 0.002    # cells/tick² per unit of wind_strength
# Muzzle velocity: power / POWER_TO_VEL cells/tick.
POWER_TO_VEL = 100.0
POWER_MIN = 100
POWER_MAX = 1000
ANGLE_MIN = 0
ANGLE_MAX = 180
ANGLE_DEFAULT = 90
POWER_DEFAULT = 500

# ---- damage ------------------------------------------------------------

EXPLOSION_DAMAGE_MAX = 50     # direct hit at distance 0
FALL_DAMAGE_PER_CELL = 3      # per cell beyond the free 2-cell fall

# ---- wind --------------------------------------------------------------

WIND_STRENGTH_MAX = 10         # |wind| in [0..10]


Phase = Literal["IDLE", "FLYING", "SETTLING", "ROUND_OVER", "GAME_OVER"]


# ---- weapons -----------------------------------------------------------


@dataclass(frozen=True)
class WeaponSpec:
    id: str
    name: str
    cost: int                      # shop price
    radius: int                    # explosion radius (cells)
    starter_count: int = 0         # amount given to each tank at match start
    effect: str = "explode"        # "explode" | "mirv" | "digger" | "dirt" | "napalm"


WEAPONS: dict[str, WeaponSpec] = {
    "baby":    WeaponSpec("baby",    "Baby Missile", cost=0,     radius=3, starter_count=-1),
    "missile": WeaponSpec("missile", "Missile",      cost=500,   radius=4, starter_count=5),
    "nuke":    WeaponSpec("nuke",    "Nuke",         cost=10000, radius=8),
    "mirv":    WeaponSpec("mirv",    "MIRV",         cost=5000,  radius=3, effect="mirv"),
    "digger":  WeaponSpec("digger",  "Digger",       cost=1500,  radius=5, effect="digger"),
    "dirt":    WeaponSpec("dirt",    "Dirt Ball",    cost=1000,  radius=4, starter_count=2, effect="dirt"),
    "napalm":  WeaponSpec("napalm",  "Napalm",       cost=4000,  radius=5, effect="napalm"),
}

# Display order for cycling in the TUI.
WEAPON_ORDER: tuple[str, ...] = (
    "baby", "missile", "nuke", "mirv", "digger", "dirt", "napalm",
)


def starter_inventory() -> dict[str, int]:
    """Inventory handed to every tank at match start."""
    inv: dict[str, int] = {}
    for w in WEAPONS.values():
        if w.starter_count != 0:
            # -1 signals "infinite" — we use a huge number so the UI
            # can display '∞' and the engine never runs out.
            inv[w.id] = 9999 if w.starter_count < 0 else w.starter_count
    return inv


def is_infinite(count: int) -> bool:
    return count >= 9000


# ---- data classes ------------------------------------------------------

# Tank colour palette — up to 8 tanks.
TANK_COLOURS: tuple[str, ...] = (
    "rgb(255,80,80)",       # red
    "rgb(90,220,250)",      # cyan
    "rgb(120,230,120)",     # green
    "rgb(240,220,90)",      # yellow
    "rgb(230,120,230)",     # magenta
    "rgb(230,230,230)",     # white
    "rgb(240,170,80)",      # orange
    "rgb(120,160,240)",     # blue
)


@dataclass
class Tank:
    """A single tank."""
    slot: int                    # 0..N-1 display order / colour slot
    x: int                       # column
    owner: str                   # "human" | "ai:moron" | "ai:lame" | "ai:poor" | "ai:good"
    name: str                    # display name
    angle: int = ANGLE_DEFAULT
    power: int = POWER_DEFAULT
    health: int = 100
    gold: int = 0
    kills: int = 0
    weapons: dict[str, int] = field(default_factory=starter_inventory)
    selected_weapon: str = "baby"
    # Fall animation state — >0 means "in the middle of a fall".
    falling: bool = False
    _fall_y: float = 0.0
    # Turn accounting:
    last_round_wins: int = 0

    @property
    def alive(self) -> bool:
        return self.health > 0

    @property
    def colour(self) -> str:
        return TANK_COLOURS[self.slot % len(TANK_COLOURS)]

    @property
    def is_ai(self) -> bool:
        return self.owner.startswith("ai:")

    @property
    def difficulty(self) -> str:
        """'moron'|'lame'|'poor'|'good' or '' for humans."""
        if not self.owner.startswith("ai:"):
            return ""
        return self.owner[3:]


@dataclass
class Projectile:
    x: float
    y: float
    vx: float
    vy: float
    weapon_id: str
    owner_slot: int                    # tank slot that fired
    trail: list[tuple[int, int]] = field(default_factory=list)
    alive: bool = True
    # MIRV split tracking — has this parent already split?
    split_done: bool = False
    apex_y: float | None = None        # min y seen — used for MIRV split

    def cell(self) -> tuple[int, int]:
        return int(round(self.x)), int(round(self.y))


@dataclass
class Explosion:
    """Animation-only explosion. Carving terrain + damage happens on the
    frame the explosion is BORN, not during its lifetime — this matches
    the original: instant hit, lingering flash."""
    x: int
    y: int
    radius: int
    age: int = 0
    lifetime: int = 14

    @property
    def dead(self) -> bool:
        return self.age >= self.lifetime


@dataclass
class Notification:
    kind: str
    text: str


# ---- engine ------------------------------------------------------------


class Engine:
    """Scorched Earth game state + turn/tick loop.

    Events:
      * 'tick' ()
      * 'turn_start' (tank)
      * 'fire' (tank, projectile)
      * 'projectile_impact' (projectile, cx, cy)  — last cell before detonation
      * 'explosion' (Explosion)
      * 'tank_hit' (tank, damage)
      * 'tank_killed' (tank, killer_slot_or_-1)
      * 'mirv_split' (projectile, children)
      * 'round_over' (winner_slot_or_-1)    winner=-1 means draw
      * 'match_over' (winner_slot_or_-1)
      * 'notify' (Notification)
    """

    def __init__(
        self,
        tanks: list[Tank] | None = None,
        *,
        seed: int | None = None,
        total_rounds: int = 3,
    ) -> None:
        self.rng = random.Random(seed)
        self.tick_count = 0
        self.phase: Phase = "IDLE"
        self.paused = False

        # Match state.
        self.total_rounds = total_rounds
        self.round_no = 1
        self.match_winner: int | None = None

        # Wind for the current turn. Roll at every turn start.
        self.wind_strength = 0

        # Dirt gravity progress flag — we run a single-pass settle in the
        # same tick as the explosion; no per-tick dirt animation needed.
        # Retained for future expansion.

        # Tanks + active turn.
        if tanks is None:
            # 1 human + 3 AI bots (spec default).
            tanks = default_match_tanks(3, self.rng)
        self.tanks: list[Tank] = tanks
        self._turn_order: list[int] = list(range(len(self.tanks)))
        self._turn_idx = -1  # advanced to 0 in start_match below
        self.current_tank_slot: int = 0

        # Terrain + entities.
        self.terrain: list[int] = _generate_terrain(self.rng)
        self.projectiles: list[Projectile] = []
        self.explosions: list[Explosion] = []

        # Pub/sub
        self._subs: dict[str, list[Callable[..., None]]] = {}
        self.log: list[Notification] = []

        # AI turn pending flag — the App inspects this and runs
        # ``run_ai_turn()`` after a short delay so the human can see it
        # happen.
        self.ai_pending: bool = False

        # Kick off round 1 / first turn.
        self._place_tanks_on_terrain()
        self._start_turn(first=True)

    # --- pub/sub --------------------------------------------------------

    def on(self, event: str, cb: Callable[..., None]) -> None:
        self._subs.setdefault(event, []).append(cb)

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        for cb in list(self._subs.get(event, ())):
            cb(*args, **kwargs)

    def notify(self, kind: str, text: str) -> None:
        n = Notification(kind, text)
        self.log.append(n)
        if len(self.log) > 200:
            del self.log[:50]
        self.emit("notify", n)

    # --- input ---------------------------------------------------------

    @property
    def current_tank(self) -> Tank | None:
        if 0 <= self.current_tank_slot < len(self.tanks):
            return self.tanks[self.current_tank_slot]
        return None

    def _can_control(self) -> bool:
        t = self.current_tank
        if t is None or not t.alive:
            return False
        if t.is_ai:
            return False
        return self.phase == "IDLE" and not self.paused

    def adjust_angle(self, delta: int) -> None:
        if not self._can_control():
            return
        t = self.current_tank
        assert t is not None
        t.angle = max(ANGLE_MIN, min(ANGLE_MAX, t.angle + delta))

    def adjust_power(self, delta: int) -> None:
        if not self._can_control():
            return
        t = self.current_tank
        assert t is not None
        t.power = max(POWER_MIN, min(POWER_MAX, t.power + delta))

    def cycle_weapon(self, step: int = 1) -> None:
        if not self._can_control():
            return
        t = self.current_tank
        assert t is not None
        # Restrict to owned weapons only.
        owned = [w for w in WEAPON_ORDER if t.weapons.get(w, 0) > 0]
        if not owned:
            t.selected_weapon = "baby"
            return
        try:
            i = owned.index(t.selected_weapon)
        except ValueError:
            i = 0
        i = (i + step) % len(owned)
        t.selected_weapon = owned[i]

    def select_weapon(self, weapon_id: str) -> bool:
        if not self._can_control():
            return False
        t = self.current_tank
        assert t is not None
        if t.weapons.get(weapon_id, 0) <= 0:
            return False
        t.selected_weapon = weapon_id
        return True

    def fire(self) -> bool:
        """Fire the current tank's selected weapon. Returns True iff a
        projectile was launched. Works for humans AND AI — AI calls this
        from `run_ai_turn`."""
        if self.phase != "IDLE" or self.paused:
            return False
        t = self.current_tank
        if t is None or not t.alive:
            return False
        if t.weapons.get(t.selected_weapon, 0) <= 0:
            # Fall back to free baby missile if inventory is empty.
            if t.weapons.get("baby", 0) <= 0:
                t.weapons["baby"] = 9999
            t.selected_weapon = "baby"
        # Decrement count — infinite (9999+) stays.
        cnt = t.weapons.get(t.selected_weapon, 0)
        if not is_infinite(cnt):
            t.weapons[t.selected_weapon] = cnt - 1
        # Compute initial velocity. Angle semantics (classic SE):
        #   0°   = pointing left (-x)
        #   90°  = straight up  (-y)
        #   180° = pointing right (+x)
        #   So vx = -cos(angle) (sign flipped so 0° → -x),
        #   vy = -sin(angle) (negative because y grows down).
        speed = t.power / POWER_TO_VEL
        a = math.radians(t.angle)
        vx = -math.cos(a) * speed
        vy = -math.sin(a) * speed
        # Muzzle position — just above the tank centre.
        mx = float(t.x)
        my = float(self.terrain[t.x] - 2)
        proj = Projectile(
            x=mx, y=my,
            vx=vx, vy=vy,
            weapon_id=t.selected_weapon,
            owner_slot=t.slot,
        )
        self.projectiles.append(proj)
        self.phase = "FLYING"
        self.notify(
            "fire",
            f"{t.name} fires {WEAPONS[t.selected_weapon].name} "
            f"(angle {t.angle}°, power {t.power})"
        )
        self.emit("fire", t, proj)
        return True

    def toggle_pause(self) -> bool:
        if self.phase == "GAME_OVER":
            return False
        self.paused = not self.paused
        return self.paused

    # --- tick ----------------------------------------------------------

    def tick(self) -> None:
        if self.paused or self.phase in ("IDLE", "GAME_OVER", "ROUND_OVER"):
            self.emit("tick")
            return
        self.tick_count += 1
        self._step_projectiles()
        self._step_explosions()
        # After all projectiles resolve, decide if the turn is complete.
        if not self.projectiles and not self.explosions and self.phase == "FLYING":
            self.phase = "SETTLING"
        if self.phase == "SETTLING" and not self.explosions:
            # Drop any tanks whose terrain got eaten out from under them.
            if not self._settle_tanks():
                self._end_turn()
        self.emit("tick")

    # --- projectile step ------------------------------------------------

    def _step_projectiles(self) -> None:
        """Advance every live projectile by one frame. Resolve impacts."""
        wind_ax = self.wind_strength * WIND_ACCEL_PER_UNIT
        # Iterate over a snapshot — MIRV split appends to the list mid-loop
        # and we don't want to step newly-born children on the same tick.
        for p in list(self.projectiles):
            if not p.alive:
                continue
            p.vy += GRAVITY
            p.vx += wind_ax
            p.x += p.vx
            p.y += p.vy
            # Track apex for MIRV split trigger.
            if p.apex_y is None or p.y < p.apex_y:
                p.apex_y = p.y
            # Trail.
            cell = p.cell()
            if not p.trail or p.trail[-1] != cell:
                p.trail.append(cell)
                if len(p.trail) > 80:
                    del p.trail[:20]

            # MIRV: split on the way back DOWN after reaching apex.
            if (p.weapon_id == "mirv" and not p.split_done
                    and p.apex_y is not None
                    and p.vy > 0.5  # descending with momentum
                    and p.y > p.apex_y + 1.0):
                self._mirv_split(p)
                continue

            # Off-field horizontally: kill the projectile quietly.
            cx, cy = p.cell()
            if cx < 0 or cx >= FIELD_W:
                p.alive = False
                continue
            # Fell off bottom somehow (shouldn't with terrain spanning
            # the floor — but guard anyway).
            if cy >= FIELD_H:
                p.alive = False
                continue
            # Hit ground?
            if cy >= self.terrain[cx]:
                # Clamp to surface for the impact point.
                impact_x = cx
                impact_y = max(0, self.terrain[cx])
                p.alive = False
                self.emit("projectile_impact", p, impact_x, impact_y)
                self._detonate(p, impact_x, impact_y)
                continue

        # Reap dead projectiles.
        self.projectiles = [p for p in self.projectiles if p.alive]

    def _mirv_split(self, parent: Projectile) -> None:
        parent.split_done = True
        parent.alive = False
        children: list[Projectile] = []
        speed = math.hypot(parent.vx, parent.vy)
        # Spread 3 warheads at angles roughly in a 40° fan about the
        # parent's heading.
        heading = math.atan2(parent.vy, parent.vx)
        for off in (-0.35, 0.0, 0.35):
            a = heading + off
            c = Projectile(
                x=parent.x, y=parent.y,
                vx=math.cos(a) * speed,
                vy=math.sin(a) * speed,
                weapon_id="baby",          # warheads are small explosions
                owner_slot=parent.owner_slot,
            )
            # Force these to behave like MIRV warheads (small radius) —
            # we tag via special weapon "mirv_warhead".
            c.weapon_id = "mirv_warhead"
            children.append(c)
        self.projectiles.extend(children)
        self.notify("mirv", "MIRV splits!")
        self.emit("mirv_split", parent, children)

    # --- detonation ---------------------------------------------------

    def _detonate(self, p: Projectile, cx: int, cy: int) -> None:
        """Resolve a projectile's impact at (cx, cy)."""
        weapon = p.weapon_id
        if weapon == "mirv_warhead":
            radius = 3
            self._blast(cx, cy, radius, p.owner_slot)
            return
        spec = WEAPONS.get(weapon)
        if spec is None:
            self._blast(cx, cy, 3, p.owner_slot)
            return
        if spec.effect == "dirt":
            self._dirt_bomb(cx, cy, spec.radius)
            # A little flash without carving terrain / damage.
            self.explosions.append(Explosion(x=cx, y=cy, radius=1))
            self.emit("explosion", self.explosions[-1])
            return
        if spec.effect == "digger":
            self._digger(cx, cy, spec.radius, p.owner_slot)
            return
        if spec.effect == "napalm":
            # v1 simplification — model napalm as a medium blast with a
            # bonus damage bump.
            self._blast(cx, cy, spec.radius, p.owner_slot,
                        damage_bonus=15)
            return
        # Default — plain blast.
        self._blast(cx, cy, spec.radius, p.owner_slot)

    def _blast(self, cx: int, cy: int, radius: int, owner_slot: int,
               damage_bonus: int = 0) -> None:
        """Standard explosion: carve terrain + damage tanks + flash."""
        self._carve_terrain(cx, cy, radius)
        self._damage_tanks(cx, cy, radius, owner_slot, damage_bonus)
        ex = Explosion(x=cx, y=cy, radius=radius)
        self.explosions.append(ex)
        self.emit("explosion", ex)

    def _digger(self, cx: int, cy: int, radius: int, owner_slot: int) -> None:
        """Digger carves a vertical shaft down from impact."""
        # Carve a cylinder: every column in [cx-radius/2..cx+radius/2]
        # has terrain lowered to the field floor (or deep).
        half = radius // 2
        for x in range(max(0, cx - half), min(FIELD_W, cx + half + 1)):
            self.terrain[x] = min(FIELD_H, self.terrain[x] + radius + 2)
        # Very mild damage to surface tanks in the shaft.
        self._damage_tanks(cx, cy, radius, owner_slot, damage_bonus=-20)
        ex = Explosion(x=cx, y=cy, radius=max(2, radius // 2))
        self.explosions.append(ex)
        self.emit("explosion", ex)

    def _dirt_bomb(self, cx: int, cy: int, radius: int) -> None:
        """Add a dome of terrain at (cx, cy)."""
        for x in range(max(0, cx - radius), min(FIELD_W, cx + radius + 1)):
            dx = x - cx
            cap = int(round(math.sqrt(max(0, radius * radius - dx * dx))))
            if cap <= 0:
                continue
            new_top = max(SKY_MIN - 1, cy - cap)
            self.terrain[x] = min(self.terrain[x], new_top)

    def _carve_terrain(self, cx: int, cy: int, radius: int) -> None:
        """Remove a disc of terrain centred at (cx, cy) of given radius."""
        for x in range(max(0, cx - radius), min(FIELD_W, cx + radius + 1)):
            dx = x - cx
            depth = int(round(math.sqrt(max(0, radius * radius - dx * dx))))
            if depth <= 0:
                continue
            # Raise terrain to the deepest point in this column that the
            # blast sphere reaches — anything above it is removed.
            top_after = max(self.terrain[x], cy + depth)
            # Clamp to field bottom; a bottomless column reads as FIELD_H.
            self.terrain[x] = min(FIELD_H, top_after)

    def _damage_tanks(self, cx: int, cy: int, radius: int,
                      owner_slot: int, damage_bonus: int = 0) -> None:
        """Apply falloff damage to tanks near the blast."""
        for t in self.tanks:
            if not t.alive:
                continue
            tx = t.x
            ty = self.terrain[tx] - 1
            dist = math.hypot(tx - cx, ty - cy)
            if dist > radius:
                continue
            # Linear falloff: at dist=0 -> max; at dist=radius -> 0.
            base = int(EXPLOSION_DAMAGE_MAX * (1.0 - dist / max(1, radius)))
            dmg = max(0, base + damage_bonus)
            if dmg <= 0:
                continue
            self._hit_tank(t, dmg, owner_slot)

    def _hit_tank(self, t: Tank, damage: int, owner_slot: int) -> None:
        before = t.health
        t.health = max(0, t.health - damage)
        self.emit("tank_hit", t, damage)
        self.notify("hit",
                    f"{t.name} takes {damage} damage (hp {before}→{t.health})")
        if t.health <= 0 and before > 0:
            killer = owner_slot if owner_slot != t.slot else -1
            if killer >= 0 and 0 <= killer < len(self.tanks):
                self.tanks[killer].kills += 1
            self.notify("killed", f"{t.name} destroyed!")
            self.emit("tank_killed", t, killer)

    # --- explosion step (animation-only) -------------------------------

    def _step_explosions(self) -> None:
        for ex in self.explosions:
            ex.age += 1
        self.explosions = [e for e in self.explosions if not e.dead]

    # --- tank settle --------------------------------------------------

    def _settle_tanks(self) -> bool:
        """Make any tanks whose terrain was excavated fall. Returns True
        if any tank is still animating a fall.

        v1 simplification — we snap tanks to new terrain instantly and
        apply fall damage. A proper animation would step 1 cell/tick but
        that adds state we don't need for gameplay.
        """
        any_fell = False
        for t in self.tanks:
            if not t.alive:
                continue
            surface = self.terrain[t.x] - 1
            cur_y = self.terrain[t.x] - 1  # where the tank IS (tracked implicitly by terrain)
            # We stored initial tank y via terrain, so "falling" is when
            # the surface moved DOWN (increased y / numerically larger).
            # We don't actually store an old y — we use the fact that
            # terrain was just modified. Instead, check if the tank is
            # ever airborne (terrain[x] much lower than field mid): use
            # fall_distance = change since last settle check, stored
            # implicitly by comparing to a cached "last known surface".
            if not hasattr(t, "_last_surface"):
                t._last_surface = surface  # type: ignore[attr-defined]
            last = getattr(t, "_last_surface")
            if surface > last:
                fall = surface - last
                extra = max(0, fall - 2)
                if extra > 0:
                    dmg = extra * FALL_DAMAGE_PER_CELL
                    self._hit_tank(t, dmg, -1)
                    any_fell = True
                t._last_surface = surface  # type: ignore[attr-defined]
        return any_fell

    # --- turn control --------------------------------------------------

    def _place_tanks_on_terrain(self) -> None:
        """Assign each tank a column + cache the initial terrain surface."""
        # Space tanks evenly across the field with a small jitter.
        n = len(self.tanks)
        if n == 0:
            return
        gap = FIELD_W // (n + 1)
        used: set[int] = set()
        for i, t in enumerate(self.tanks):
            base_x = gap * (i + 1)
            jitter = self.rng.randint(-2, 2)
            x = max(2, min(FIELD_W - 3, base_x + jitter))
            # Avoid overlapping columns.
            while x in used:
                x += 1
            used.add(x)
            t.x = x
            t._last_surface = self.terrain[x] - 1  # type: ignore[attr-defined]

    def _roll_wind(self) -> None:
        self.wind_strength = self.rng.randint(
            -WIND_STRENGTH_MAX, WIND_STRENGTH_MAX
        )

    def _start_turn(self, *, first: bool = False) -> None:
        self._roll_wind()
        if first:
            # Pick first alive tank, wrapping.
            self._turn_idx = -1
        # Advance to the next alive tank.
        N = len(self.tanks)
        for _ in range(N + 1):
            self._turn_idx = (self._turn_idx + 1) % N
            t = self.tanks[self._turn_idx]
            if t.alive:
                break
        self.current_tank_slot = self._turn_idx
        tank = self.tanks[self.current_tank_slot]
        self.phase = "IDLE"
        self.notify("turn", f"{tank.name}'s turn")
        self.emit("turn_start", tank)
        if tank.is_ai:
            self.ai_pending = True

    def _end_turn(self) -> None:
        # Check round-end: one alive tank left OR all dead.
        alive = [t for t in self.tanks if t.alive]
        if len(alive) <= 1:
            winner = alive[0].slot if alive else -1
            self._end_round(winner)
            return
        self._start_turn()

    def _end_round(self, winner_slot: int) -> None:
        self.phase = "ROUND_OVER"
        if winner_slot >= 0:
            t = self.tanks[winner_slot]
            t.last_round_wins += 1
            payout = 1000 + 500 * t.kills
            t.gold += payout
            self.notify(
                "round_over",
                f"Round {self.round_no}: {t.name} wins! +{payout} gold"
            )
        else:
            self.notify("round_over", f"Round {self.round_no}: draw!")
        # Living bonus for any survivor too.
        for t in self.tanks:
            if t.alive:
                t.gold += 100 * self.round_no
        self.emit("round_over", winner_slot)
        if self.round_no >= self.total_rounds:
            self._end_match()

    def start_next_round(self) -> None:
        """Called by the App after the shop closes."""
        if self.phase == "GAME_OVER":
            return
        self.round_no += 1
        # Restore all tanks to 100hp, reset kills, keep weapons/gold.
        for t in self.tanks:
            t.health = 100
            t.kills = 0
            t.angle = ANGLE_DEFAULT
            t.power = POWER_DEFAULT
            # Ensure baby missile still available.
            t.weapons.setdefault("baby", 9999)
            if t.weapons.get("baby", 0) < 1:
                t.weapons["baby"] = 9999
        self.terrain = _generate_terrain(self.rng)
        self.projectiles = []
        self.explosions = []
        self._place_tanks_on_terrain()
        self._turn_idx = -1
        self._start_turn(first=True)

    def _end_match(self) -> None:
        # Winner: highest gold + kills combined.
        scores = [
            (t.gold + t.kills * 500, t.slot) for t in self.tanks
        ]
        scores.sort(reverse=True)
        winner = scores[0][1] if scores else -1
        self.match_winner = winner
        self.phase = "GAME_OVER"
        if winner >= 0:
            self.notify("match_over",
                        f"Match over: {self.tanks[winner].name} wins!")
        else:
            self.notify("match_over", "Match over: no winner")
        self.emit("match_over", winner)

    # --- AI turn entry point ------------------------------------------

    def run_ai_turn(self) -> None:
        """The App drives this after a short delay so the AI move is
        visible rather than instantaneous. Mutates `angle/power/weapon`
        then calls ``fire()``."""
        if not self.ai_pending:
            return
        t = self.current_tank
        if t is None or not t.is_ai or not t.alive:
            self.ai_pending = False
            return
        # Late-bound import to avoid circular refs.
        from . import ai as ai_mod
        weapon, angle, power = ai_mod.pick_move(self, t)
        t.selected_weapon = weapon if t.weapons.get(weapon, 0) > 0 else "baby"
        t.angle = max(ANGLE_MIN, min(ANGLE_MAX, int(angle)))
        t.power = max(POWER_MIN, min(POWER_MAX, int(power)))
        self.ai_pending = False
        self.fire()

    # --- restart -------------------------------------------------------

    def restart(self) -> None:
        """Fresh match — keep player identities/difficulties, reset gold,
        health, weapons, terrain."""
        for t in self.tanks:
            t.health = 100
            t.gold = 0
            t.kills = 0
            t.last_round_wins = 0
            t.angle = ANGLE_DEFAULT
            t.power = POWER_DEFAULT
            t.weapons = starter_inventory()
            t.selected_weapon = "baby"
        self.round_no = 1
        self.match_winner = None
        self.phase = "IDLE"
        self.terrain = _generate_terrain(self.rng)
        self.projectiles = []
        self.explosions = []
        self._place_tanks_on_terrain()
        self._turn_idx = -1
        self._start_turn(first=True)

    # --- snapshot -----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "tick": self.tick_count,
            "phase": self.phase,
            "round_no": self.round_no,
            "total_rounds": self.total_rounds,
            "current_tank": self.current_tank_slot,
            "wind": self.wind_strength,
            "tanks_alive": sum(1 for t in self.tanks if t.alive),
            "total_tanks": len(self.tanks),
            "projectiles_in_flight": len(self.projectiles),
            "explosions_active": len(self.explosions),
            "paused": self.paused,
            "match_winner": self.match_winner,
        }


# ---- terrain generator -------------------------------------------------


def _generate_terrain(rng: random.Random) -> list[int]:
    """Procedural hills — sum of 3-5 sine waves clamped to
    [SKY_MIN, SKY_MAX]."""
    n_waves = rng.randint(3, 5)
    components: list[tuple[float, float, float]] = []
    for _ in range(n_waves):
        amp = rng.uniform(1.5, 4.0)
        freq = rng.uniform(0.04, 0.14)
        phase = rng.uniform(0, 2 * math.pi)
        components.append((amp, freq, phase))
    base = (SKY_MIN + SKY_MAX) / 2
    terrain: list[int] = []
    for x in range(FIELD_W):
        h = base
        for amp, freq, phase in components:
            h += amp * math.sin(freq * x + phase)
        terrain.append(int(max(SKY_MIN, min(SKY_MAX, round(h)))))
    return terrain


# ---- match setup -------------------------------------------------------


DEFAULT_NAMES: tuple[str, ...] = (
    "Red", "Cyan", "Green", "Yellow", "Magenta", "White", "Orange", "Blue",
)


def default_match_tanks(n_ai: int, rng: random.Random) -> list[Tank]:
    """Default match: 1 human + n_ai bots, assorted difficulties.

    n_ai may be 0..7; we clamp total to 2..8.
    """
    n_ai = max(1, min(7, n_ai))
    tanks: list[Tank] = []
    tanks.append(Tank(slot=0, x=10, owner="human", name="You"))
    difficulties = ["ai:moron", "ai:lame", "ai:poor", "ai:good"]
    for i in range(n_ai):
        diff = difficulties[min(i, len(difficulties) - 1)]
        tanks.append(
            Tank(
                slot=i + 1,
                x=30 + 10 * i,
                owner=diff,
                name=DEFAULT_NAMES[(i + 1) % len(DEFAULT_NAMES)],
            )
        )
    return tanks
