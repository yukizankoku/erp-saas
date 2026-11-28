# STATE — status terkini proyek

> Diperbarui setiap checkpoint oleh PM Agent. Maks. 150 baris; ganti isi lama, jangan menumpuk riwayat.

**Diperbarui:** 2026-11-28 10:25 WIB (run 2026-11-28T1009 ditutup)
**Fase aktif:** 1 — Platform Inti · **Mode:** active
**Batas pemakaian:** berhenti bila mingguan ≥ 65% Sen–Jum; Sab–Min WIB s.d. 100% (keputusan PO 25 Nov). Terakhir: mingguan 90% (28 Nov 10:20 WIB); reset Sen 30 Nov 07:00 WIB.

## Selesai
- Fase 0 lengkap (PRD v0.3, monorepo, CI, agent ops, ADR 0001–0003, runner + jadwal).
- #2 P1-01 Fondasi data (PR #33): UUIDv7, konteks tenant per transaksi (aman savepoint), RLS FORCE fail-closed,
  BaseRecord + TenantRepository, 409 record.conflict, URL migrasi hanya dari env proses. ADR 0004. 115 test.
- #35 bentrok id → 409 (ikut PR #33).
- Neon: `neon init` + `neon deploy` OK; backend tersambung (PR #30) — `DATABASE_URL` pooled / `DATABASE_URL_UNPOOLED` direct.
- Vercel: preview deploy hijau (PR #32): web statis + FastAPI di `/api/*`, region sin1.
- Neon end-to-end terbukti lewat `neon-smoke` (PR #31): migrasi + /health/db ok.
- #34 role `app_runtime` dibuat di Neon produksi (PR #39) + `APP_DATABASE_URL` mengalahkan URL integrasi (PR #40).
- `neon-smoke` post-job cleanup diperbaiki (PR #43). #2 ditutup.
- #36 P1-01d filter tenant ORM (PR #44, keputusan PO Opsi B): kriteria tenant otomatis, identity map, deny-by-default
  tulis + CI `scripts/check_orm_banned_apis.py` (DML di CTE, `make_transient_to_detached`, `merge(load=False)`). Sisa → #47.
- #37 P1-01e FK komposit (PR #48): UNIQUE (tenant_id, id) otomatis, `tenant_fk()`, 23503 → 422 `record.reference_invalid`
  tanpa oracle, meta-test FK. ADR 0004 §4a/§7a.
- RLS produksi aktif: Vercel memakai `APP_DATABASE_URL` role `app_runtime` (PO, 28 Nov).
- #3 bagian A (PR #49): migrasi pertama `0001` — tenants (global, `app_runtime` SELECT saja), companies/branches/
  warehouses (BaseRecord + RLS, FK komposit, gudang–cabang satu company via FK 3 kolom), service + validasi.
  `infra/sql/app_runtime.sql` kini menjaga tenants read-only saat dijalankan ulang.
- #10 bagian A (PR #45): ports `ObjectStorage`/`Mailer`/`JobQueue`/`Realtime`/`PdfRenderer` + adapter dev/in-memory,
  pemilihan via env, key allowlist + kepemilikan tenant, signed URL kanonik; produksi menolak adapter dev saat dipakai. ADR 0005.

## Berikutnya (urutan)
1. #4 P1-03 Autentikasi (login, token, sesi) → prasyarat #3 bagian B (API org + konteks company aktif, catatan di #3).
2. #10 bagian B: job queue Postgres (tenant_id per job, handler dalam tenant_context, redaksi last_error), realtime per tenant.
3. #18 P1-17 i18n/zona waktu/mata uang → #19 P1-18 design system.
4. #47 (p3) celah lapis 1 tersisa + follow-up review.

## Menunggu PO
- #50 enkripsi NPWP company (Opsi A/B/C) — sebelum API org (#3 bagian B) di-merge.
- Setelah migrasi `0001` dijalankan di Neon produksi: jalankan ulang workflow `db-provision-roles`.
- GitHub Environment `db-production`: pastikan *Required reviewers* aktif.
- Pengaturan repo: default branch `main`, hapus head branch otomatis, proteksi `main`.

## Catatan teknis
- Hemat kuota: kerjakan tugas di sesi dengan riwayat pendek (runner); sesi panjang yang bangun setelah jeda
  membaca ulang seluruh konteks (±2% kuota mingguan per bangun).
- Sub-agent berbagi folder kerja dengan PM → PM memakai `git worktree` untuk kerja paralel; stage file eksplisit.
- Sesi agent cloud tidak bisa TCP ke Postgres Neon (hanya HTTPS); uji Neon lewat GitHub Actions (`neon-smoke`).
- `/tmp/claude-0` berkala kembali ke izin 700 → PostgreSQL uji lokal crash; jalankan `chmod 711 /tmp/claude-0` lalu
  `scripts/dev_pg.sh` lagi.
- TypeScript dikunci 6.x; proxy menolak hapus branch (lock via commit fast-forward, ADR 0002); runner persisten (ADR 0003).
