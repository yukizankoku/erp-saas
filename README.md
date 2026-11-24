# [APP_NAME] — ERP SaaS

ERP SaaS terintegrasi untuk UKM hingga perusahaan menengah (Indonesia-first). Nama aplikasi belum final;
dibaca dari konfigurasi `APP_NAME` / `VITE_APP_NAME`.

- **Spesifikasi produk:** [`docs/PRD.md`](docs/PRD.md)
- **Aturan untuk AI agent:** [`CLAUDE.md`](CLAUDE.md)
- **Status pengerjaan:** [`.agent/STATE.md`](.agent/STATE.md) dan GitHub Issues berlabel `daily-report`

## Struktur

| Folder | Isi |
|---|---|
| `backend/` | Python 3.12 + FastAPI (MVC modular), SQLAlchemy 2 + Alembic |
| `web/` | React + TypeScript + Vite (PWA) |
| `packages/` | Kode TypeScript bersama (api-client, ui, i18n, sync) |
| `mobile/` | Aplikasi Expo (mulai Fase 3) |
| `infra/` | docker-compose untuk dev lokal; konfigurasi VPS (Fase 9) |
| `scripts/` | Skrip operasi agent & utilitas |
| `docs/` | PRD, ADR, desain, runbook |

## Menjalankan secara lokal

```bash
cp .env.example backend/.env
docker compose -f infra/docker-compose.yml up -d     # PostgreSQL dev
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
cd web && pnpm install && pnpm dev                    # http://localhost:5173 (proxy /api → :8000)
```

## Branch

`main` = produksi (persetujuan PO) · `develop` = integrasi · `feat/*`, `fix/*`, `chore/*`, `docs/*` = kerja per tugas.
Lihat PRD §5.8.
