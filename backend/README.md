# Backend — [APP_NAME]

Python + FastAPI, pola MVC modular (lihat `docs/PRD.md` §6.3).

```bash
uv sync                      # pasang dependensi
uv run uvicorn app.main:app --reload
uv run pytest                # test
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run alembic upgrade head  # migrasi (butuh DATABASE_URL_DIRECT / DATABASE_URL)
```

## Test integrasi PostgreSQL (RLS)

`tests/integration/` memakai PostgreSQL nyata dengan RLS aktif (ADR 0004 §8). URL hanya dibaca dari env
`DATABASE_URL` (bukan `.env.local`) dan harus role **superuser** database uji lokal/CI; tanpa `DATABASE_URL` test
di-skip, kecuali `CI=true` (gagal). Fixture membuat role `app_rls_test` dan tabel uji sendiri, lalu menghapus tabelnya.

```bash
eval "$(../scripts/dev_pg.sh)"   # export DATABASE_URL lokal
uv run pytest
```

Role database aplikasi & pedoman migrasi data di bawah RLS: `docs/runbooks/db-roles.md`.
