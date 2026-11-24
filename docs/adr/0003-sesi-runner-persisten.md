# 0003 — Run otomatis lewat satu sesi runner persisten

**Status:** Diterima (24 Nov 2026) · **Rujukan:** PRD §5.1, §5.6 · **Mengubah:** asumsi "setiap run = sesi baru"

## Konteks
PRD §5.1 merencanakan setiap run sebagai sesi cloud baru (konteks bersih). Uji coba 24 Nov 2026 menunjukkan sesi baru yang
dibuat scheduled task di environment *Default* hanya bisa meng-clone repo secara read-only: push, issue, dan PR ditolak
(403 — akses GitHub tidak aktif untuk sesi itu), dan environment Default tidak bisa diberi daftar repository.
Pemicu manual (`fire_trigger`) juga selalu membuat sesi baru tanpa repo.

## Keputusan
- Dibuat satu sesi **"ERP Agent Runner (otomatis)"** dengan repo `yukizankoku/erp-saas` terpasang (akses GitHub penuh,
  mode izin auto). Semua scheduled task mengirim instruksi ke sesi ini (`persistent_session_id`).
- Kebersihan konteks dijaga oleh aturan PLAYBOOK: setiap run mengabaikan riwayat percakapan dan hanya memakai
  `STATE.md`, issue, dan git sebagai sumber kebenaran; kompaksi otomatis meringkas riwayat lama.
- Uji manual dilakukan dengan scheduled task sekali jalan (`run_once_at`), bukan `fire_trigger`.

## Konsekuensi
- (+) Run otomatis punya akses GitHub tanpa tindakan tambahan PO.
- (−) Konteks runner bertambah dari run ke run → biaya baca konteks di awal run lebih besar daripada sesi baru.
- (−) Run tidak bisa paralel (memang tidak diinginkan; lock tetap dipakai sebagai pengaman).

## Alternatif / rencana perbaikan
Bila PO membuat environment non-Default dengan repository terpasang, atau membuat routine dari UI claude.ai dengan
repository dipilih, scheduled task dapat kembali ke mode "sesi baru per run". PM mengevaluasi ulang di akhir Fase 1.
