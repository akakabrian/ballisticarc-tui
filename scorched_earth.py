"""Entry point — `python scorched_earth.py`."""
from __future__ import annotations

import argparse

from ballisticarc_tui.app import run


def main() -> None:
    p = argparse.ArgumentParser(prog="ballisticarc-tui")
    p.parse_args()
    run()


if __name__ == "__main__":
    main()
