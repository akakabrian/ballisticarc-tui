# scorched-earth-tui

Terminal **Scorched Earth** — clean-room Python/Textual tribute
to Wendell Hicken's 1991 turn-based artillery classic. Part of
the `~/AI/projects/tui-games/` batch; structured to match the
`tui-game-build` skill.

Spawn up to 8 tanks on a procedural hilly heightmap. Adjust
turret angle and muzzle power, account for wind, pick a weapon
(baby missile / nuke / MIRV / digger / dirt ball / napalm), and
lob a shell over a hill at your opponent. Ground is destructible
— carve tunnels, raise cover with dirt balls, watch enemy tanks
fall into craters.

## Quick start

```
make all        # venv + editable install
make run        # play
make test       # QA harness
make perf       # hot-path bench
```

## Keys

| key | action |
|---|---|
| `←` / `→` | turret angle ∓1° (shift: ±5°) |
| `↑` / `↓` | muzzle power ±25 (shift: ±100) |
| `w` / `W` | next / previous weapon |
| `e` | equipment picker |
| `space` / `f` | fire |
| `p` / `escape` | pause |
| `s` | toggle synth sounds |
| `?` | help overlay |
| `n` | new game (at GAME OVER / ROUND OVER) |
| `q` | quit |

## Design

See [DECISIONS.md](./DECISIONS.md) for the full rationale —
licensing posture (clean-room, MIT), engine model (pure Python,
turn-driven with a 25 Hz projectile animation tick), terrain
heightmap + destruction model, tank physics, AI difficulty
ladder (Moron / Lame / Poor / Good), wind, weapon pool, shop
economy, and which tui-game-build phases were landed vs skipped.

## Licensing

MIT — our code is original. `NOTICE` credits Wendell Hicken as
the designer of the original *Scorched Earth*; no commercial
assets are bundled.
