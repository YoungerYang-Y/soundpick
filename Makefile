.PHONY: setup env dev test lint

setup:
	./scripts/setup.sh

env:
	.venv/bin/python scripts/init_env.py

dev:
	.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check app scripts tests
	.venv/bin/python -m ruff format --check app scripts tests
