# 0005 — Ports & adapters infrastruktur (bagian A)

**Status:** Diusulkan (27 Nov 2026) — adapter vendor = gerbang PO ·
**Rujukan:** issue #10 (P1-09), PRD §6.5, CLAUDE.md aturan 9, ADR 0001

## Konteks
PRD §6.5 memisahkan fase Vercel dari fase VPS. Kode bisnis tidak boleh memanggil layanan vendor langsung; ia hanya
bergantung pada port, sedangkan adapter dipilih lewat konfigurasi.

## Keputusan
### 1. Port (`backend/app/core/ports/`, `typing.Protocol` + `runtime_checkable`, method I/O async)
- `ObjectStorage`: `put/get/delete/exists`, `presigned_upload_url(key, content_type, max_bytes, expires_in)`,
  `presigned_download_url(key, expires_in)` -> `PresignedUrl(url, method, headers, expires_at)`.
  Kunci opaque, dibangun `tenant_key(...)` = `t/<tenant_id>/...` dari konteks tenant. `validate_key` menolak kunci
  kosong, absolut, `..`, `.`, segmen kosong, `\`, NUL, dan `:`; adapter lokal juga memastikan path ada di bawah root.
- `Mailer`: `send(EmailMessage(to, subject, text, html?, reply_to?))`.
- `JobQueue`: `enqueue(name, payload, *, run_at, max_attempts=5) -> id`; `JobRunner.run_pending()`; handler didaftarkan
  dengan `@job_handlers.register("modul.nama")`. Retry backoff eksponensial tetap `min(30 * 2^(n-1), 3600)` detik;
  gagal pada percobaan ke-`max_attempts` -> status `failed` (+ `last_error`). Status string biasa, bukan ENUM PG.
- `Realtime`: `publish(channel, event)`, `poll(channel, since_cursor) -> (events, cursor)` (polling; fase Vercel).
  Pemanggil wajib memasukkan tenant ke nama channel.
- `PdfRenderer`: stub `render(html) -> bytes`; adapter bawaan melempar `NotImplementedError`.

### 2. Adapter dev/test (`backend/app/core/adapters/`)
Storage lokal (filesystem) & in-memory, mailer console (hanya log penerima+subjek) & memory (menangkap pesan),
job queue in-memory (jam dapat disuntik untuk test), realtime in-memory.

### 3. Pemilihan adapter (`ports/providers.py`)
Env: `STORAGE_BACKEND=local|memory`, `MAILER_BACKEND=console|memory`, `JOBQUEUE_BACKEND=memory`,
`REALTIME_BACKEND=memory`, `STORAGE_LOCAL_ROOT`, `STORAGE_SIGNING_SECRET`. `get_storage()` dst. adalah dependency
FastAPI (singleton per proses). Nilai tak dikenal -> `UnknownBackendError` yang menyebut nilai yang diizinkan;
`validate_backends(settings)` dipanggil saat startup agar gagal cepat.

### 4. URL bertanda tangan lokal
HMAC-SHA256 atas `method, key, exp, content_type, max_bytes`; `verify_signed` memakai `hmac.compare_digest`, menolak
kunci tak aman dan URL kedaluwarsa. Secret dari `STORAGE_SIGNING_SECRET`; bila kosong, acak per proses. Tidak ada
secret di kode/repo (test memakai secret tetap palsu). Endpoint yang menerima URL ini belum ada (menyusul bersama
fitur lampiran).

## Konsekuensi
- Adapter vendor (Vercel Blob/R2, penyedia email transaksional) **hanya setelah persetujuan PO** (layanan pihak
  ketiga menerima data pelanggan + biaya/secret baru) — issue `needs-po` terpisah.
- **Bagian B:** job queue berbasis tabel Postgres (+ migrasi, RLS, pemicu terjadwal); antarmuka tidak berubah.
- Adapter in-memory tidak berbagi state antar proses/instance serverless; hanya untuk dev/test.

## Alternatif
SDK vendor langsung di kode bisnis (ditolak: aturan 9, lock-in); `Enum` untuk status job (ditolak: aturan DB 5).
