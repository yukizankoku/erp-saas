# 0002 — Lock antar-run agent via branch git

**Status:** Diterima (24 Nov 2026) · **Rujukan:** PRD §5.4, §5.11

## Konteks
Run agent terjadwal berjalan di sesi cloud terpisah; dua run tidak boleh mengerjakan repo bersamaan.
Proxy sesi cloud menolak penghapusan ref git, sehingga lock berbasis "buat lalu hapus branch" tidak bisa dipakai.
Menyimpan file lock di `develop` akan mengotori riwayat.

## Keputusan
- Lock = branch `agent/lock` yang tidak pernah dihapus. Acquire/release = commit fast-forward bertree kosong
  dengan pesan `LOCKED|UNLOCKED <run_id> <iso-utc>` (`scripts/agent_lock.py`). Push tanpa force → dua run yang
  berebut, salah satunya ditolak (compare-and-swap).
- Lock basi setelah 6 jam dan boleh diambil alih.
- Pengaman kedua: setiap run punya **deadline** = run terjadwal berikutnya − 15 menit atau awal jam terlarang
  (`scripts/agent_preflight.py`), sehingga run tidak tumpang tindih walau lock gagal.

## Konsekuensi
Branch `agent/lock` tumbuh dua commit kosong per run (sangat kecil). Laporan harian tidak memakai lock (read-only).
