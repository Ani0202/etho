.PHONY: check lint test fmt

check: lint test

lint:
	uv run ruff check etho tests eval scripts bin/codex_run.py bin/codex-queue

test:
	uv run pytest

fmt:
	uv run ruff check --fix etho tests eval scripts bin/codex_run.py bin/codex-queue
