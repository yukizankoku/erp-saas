# 0001 — Fondasi arsitektur

**Status:** Diterima (PO, 24 Nov 2026) · **Rujukan:** PRD §6

## Konteks
ERP SaaS dengan 15+ modul, dikembangkan oleh tim AI agent, harus ringan, aman, murah di fase awal (Vercel + Neon),
lalu pindah ke VPS tanpa menulis ulang.

## Keputusan
1. **Modular monolith** Python (FastAPI) dengan pola MVC per modul; modul berinteraksi lewat service publik/event domain.
2. **SQLAlchemy 2 async + Alembic**, satu riwayat migrasi linear, naming convention constraint di `Base.metadata`.
3. **Multi-tenant shared schema**: `tenant_id` di setiap tabel bisnis + repository scoping + PostgreSQL RLS
   (`SET LOCAL app.tenant_id`) + test isolasi otomatis per endpoint.
4. **Record Framework** di `app/framework/` menyediakan fitur lintas modul (audit, lampiran, import/export, recycle bin,
   dynamic fields, approval, print, penomoran) — modul tidak membuat ulang.
5. **Ports & adapters** untuk storage, queue, realtime, email, PDF, cache.
6. **UUIDv7** dibuat aplikasi/klien (siap offline); kolom `version` untuk optimistic locking & sinkronisasi.
7. **Web**: React + TypeScript + Vite PWA; **mobile**: Expo; tipe API di-generate dari OpenAPI.
8. **TypeScript 6.x** dikunci sementara karena typescript-eslint belum mendukung TS 7.

## Konsekuensi
- Satu deploy sederhana; batas modul dijaga lewat review & aturan impor.
- RLS menambah kompleksitas sesi DB, tetapi menjadi lapisan pertahanan kedua terhadap kebocoran lintas tenant.
- Adapter fase awal terbatas (tanpa WebSocket/worker permanen) — fitur realtime/job berat menyesuaikan.

## Alternatif yang ditolak
- Microservices (terlalu berat untuk tim & biaya awal) · Django (ORM sendiri, bertentangan dengan SQLAlchemy/Alembic) ·
  schema/DB per tenant sejak awal (operasional migrasi mahal; tetap terbuka sebagai opsi enterprise).
