"""Optional synth sounds for ballisticarc-tui.

Same pattern as missile-command-tui / space-invaders-tui: stdlib
``wave`` synth, fire-and-forget ``paplay`` / ``aplay`` / ``afplay``,
per-sound 80 ms debounce.  Off by default; `s` at runtime or
`SE_TUI_SOUND=1` pre-enables.
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Callable


_PLAYER: str | None = None
for _cmd in ("paplay", "aplay", "afplay"):
    if shutil.which(_cmd):
        _PLAYER = _cmd
        break


def _runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    d = Path(base) / "ballisticarc-tui-sounds"
    d.mkdir(parents=True, exist_ok=True)
    return d


_TONES: dict[str, tuple[float, float, float, str]] = {
    "fire":      (520.0, 0.120, 0.26, "sweep_up"),
    "explode":   (140.0, 0.360, 0.32, "noise"),
    "hit":       (260.0, 0.140, 0.28, "sweep_down"),
    "killed":    (90.0,  0.500, 0.38, "sweep_down"),
    "round":     (620.0, 0.320, 0.28, "sweep_up"),
    "game_over": (100.0, 0.700, 0.38, "sweep_down"),
    "turn":      (700.0, 0.060, 0.20, "sine"),
}


def _synthesise(path: Path, freq: float, dur: float, amp: float, kind: str) -> None:
    sr = 22_050
    n = int(sr * dur)
    attack = int(sr * 0.008)
    release = int(sr * 0.020)
    frames = bytearray()
    rng_state = 0x12345
    for i in range(n):
        env = 1.0
        if i < attack:
            env = i / max(1, attack)
        elif i > n - release:
            env = max(0.0, (n - i) / max(1, release))
        if kind == "noise":
            rng_state = (1103515245 * rng_state + 12345) & 0x7fffffff
            raw = (rng_state % 65536) / 32768.0 - 1.0
            sample = amp * env * raw * 0.6
        else:
            if kind == "sweep_down":
                f = freq * (1.0 - 0.5 * (i / n))
            elif kind == "sweep_up":
                f = freq * (0.6 + 0.8 * (i / n))
            else:
                f = freq
            sample = amp * env * math.sin(2 * math.pi * f * i / sr)
        frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def _ensure_wav(name: str) -> Path | None:
    if name not in _TONES:
        return None
    path = _runtime_dir() / f"{name}.wav"
    if not path.exists() or path.stat().st_size < 1000:
        freq, dur, amp, kind = _TONES[name]
        try:
            _synthesise(path, freq, dur, amp, kind)
        except OSError:
            return None
    return path


class Sounds:
    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = os.environ.get("SE_TUI_SOUND", "").lower() in (
                "1", "true", "yes",
            )
        self.enabled = bool(enabled) and _PLAYER is not None
        self._last_played: dict[str, float] = {}
        self._debounce_s = 0.080
        self._test_hook: Callable[[str, Path], None] | None = None

    @property
    def available(self) -> bool:
        return _PLAYER is not None

    def toggle(self) -> bool:
        if _PLAYER is None:
            self.enabled = False
            return False
        self.enabled = not self.enabled
        return self.enabled

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        last = self._last_played.get(name, 0.0)
        if now - last < self._debounce_s:
            return
        self._last_played[name] = now
        path = _ensure_wav(name)
        if path is None:
            return
        if self._test_hook is not None:
            try:
                self._test_hook(name, path)
            except Exception:
                pass
            return
        if _PLAYER is None:
            return
        try:
            subprocess.Popen(
                [_PLAYER, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError):
            self.enabled = False
