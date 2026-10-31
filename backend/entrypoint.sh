#!/bin/sh
# Apply migrations and load (idempotent) seed data, then start the given command.
set -e

echo "[entrypoint] running migrations"
alembic upgrade head

echo "[entrypoint] seeding demo data (+ synthetic national dataset on first start)"
python -m app.db.seed --national-if-empty

exec "$@"
