PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
PYTEST ?= .venv/bin/pytest

.PHONY: help dev migrate seed test test-integration lint typecheck agent dashboard simulate up clean

help:
	@echo "make dev              - install package + dev deps in editable mode"
	@echo "make migrate          - apply alembic migrations (creates infra_monitor.db)"
	@echo "make seed             - insert sample hosts and rules"
	@echo "make test             - run unit tests with coverage"
	@echo "make test-integration - run integration tests (RUN_INTEGRATION=1)"
	@echo "make lint             - ruff + black --check"
	@echo "make typecheck        - mypy --strict"
	@echo "make agent            - start the local psutil collector"
	@echo "make dashboard        - run FastAPI dashboard on :8080"
	@echo "make simulate         - feed synthetic metrics to demonstrate the alert flow"
	@echo "make up               - docker-compose up"

dev:
	$(PIP) install -e ".[dev]"

migrate:
	$(PY) -m infra_monitor.cli migrate

seed:
	$(PY) -m infra_monitor.cli seed

test:
	$(PYTEST) tests/unit --cov --cov-report=term-missing --cov-fail-under=80

test-integration:
	RUN_INTEGRATION=1 $(PYTEST) tests/integration -m integration

lint:
	.venv/bin/ruff check src tests
	.venv/bin/black --check src tests

format:
	.venv/bin/ruff check --fix src tests
	.venv/bin/black src tests

typecheck:
	.venv/bin/mypy src

agent:
	$(PY) -m infra_monitor.cli agent

dashboard:
	$(PY) -m infra_monitor.cli dashboard

simulate:
	$(PY) -m infra_monitor.cli simulate

up:
	docker compose up --build

clean:
	rm -rf .venv build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage infra_monitor.db
