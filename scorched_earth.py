"""Entry point — `python scorched_earth.py`."""
from __future__ import annotations

import argparse

from scorched_earth_tui.app import run


def main() -> None:
    p = argparse.ArgumentParser(prog="scorched-earth-tui")
    p.parse_args()
    run()


if __name__ == "__main__":
    main()
