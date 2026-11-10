UV_RUN := uv run --locked --extra dev

.PHONY: up down logs install lint test test-int train eval load demo

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

install:
	cd backend && uv sync --locked --extra dev

lint:
	cd backend && $(UV_RUN) ruff check .
	cd backend && $(UV_RUN) ruff format --check .
	cd backend && $(UV_RUN) mypy
	cd backend && $(UV_RUN) mypy --strict src/engine/detection src/engine/simulation
	@echo frontend lint: not implemented yet

test:
	cd backend && $(UV_RUN) pytest tests/unit --cov=engine --cov-report=term-missing

test-int:
	@echo test-int: not implemented yet

train:
	@echo train: not implemented yet

eval:
	@echo eval: not implemented yet

load:
	@echo load: not implemented yet

demo:
	@echo demo: not implemented yet
