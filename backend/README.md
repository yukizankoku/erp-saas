# Backend — [APP_NAME]

Python + FastAPI, pola MVC modular (lihat `docs/PRD.md` §6.3).

```bash
uv sync                      # pasang dependensi
uv run uvicorn app.main:app --reload
uv run pytest                # test
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run alembic upgrade head  # migrasi (lihat "Migrasi" di bawah)
```

## Migrasi (Alembic)

Database tujuan **hanya** dari `-x db_url=...` atau environment variable proses dengan urutan
`DATABASE_URL_UNPOOLED` → `DATABASE_URL_DIRECT` → `DATABASE_URL` (`app/core/migration_target.py`) — **tidak** dari
`.env` / `.env.local`, jadi `.env.local` hasil `neon env pull` tidak pernah dipakai diam-diam. Sebelum migrasi,
`alembic` mencetak tujuan ke stderr (`host=… port=… database=… user=…`, tanpa password); periksa sebelum lanjut.

```bash
# Lokal / CI: set ketiganya (dua perintah: `$DATABASE_URL` di export yang sama masih bernilai lama).
export DATABASE_URL=postgresql://erp@localhost:5432/erp
export DATABASE_URL_DIRECT=$DATABASE_URL DATABASE_URL_UNPOOLED=$DATABASE_URL
uv run alembic upgrade head

# Neon (koneksi direct dari .env.local) — harus eksplisit, di subshell agar tidak tertinggal di shell:
(set -a; . ../.env.local; set +a; uv run alembic upgrade head)
```

Migrasi ke Neon produksi/staging = gerbang PO (CLAUDE.md); agent hanya memigrasikan database lokal/CI.

## Test integrasi PostgreSQL (RLS)

`tests/integration/` memakai PostgreSQL nyata dengan RLS aktif (ADR 0004 §8). URL hanya dibaca dari env
`DATABASE_URL` (bukan `.env.local`) dan harus role **superuser** database uji lokal/CI; tanpa `DATABASE_URL` test
di-skip, kecuali `CI=true` (gagal). Fixture membuat role `app_rls_test` dan tabel uji sendiri, lalu menghapus tabelnya.

```bash
eval "$(../scripts/dev_pg.sh)"   # export DATABASE_URL lokal
uv run pytest
```

Role database aplikasi & pedoman migrasi data di bawah RLS: `docs/runbooks/db-roles.md`.
