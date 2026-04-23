# scorched-earth-tui — Design Decisions

Clean-room terminal port of Wendell Hicken's 1991 turn-based
artillery game **Scorched Earth**. Records the judgment calls
made during construction so future readers understand why this
diverges from the simcity-tui / missile-command-tui / crimson-
fields-tui reference layouts.

## 1. Licensing — clean-room reimplementation, no vendored assets

Wendell Hicken's *Scorched Earth* (1991) is shareware but all IP
is retained by the author. The VGA PCX assets, sound effects, and
binary have never been open-sourced. We do not vendor any
commercial assets (sprites, WAVs, the binary) from the original
PC release or any subsequent port.

**Approach:** clean-room reconstruction from publicly documented
design — the in-game help pages, the shareware manual
(scorch.txt), the `MobyGames` + `Wikipedia` feature summaries,
and long-form community write-ups on the weapon pool and AI
difficulty curve. All consulted as *design reference, not code*.
The terrain heightmap, tank physics, projectile integration,
explosion carving, dirt gravity, wind model, weapon pool, AI
targeting, and shop economy are **re-derived** — we make a game
in the *shape* of Scorched Earth the way missile-command-tui is
in the shape of Missile Command.

Where the original design has become cultural shorthand (2-8
tanks, hilly destructible terrain, wind arrow, turret angle,
power slider, Moron/Lame/Poor/Good AI names, iconic weapons
like baby missile / nuke / MIRV / digger / dirt ball / napalm),
we keep the beats because they're the iconic reference, but the
implementation — every character in this repo — is our own.

**Consequence:** no `vendor/` binary. `make bootstrap` is a
no-op with a note. License: MIT. A `NOTICE` file credits
Wendell Hicken as the designer of the original game.

## 2. Engine model — native Python, turn-driven (Skill binding-strategy pattern 4)

No SWIG, no subprocess, no external engine. A turn-based
artillery game has a tiny state space — a heightmap, a handful
of tanks, at most a handful of in-flight projectiles — so a pure
Python implementation keeps snapshots, determinism, and QA
trivial. This follows the tui-game-build skill's §2 binding-
strategy pattern 4 — "clean-room Python reimplementation".

**Core loop is NOT tick-driven in the RTS sense.** Scorched
Earth is turn-based: one tank aims and fires, the projectile
animates at ~25 Hz, dirt settles, the next tank takes its turn.
The engine exposes:

  * `start_turn()` — the current tank picks a weapon, adjusts
    angle/power. Simulation is "idle".
  * `fire()` — spawns a `Projectile`; state becomes `FLYING`.
  * `tick()` — called ~40 ms while `FLYING` to advance the
    projectile(s), apply gravity, resolve impacts, carve
    terrain, settle dirt, damage tanks.
  * When no projectiles remain and dirt has settled, engine
    transitions to the next tank's turn.
  * When only one player's tanks remain alive, the round ends.

Animation cadence: 25 Hz (40 ms/tick). Projectile speed chosen
so a full-power shot crosses the screen in 1-2 seconds.

## 3. Playfield — 80×30 destructible heightmap

Classic 2D side-view:

  * Playfield 80 columns × 30 rows.
  * `terrain[x]` stores the ground height at column x (how
    many rows from the top are sky). Rows [0..terrain[x]-1]
    are sky; [terrain[x]..29] are ground.
  * **Destructible.** An explosion centred at (cx, cy) with
    radius r carves a disc out of the ground by raising
    `terrain[x]` to max(terrain[x], cy + sqrt(r^2 - (x-cx)^2))
    for each x in the blast radius — i.e. dirt removed becomes
    sky. We then run a gravity pass: any floating ground cell
    (sky below it) falls. Implementation is simpler than the
    original's per-pixel fall: we just recompute terrain[x] as
    the highest ground cell remaining in that column after
    carving.
  * **Procedural hills.** We build `terrain[]` by summing 3-5
    sine waves of random frequency/phase/amplitude plus a
    base line, then clamping to [8, 26]. Gives the
    recognisable rolling-hills shape without letting a tank
    spawn at the very top or very bottom.

## 4. Tanks — 2-8 players, turret angle, power, health

Classic SE layout:

  * 2-8 tanks (human + AI fill). Default is 1 human + 3 AI.
  * Each tank has: position `x` (column), `y` (derived from
    terrain), `angle` (0..180°, 90° is straight up, 0° is
    pointing left, 180° is pointing right — classic SE
    convention), `power` (0..1000 where 1000 ≈ full-speed
    across the screen), `health` (0..100, 100 starts),
    `gold`, a `weapons` inventory dict `{weapon_id: count}`,
    and an `owner` tag: "human" or "ai:<difficulty>".
  * Tanks spawn with x-positions spaced evenly across the
    playfield (±2 column jitter) so they don't overlap.
  * **Sits on top of terrain.** A tank's y is always
    `terrain[x] - 1` (one row of sky above the ground). If
    terrain under it changes (explosion hollows the column),
    the tank falls — after a short animation delay — and
    takes `max(0, fall_distance - 2)` damage per cell
    beyond 2 (generous; SE was harsher).
  * **Death at 0 HP.** Dead tanks leave a wreck sprite on the
    ground; they don't block projectiles.

## 5. Turns — weapon, angle, power, fire

Turn loop:

  1. Engine picks the next alive tank in a fixed round-robin
     order. If it's an AI, `ai.take_turn()` populates the
     angle/power/weapon immediately. If it's a human, we
     wait for input.
  2. Player adjusts angle with `← →` (±1° per tap; hold
     Shift for ±5°), power with `↑ ↓` (±25 per tap; ±100
     with Shift), cycles weapons with `w`/`W` (or picks via
     the `e` equipment modal). Display overlays the tank's
     turret line from the turret centre out at `angle`, in
     dim yellow.
  3. `space` fires. Engine spawns a `Projectile` with the
     tank's position, velocity derived from angle + power,
     and a weapon type tag. State → FLYING.
  4. Tick loop advances the projectile: `vx += 0` (no
     horizontal drag; wind adds a small constant accel
     instead), `vy += GRAVITY`, `vx += WIND_ACCEL`, `x += vx`,
     `y += vy`. Record the trail cell.
  5. Projectile hits when it enters a ground cell or leaves
     the playfield horizontally. Weapon-specific detonation:
     most weapons spawn an `Explosion(x, y, radius)`; a
     `MIRV` splits into 3 sub-projectiles at the weapon's
     apex (highest point of its arc); a `DIRT` bomb
     **adds** terrain instead of removing it.
  6. Explosion step carves terrain, damages nearby tanks
     (linear falloff with distance), triggers wrecks.
  7. When all projectiles + explosions are gone and dirt
     has settled, switch turn.

## 6. Projectile physics

  * `GRAVITY = 0.08` cells/tick² (downward in our y-down
    coord system, so +0.08).
  * `WIND_ACCEL = wind_strength * 0.002` cells/tick²
    (horizontal, signed; wind_strength in [-10..+10] at the
    start of each turn, drawn uniformly).
  * Muzzle velocity from `power` (0..1000): velocity
    magnitude is `power / 100` cells/tick. At power=1000 a
    projectile launched at 45° crosses ~80 columns before
    landing — full-field range.
  * Trail stored as a list of integer cells for rendering;
    capped at 80 to avoid unbounded growth.

## 7. Weapons pool

At least 6 weapons in v1; shop expands the list later.

| id | name | cost | radius | notes |
|---|---|---|---|---|
| `baby`    | Baby Missile | free  | 3 | unlimited, starter |
| `missile` | Missile      | 500   | 4 | small upgrade |
| `nuke`    | Nuke         | 10000 | 8 | big crater |
| `mirv`    | MIRV         | 5000  | 3 each | splits into 3 warheads at apex |
| `digger`  | Digger       | 1500  | 5 | carves a vertical shaft on impact |
| `dirt`    | Dirt Ball    | 1000  | 4 | **adds** terrain — strategic |
| `napalm`  | Napalm       | 4000  | 5 | burns for 3 ticks, damage-over-time |

Starter tanks get `{baby: ∞, missile: 5, dirt: 2}`.

## 8. Wind

Changes **each turn** (not each round). Drawn uniformly from
[-10..+10]. Displayed as an arrow `◀━━━` or `━━━▶` in the
status panel, length proportional to magnitude. Wind applies
a constant horizontal acceleration to all in-flight
projectiles — it's a force, not a velocity — so longer shots
drift more, as in the original.

## 9. AI — 4 difficulty levels

Classic SE names: Moron, Lame, Poor, Good.

  * **Moron**  random angle (0-180°), random power (200-900),
    random weapon.
  * **Lame**   random weapon; angle biased toward the nearest
    enemy (±30° jitter); power biased by rough distance.
  * **Poor**   computes a ballistic solution (angle + power)
    for the nearest enemy assuming no wind, then adds ±15°
    angle / ±100 power jitter. Picks best weapon it owns for
    the situation (nuke if cornered, digger if blocked).
  * **Good**   full ballistic solution *with* wind. Samples a
    few candidate angles and picks the one whose simulated
    trajectory hits closest to the target. Picks weapon
    sensibly. Jitter is ±5° / ±40 power.

Implementation: `ai.take_turn(engine, tank)` returns
`(weapon_id, angle, power)` given the current state.

## 10. Rounds, shop, gold

One **round** = all tanks fight until one survives (or all
dead = draw). Winners earn `1000 + 500 × kills` gold.
Survivors also get `100 × rounds_played` for living.

**Shop.** Between rounds we push a `ShopScreen` modal. Player
can spend gold buying quantities of weapons. AI tanks
auto-buy greedily (Good AI: prioritise nukes + MIRV; Lame
just buys the cheapest it can afford). After shopping, start
a new round: regenerate terrain, respawn tanks at fresh
positions, roll fresh wind.

Default match is `3` rounds; game-over modal shows overall
winner.

## 11. Keyboard bindings

| key | action |
|---|---|
| `← →` | turret angle ∓1° (Shift: ±5°) |
| `↑ ↓` | power ±25 (Shift: ±100) |
| `w` / `W` | next/prev weapon |
| `e` | open equipment / weapon picker modal |
| `space` / `f` | fire |
| `?` | help overlay |
| `p` / `escape` | pause |
| `s` | toggle synth sounds |
| `n` | new game (at ROUND OVER / GAME OVER) |
| `q` | quit |

Angle/power/weapon bindings are `priority=True` on the App so
they don't get eaten by scrollable panels; modal screens use
non-conflicting keys (`+`/`-`, letters) per the skill gotcha.

## 12. Persistence

Stored in `$XDG_DATA_HOME/scorched-earth-tui/state.json`:

    {
      "high_score":   123450,
      "sound_enabled": false
    }

High score is the best per-player (human) single-match total
gold + kill credit. No mid-match save — you play a match to
completion.

## 13. Stage-7 phases implemented vs skipped

Following the `tui-game-build` skill's phased polish. For v1
we are landing:

  * **Phase A (UI beauty)** — distinct colours per weapon
    type, per-tank palette (red/cyan/green/yellow/magenta/
    white/orange/blue), dim-yellow turret line, bold wind
    arrow in the status rail, procedurally-varied terrain
    shades (dark-green top, darker-brown interior), night-
    sky background with sparse stars.
  * **Phase D (sound)** — synth tones for fire / explode /
    tank-hit / tank-killed / round-over. Off by default; `s`
    toggles; `SE_TUI_SOUND=1` pre-enables. Debounce 80 ms.
  * **Phase E (save/load + stats)** — high-score
    persistence, HelpOverlay, PauseScreen, ShopScreen,
    GameOverScreen, RoundOverScreen modals.
  * **Phase F (animation)** — explosion rings grow/fade
    frame-by-frame, projectile trail fades, tank turret
    blinks for the current tank.

Skipped with rationale (v1):

  * **Phase B (overlays)** — no overlay data that would help
    (no pollution / demand maps in an artillery game). The
    status rail already shows wind, gold, health, weapons.
  * **Phase C (agent REST API)** — action space is compact
    (`set_angle`, `set_power`, `pick_weapon`, `fire`); if we
    ever bot it, the engine is importable. Open future
    exercise.
  * **Phase G (LLM advisor)** — tactical shooter with
    simple state doesn't benefit from Claude in-the-loop
    for v1. Could become an "AI coach" mode later.

## 14. Reference layout match

Package name `scorched_earth_tui`. Files modeled on
missile-command-tui's tick-driven engine + screens/sounds/
state/sprites split. Tests: `qa.py` with Pilot scenarios,
`perf.py` with bench targets. Sibling projects in
`~/AI/projects/tui-games/` share the layout exactly.
