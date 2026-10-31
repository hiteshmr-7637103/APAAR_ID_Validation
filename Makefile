.PHONY: up down reset logs test lint format migrate seed seed-national

# Bring up db, api, web (migrations + seed run automatically in the api container).
up:
	docker compose up --build

down:
	docker compose down

# Wipe the database volume and start fresh.
reset:
	docker compose down -v
	docker compose up --build

logs:
	docker compose logs -f api

# Backend tests run inside the api container against the apaar_test database.
test:
	docker compose run --rm --entrypoint "" api pytest

lint:
	docker compose run --rm --no-deps --entrypoint "" api sh -c "ruff check . && black --check ."
	docker compose run --rm --no-deps web npm run lint

format:
	docker compose run --rm --no-deps --entrypoint "" api sh -c "ruff check --fix . && black ."

migrate:
	docker compose run --rm api alembic upgrade head

seed:
	docker compose run --rm api python -m app.db.seed

# Regenerate the deterministic synthetic national dataset (same as POST /api/v1/demo/seed).
seed-national:
	docker compose run --rm api python -m app.db.seed --national
