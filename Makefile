.PHONY: setup env dev test coverage browser-check lint ui-check runtime-check

setup:
	./scripts/setup.sh

env:
	.venv/bin/python scripts/init_env.py

dev:
	.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

test:
	.venv/bin/python -m scripts.run_tests

coverage:
	.venv/bin/python -m scripts.run_tests --coverage

browser-check:
	.venv/bin/python -m scripts.run_tests --browser

lint:
	.venv/bin/python -m ruff check app scripts tests
	.venv/bin/python -m ruff format --check app scripts tests

ui-check:
	mise exec -- node --input-type=module --check < app/static/login.js
	mise exec -- node --input-type=module --check < app/static/app.js
	mise exec -- node --input-type=module --check < app/static/task-view.js
	mise exec -- node --input-type=module --check < app/static/file-share.js
	mise exec -- node tests/ui_flow.cjs

runtime-check:
	.venv/bin/python -m scripts.check_runtime
