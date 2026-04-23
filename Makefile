.PHONY: all venv run test test-only perf clean bootstrap

# Bootstrap / engine targets are no-ops: the engine is pure Python
# (see DECISIONS.md §2). Kept so `make all` still means "everything
# you need to play" on a fresh clone.
all: venv

bootstrap:
	@echo "no external engine to vendor — pure Python (DECISIONS.md §1,§2)"

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -e .

run: venv
	.venv/bin/python scorched_earth.py

test: venv
	.venv/bin/python -m tests.qa

test-only: venv
	.venv/bin/python -m tests.qa $(PAT)

perf: venv
	.venv/bin/python -m tests.perf

clean:
	rm -rf .venv *.egg-info tests/out/*.svg tests/out/*.png
