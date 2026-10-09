#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=etfphase4accept20261008

if [[ ! -f .env ]]; then
  key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'ETF_DATA_MODE=demo\nETF_API_WRITE_KEY=%s\nLLM_ENABLED=false\n' "$key" > .env
  chmod 600 .env
fi

docker compose build
docker compose up -d --wait
python3 scripts/check_phase4_container.py before
docker compose down
docker compose up -d --wait
python3 scripts/check_phase4_container.py after
docker compose down
chmod 755 output output/phase4_container_acceptance
chmod 644 output/phase4_container_acceptance/*
printf '\nDocker API, PDF and database restart acceptance passed. Results: %s/output/phase4_container_acceptance\n' "$PWD"
