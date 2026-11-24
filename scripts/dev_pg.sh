#!/usr/bin/env bash
# PostgreSQL lokal sementara untuk test di sesi agent cloud (Docker tidak tersedia).
# Pemakaian (dari root repo):  eval "$(scripts/dev_pg.sh)"   → mengekspor DATABASE_URL
# Variabel opsional: PG_DEV_PORT (default 5432), PG_DEV_DIR (default ${TMPDIR:-/tmp}/erp-pg).
# Saat dijalankan sebagai root, server berjalan sebagai user postgres: induk PG_DEV_DIR harus bisa ditelusuri user itu.
set -euo pipefail

PGBIN=${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}
PORT=${PG_DEV_PORT:-5432}
DIR=${PG_DEV_DIR:-${TMPDIR:-/tmp}/erp-pg}

if [ -z "$PGBIN" ]; then
  echo "echo 'PostgreSQL tidak ditemukan; gunakan docker compose -f infra/docker-compose.yml up -d' >&2"
  exit 1
fi

if ! "$PGBIN/pg_isready" -h localhost -p "$PORT" -q; then
  mkdir -p "$DIR"
  chown postgres "$DIR" 2>/dev/null || true
  run() { if [ "$(id -u)" = "0" ]; then su postgres -c "$*"; else sh -c "$*"; fi; }
  if [ ! -f "$DIR/data/PG_VERSION" ]; then
    run "$PGBIN/initdb -D $DIR/data -U erp --auth=trust -E UTF8" >/dev/null
  fi
  run "$PGBIN/pg_ctl -D $DIR/data -o '-p $PORT -k $DIR' -l $DIR/log -w start" >/dev/null
  "$PGBIN/createdb" -h localhost -p "$PORT" -U erp erp 2>/dev/null || true
fi

echo "export DATABASE_URL=postgresql://erp@localhost:$PORT/erp"
