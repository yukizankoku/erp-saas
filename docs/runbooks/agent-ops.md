# Runbook — Operasi AI Agent

## Komponen

| Komponen | Keterangan |
|---|---|
| Sesi runner | "ERP Agent Runner (otomatis)" di claude.ai/code — semua run terjadwal masuk ke sini (ADR 0003) |
| Scheduled task | Dikelola di halaman scheduled tasks claude.ai (lihat tabel jadwal) |
| Kontrol | `.agent/control.yaml` (mode, jam terlarang, jadwal untuk menghitung deadline) |
| Protokol | `.agent/PLAYBOOK.md` |
| Status | `.agent/STATE.md`, `.agent/runs/`, issue berlabel `daily-report` |
| Lock | Branch `agent/lock` (`scripts/agent_lock.py`, ADR 0002) |

## Jadwal (WIB)

| Scheduled task | Cron |
|---|---|
| ERP · Work run 18:07 WIB (Sen–Jum) | `CRON_TZ=Asia/Jakarta 7 18 * * 1-5` |
| ERP · Work run 00:07 WIB (setiap hari) | `CRON_TZ=Asia/Jakarta 7 0 * * *` |
| ERP · Work run akhir pekan | `CRON_TZ=Asia/Jakarta 7 5,10,15,19 * * 0,6` |
| ERP · Laporan harian 04:47 WIB | `CRON_TZ=Asia/Jakarta 47 4 * * *` |

Jadwal di `.agent/control.yaml` (`schedule`) harus selalu sama dengan tabel ini.

## Menghentikan agent

| Kebutuhan | Cara |
|---|---|
| Jeda sementara (token hampir nol) | Ubah `mode: paused` di `.agent/control.yaml` (branch `develop`) |
| Berhenti total (token nol) | Nonaktifkan keempat scheduled task di claude.ai, atau minta "STOP" ke PM Agent |
| Hentikan run yang sedang berjalan | Buka sesi runner → tombol stop/interrupt |
| Lanjut lagi | Aktifkan scheduled task + `mode: active` |

## Masalah umum

| Gejala | Tindakan |
|---|---|
| Laporan harian tidak muncul | Cek sesi runner masih ada (tidak diarsipkan) dan scheduled task aktif |
| Run selalu NO-GO "terkunci" | `python3 scripts/agent_lock.py status`; lock basi otomatis setelah 6 jam |
| Branch lama menumpuk | Proxy sesi agent menolak hapus branch → aktifkan "Automatically delete head branches" di Settings GitHub |
