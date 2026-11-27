# STATE — status terkini proyek

> Diperbarui setiap checkpoint oleh PM Agent. Maks. 150 baris; ganti isi lama, jangan menumpuk riwayat.

**Diperbarui:** 2026-11-28 00:40 WIB (run 2026-11-28T0009 ditutup)
**Fase aktif:** 1 — Platform Inti · **Mode:** active
**Batas pemakaian:** berhenti bila mingguan ≥ 65% Sen–Jum; Sab–Min WIB s.d. 100% (keputusan PO 25 Nov). Terakhir: mingguan 80% (28 Nov 00:35 WIB); reset Sen 30 Nov 07:00 WIB.

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
- #10 bagian A (PR #45): ports `ObjectStorage`/`Mailer`/`JobQueue`/`Realtime`/`PdfRenderer` + adapter dev/in-memory,
  pemilihan via env, key allowlist + kepemilikan tenant, signed URL kanonik; produksi menolak adapter dev saat dipakai. ADR 0005.

## Berikutnya (urutan)
1. #36 P1-01d filter ORM — **blocked** (PR #44 terbuka, CI hijau, 139 test): sisa 2 temuan high (DML di CTE;
   UPDATE/DELETE unit-of-work pada objek buatan manual). Usulan perbaikan di komentar #36.
2. #37 P1-01e FK komposit (tenant_id, x_id) + amandemen ADR 0004 (juga menutup referensi FK lintas tenant).
3. #10 bagian B: job queue Postgres (tenant_id per job, handler dalam tenant_context, redaksi last_error),
   channel realtime terikat tenant.
4. #18 P1-17 i18n/zona waktu/mata uang → #19 P1-18 design system (tidak bergantung #36).
5. #3 P1-02 Tenant/company/branch/warehouse (bergantung #36, #37).

## Menunggu PO
- Vercel: ganti `DATABASE_URL` ke URL pooled role `app_runtime` (Sensitive) lalu Redeploy — sampai itu, aplikasi di
  Vercel masih memakai `neondb_owner` (BYPASSRLS → RLS tidak berlaku).
- GitHub Environment `db-production`: pastikan *Required reviewers* aktif (run 25 Nov berjalan tanpa jeda persetujuan).

## Catatan teknis
- Hemat kuota: kerjakan tugas di sesi dengan riwayat pendek (runner); sesi panjang yang bangun setelah jeda
  membaca ulang seluruh konteks (±2% kuota mingguan per bangun).
- Sub-agent berbagi folder kerja dengan PM → PM memakai `git worktree` untuk kerja paralel; stage file eksplisit.
- Sesi agent cloud tidak bisa TCP ke Postgres Neon (hanya HTTPS); uji Neon lewat GitHub Actions (`neon-smoke`).
- `/tmp/claude-0` berkala kembali ke izin 700 → PostgreSQL uji lokal crash; jalankan `chmod 711 /tmp/claude-0` lalu
  `scripts/dev_pg.sh` lagi.
- TypeScript dikunci 6.x; proxy menolak hapus branch (lock via commit fast-forward, ADR 0002); runner persisten (ADR 0003).
