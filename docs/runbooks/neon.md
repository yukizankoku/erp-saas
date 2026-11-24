# Runbook — Neon (PostgreSQL)

| Item | Nilai |
|---|---|
| Organisasi / proyek | `org-late-feather-66750651` / `withered-waterfall-79816375` ("ERP-SaaS") |
| Region | `aws-ap-southeast-1` (Singapura) · PostgreSQL 18 |
| Branch default | `production` |
| Konfigurasi | `neon.ts` (policy branch) · `.neon` (link lokal, di-gitignore) |

## Variabel lingkungan (`neon env pull` → `.env.local`, tidak di-commit)

| Variabel | Pakai untuk |
|---|---|
| `DATABASE_URL` | Aplikasi — koneksi **pooled** (host `-pooler`, PgBouncer mode transaksi) |
| `DATABASE_URL_UNPOOLED` | Migrasi Alembic, `pg_dump` — koneksi **direct** |
| `DATABASE_POOL_SIZE` | `0` di serverless (Vercel) → NullPool; `>0` di server berumur panjang (VPS) |

Backend menormalisasi URL Neon untuk asyncpg (`sslmode` → `ssl`, `channel_binding` dibuang) dan mematikan
cache prepared statement agar aman di PgBouncer (`backend/app/core/db.py`).

## Perintah

```bash
neon deploy          # terapkan neon.ts ke branch tertaut + tarik env ke .env.local
neon env pull        # tarik ulang env
neon checkout dev --create   # branch pengembangan (TTL 7 hari sesuai neon.ts)
```

## Uji ujung-ke-ujung

Sesi agent cloud **tidak bisa** membuka koneksi TCP ke Postgres Neon (proxy hanya meneruskan HTTPS); API Neon
(`console.neon.tech`) bisa. Bukti koneksi aplikasi → Neon dijalankan oleh workflow `neon-smoke`
(`.github/workflows/neon-smoke.yml`): branch Neon sementara dari `production` → migrasi → start API →
`GET /api/v1/health/db` → hapus branch. Butuh secret repo `NEON_API_KEY`.

## Batas paket gratis
Free plan: kuota CU-hours & transfer bulanan; bila habis, compute disuspend (SQLSTATE `53000`, HTTP `402`).
Lihat skill `neon-postgres` › Free Plan Limits.
