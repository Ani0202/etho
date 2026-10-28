.PHONY: check lint test fmt

check: lint test

lint:
	uv run ruff check etho tests eval scripts

test:
	uv run pytest

fmt:
	uv run ruff check --fix etho tests eval scripts
