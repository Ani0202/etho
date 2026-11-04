.PHONY: check lint test fmt demo demo-serve demo-test

check: lint test

lint:
	uv run ruff check etho tests eval scripts bin/codex_run.py bin/codex-queue

test:
	uv run pytest

fmt:
	uv run ruff check --fix etho tests eval scripts bin/codex_run.py bin/codex-queue

# Offline replay of recorded Meta model responses on the 60 s CalMS21 sample clip: no API keys, no network.
demo:
	uv run python -m etho.replay.demo

# The same, then serve the result in the app, read-only, on http://127.0.0.1:8000
demo-serve:
	uv run python -m etho.replay.demo --serve

# Replays end to end and asserts the labels equal the recorded live run's (and that a miss fails loudly)
demo-test:
	uv run pytest -q -W ignore -o addopts="" tests/test_replay.py
