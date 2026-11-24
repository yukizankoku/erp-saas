# STATE — status terkini proyek

> Diperbarui setiap checkpoint oleh PM Agent. Maks. 150 baris; ganti isi lama, jangan menumpuk riwayat.

**Diperbarui:** 2026-11-24 22:00 WIB (run manual 2026-11-24T2102, perintah PO "KERJAKAN SEKARANG")
**Fase aktif:** 1 — Platform Inti · **Mode:** active
**Batas pemakaian:** berhenti bila mingguan ≥ 65% (keputusan PO). Terakhir: mingguan 57%, 5 jam 84% (reset 01:40 WIB).

## Selesai
- Fase 0 lengkap (PRD v0.3, monorepo, CI, agent ops, ADR 0001–0003, runner + jadwal).
- #2 P1-01 Fondasi data (PR #33): UUIDv7, konteks tenant per transaksi (aman savepoint), RLS FORCE fail-closed,
  BaseRecord + TenantRepository, 409 record.conflict, URL migrasi hanya dari env proses. ADR 0004. 115 test.
- #35 bentrok id → 409 (ikut PR #33).
- Neon: `neon init` + `neon deploy` OK; backend tersambung (PR #30) — `DATABASE_URL` pooled / `DATABASE_URL_UNPOOLED` direct.
- Vercel: preview deploy hijau (PR #32): web statis + FastAPI di `/api/*`, region sin1.

## Berikutnya (urutan)
1. #36 P1-01d filter tenant otomatis di level ORM (wajib sebelum tabel bisnis pertama).
2. #37 P1-01e FK komposit (tenant_id, x_id) + amandemen ADR 0004.
3. #10 P1-09 Ports & adapters.
4. #3 P1-02 Tenant/company/branch/warehouse (bergantung #36, #37).

## Menunggu PO
- PR #31 `neon-smoke`: tambahkan secret repo `NEON_API_KEY`, lalu PM menjalankan ulang job sebagai bukti akhir Neon.
- #34 role `app_runtime` di Neon (gerbang keamanan) — RLS belum efektif di produksi sampai selesai.
- Vercel: set env `DATABASE_URL` (pooled Neon) + `DATABASE_POOL_SIZE=0`.
- GitHub: default branch → `main`; "Automatically delete head branches"; proteksi `main`.

## Catatan teknis
- Sub-agent berbagi folder kerja dengan PM → PM memakai `git worktree` untuk kerja paralel; stage file eksplisit.
- Sesi agent cloud tidak bisa TCP ke Postgres Neon (hanya HTTPS); uji Neon lewat GitHub Actions (`neon-smoke`).
- `/tmp/claude-0` berkala kembali ke izin 700 → PostgreSQL uji lokal crash; jalankan `chmod 711 /tmp/claude-0` lalu
  `scripts/dev_pg.sh` lagi.
- TypeScript dikunci 6.x; proxy menolak hapus branch (lock via commit fast-forward, ADR 0002); runner persisten (ADR 0003).
