# STATE — status terkini proyek

> Diperbarui setiap checkpoint oleh PM Agent. Maks. 150 baris; ganti isi lama, jangan menumpuk riwayat.

**Diperbarui:** 2026-11-24 malam (sesi Fase 0 manual)
**Fase aktif:** 1 — Platform Inti (Fase 0 selesai, kecuali setup Vercel/Neon yang menunggu PO)
**Mode:** active · **Run otomatis pertama:** Rabu 25 Nov 2026 18:07 WIB

## Selesai (Fase 0)
- PRD v0.3 disetujui PO (`docs/PRD.md`); ADR 0001–0003.
- Monorepo: backend FastAPI + SQLAlchemy async + Alembic; web React + TS + Vite PWA; pnpm workspace.
- CI: workflow `backend`, `web`, `security` (filter path); Dependabot bulanan; template PR.
- Agent ops: `CLAUDE.md`, 9 sub-agent, `control.yaml`, `PLAYBOOK.md`, pre-flight, lock `agent/lock`, `scripts/dev_pg.sh`.
- Backlog Fase 1: issue #2–#26 (P1-01 … P1-25).
- Scheduled tasks → sesi runner "ERP Agent Runner (otomatis)" (ADR 0003).

## Berikutnya (urutan disarankan)
1. #2 P1-01 Fondasi data: BaseRecord, konteks tenant & RLS — tanpa dependensi, jalur sensitif.
2. #10 P1-09 Ports & adapters — tanpa dependensi.
3. #3 P1-02 Tenant/company/branch/warehouse.
4. #18 P1-17 i18n/zona waktu/mata uang → #19 P1-18 design system & app shell.
5. Lalu #4 auth, #6 IAM, #8 menu modular, #9 audit, dst. sesuai dependensi di tiap issue.

## Menunggu PO
- #25 P1-23: akun Vercel + Neon (region Singapura).
- Pengaturan GitHub: default branch → `main`; "Automatically delete head branches"; proteksi branch `main`.
- Rilis pertama `develop` → `main` (berisi Fase 0) — butuh persetujuan PO.

## Catatan teknis
- TypeScript dikunci 6.x (typescript-eslint belum mendukung TS 7).
- Proxy sesi agent menolak penghapusan ref git → branch tidak bisa dihapus agent; lock memakai commit fast-forward (ADR 0002).
- Sesi baru dari scheduled task tidak punya akses tulis GitHub → semua run lewat sesi runner (ADR 0003).
  `fire_trigger` selalu membuat sesi baru tanpa repo — untuk uji gunakan scheduled task sekali jalan (`run_once_at`).
- Docker tidak tersedia di sesi agent → `eval "$(scripts/dev_pg.sh)"` untuk PostgreSQL lokal; CI memakai PostgreSQL 17.
- Membuat `.claude/settings.json` (aturan izin) dari sesi agent ditolak pengaman — keputusan PO bila dibutuhkan.
