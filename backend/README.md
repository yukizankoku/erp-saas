# Backend — [APP_NAME]

Python + FastAPI, pola MVC modular (lihat `docs/PRD.md` §6.3).

```bash
uv sync                      # pasang dependensi
uv run uvicorn app.main:app --reload
uv run pytest                # test
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run alembic upgrade head  # migrasi (butuh DATABASE_URL_DIRECT / DATABASE_URL)
```
