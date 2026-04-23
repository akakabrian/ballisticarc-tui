"""Perf benchmarks for scorched-earth-tui."""
from __future__ import annotations

import asyncio
import os
import statistics
import tempfile
import time

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="scorched-perf-")

from scorched_earth_tui.app import ScorchedEarthApp  # noqa: E402
from scorched_earth_tui.engine import Engine, Tank  # noqa: E402


def _time_it(fn, iters: int) -> tuple[float, float]:
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return p50, p95


def bench_engine_tick() -> None:
    e = Engine(seed=42)
    # Fire a projectile so tick() has something to chew on.
    e.tanks[0].angle = 90
    e.tanks[0].power = 500
    e.fire()
    p50, p95 = _time_it(e.tick, iters=2000)
    print(f"  engine.tick()         p50={p50:.3f} ms  p95={p95:.3f} ms")


def bench_snapshot() -> None:
    e = Engine(seed=42)
    p50, p95 = _time_it(e.snapshot, iters=2000)
    print(f"  engine.snapshot()     p50={p50:.3f} ms  p95={p95:.3f} ms")


async def bench_render_line() -> None:
    app = ScorchedEarthApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        bv = app.field_view
        h = bv.size.height

        def paint_all_rows() -> None:
            bv._matrix_cache = None
            for y in range(h):
                bv.render_line(y)

        p50, p95 = _time_it(paint_all_rows, iters=60)
        print(f"  full field paint      p50={p50:.3f} ms  p95={p95:.3f} ms "
              f"(height={h})")


def main() -> None:
    print("scorched-earth-tui perf baseline")
    print("-" * 40)
    bench_engine_tick()
    bench_snapshot()
    asyncio.run(bench_render_line())


if __name__ == "__main__":
    main()
