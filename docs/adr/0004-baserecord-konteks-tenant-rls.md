# 0004 — BaseRecord, konteks tenant, dan RLS

**Status:** Diusulkan (24 Nov 2026) — perlu review `security`; provisioning role DB produksi = gerbang PO ·
**Rujukan:** issue #2 (P1-01), PRD §6.4, §6.6, §6.7, §7.0, ADR 0001 (#3, #4, #6)

## Konteks
ADR 0001 menetapkan shared schema + `tenant_id` + filter repository + RLS, serta UUIDv7 + `version` sejak Fase 1.
Modul bisnis pertama butuh pola baku yang: menyediakan kolom standar PRD §6.7 tanpa diulang per modul, membawa konteks
tenant dari request/job sampai PostgreSQL, aman di PgBouncer mode transaksi (Neon pooled, `app/core/db.py`), dan
**fail-closed** — tanpa konteks tenant tidak ada baris yang terbaca atau tertulis.

## Keputusan
### 1. Lokasi kode & UUIDv7
- Path (relatif `backend/app/`) di judul tiap bagian; helper RLS di `core/tenancy/rls.py`, bukan `core/rls.py`, agar
  masuk jalur sensitif `**/tenancy/**`. `core/ids.py`: `uuid7() -> UUID` (RFC 9562: 48 bit unix ms + versi/varian +
  74 bit `os.urandom`), tanpa dependensi; ganti `uuid.uuid7` di Python 3.14. Tak monoton dalam 1 ms (cukup untuk indeks).

### 2. Konteks tenant (`core/tenancy/context.py`, tanpa impor SQLAlchemy)
- Satu `ContextVar[TenantContext | None]` (bukan tiga → set/reset atomik); `TenantContext` = dataclass frozen
  (`tenant_id`, `user_id | None`, `company_id | None` — untuk `CompanyScoped`, tak dikirim ke DB). API: `get_context()`,
  `require_tenant_id()` (raise `TenantContextMissing`), `current_user_id()`, `tenant_context(...)` (set + reset token).
- Request: diset dependency **`async def`** setelah autentikasi & resolusi tenant (PRD §6.8); dependency `def` jalan di
  threadpool sehingga contextvar tak sampai ke endpoint. Lintas tenant = loop, satu transaksi per tenant; tanpa "bypass".

### 3. Konteks ke database (`core/tenancy/session.py`)
```python
@event.listens_for(Session, "after_begin")  # Session sinkron di balik AsyncSession
def _bind_tenant(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    ctx = get_context()
    session.info["rls_tenant_id"] = ctx.tenant_id if ctx else None
    if ctx is not None:
        connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(ctx.tenant_id)})
```
- `is_local = true` = semantik `SET LOCAL` (PRD §6.4): hilang di akhir transaksi, tak bocor antar klien PgBouncer.
  **Dilarang** `SET` / `set_config(..., false)` level sesi untuk `app.*`. Tanpa konteks → tidak di-set (fail-closed).
- `async ensure_tenant_bound(session)` dipanggil repository sebelum query: `await session.connection()` lalu, bila
  `info["rls_tenant_id"] != require_tenant_id()` (transaksi dimulai sebelum konteks diset, mis. lookup auth), jalankan
  `set_config` yang sama di transaksi aktif — tanpa ini query pertama setelah auth diam-diam mengembalikan 0 baris.

### 4. `BaseRecord` (`framework/record.py`) — mixin: `class Product(BaseRecord, Base)`
| Kolom | Tipe | Aturan |
|---|---|---|
| `id` | UUID PK | default `uuid7()`; klien boleh mengirim id (offline T1, PRD §6.6) |
| `tenant_id` | UUID NOT NULL | default `require_tenant_id()`; tidak pernah dari input / schema request |
| `created_at`, `updated_at` | timestamptz NOT NULL | server default `now()`; `updated_at` juga `onupdate=func.now()` |
| `created_by`, `updated_by` | UUID NULL | `default` / `onupdate` = `current_user_id()` |
| `deleted_at`, `deleted_by`, `delete_batch_id` | timestamptz / UUID / UUID, NULL | hanya diisi `soft_delete` |
| `version` | INTEGER NOT NULL | server default `1`; `version_id_col` (naik di setiap UPDATE) |
| `custom_fields` | JSONB NOT NULL | server default `'{}'::jsonb`; ubah dengan assign dict baru (tanpa `MutableDict`) |

- `__mapper_args__` (`declared_attr.directive`) = `{"version_id_col": <kolom version>, "eager_defaults": True}`;
  `eager_defaults` wajib di async agar nilai server diambil via `RETURNING`, bukan lazy load (MissingGreenlet).
- Tanpa indeks otomatis (hindari indeks ganda; storage Neon kecil): tiap model wajib ≥ 1 indeks berawalan `tenant_id`,
  unique parsial `(tenant_id, …) WHERE deleted_at IS NULL` (meta-test §8). FK ke `core_tenants` menyusul. `company_id`
  → `CompanyScoped` (P1-02, filter aplikasi saja). Tabel global (tenant, identitas user, platform) tanpa BaseRecord/RLS.

### 5. RLS per tabel bisnis (`core/tenancy/rls.py`)
```sql
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {t}
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```
- `tenant_rls_sql(table)` / `drop_tenant_rls_sql(table)` → `list[str]` (murni, dipakai fixture test) +
  `enable_tenant_rls(table)` / `disable_tenant_rls(table)` (`op.execute`). Downgrade: `DROP POLICY IF EXISTS`,
  `NO FORCE`, `DISABLE`. Nama tabel divalidasi `^[a-z][a-z0-9_]*$` (anti-injeksi).
- `NULLIF` wajib: setelah pernah di-set dalam sesi, `current_setting(..., true)` bernilai `''`, bukan NULL. Tanpa
  konteks → `tenant_id = NULL` → 0 baris, INSERT/UPDATE melanggar WITH CHECK. `current_setting` STABLE → indeks dipakai.

### 6. Role database
- **owner** (idealnya BYPASSRLS) → Alembic (`DATABASE_URL_DIRECT`); migrasi data tanpa BYPASSRLS wajib set konteks per
  tenant (owner + FORCE = 0 baris, no-op senyap). **`app_runtime`** (LOGIN NOSUPERUSER NOBYPASSRLS, DML saja +
  `ALTER DEFAULT PRIVILEGES`) → aplikasi. **`app_rls_test`** (NOLOGIN NOSUPERUSER NOBYPASSRLS) → test via `SET ROLE`.
- Superuser & BYPASSRLS **selalu** melewati RLS, termasuk FORCE. Provisioning `app_runtime` di Neon = **gerbang PO**.
  Sampai selesai, bila `SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user` bernilai `true`,
  RLS **belum aktif** di lingkungan itu — hanya filter aplikasi yang melindungi.

### 7. `TenantRepository[T]` (`framework/repository.py`)
```python
class TenantRepository[T: BaseRecord]:
    model: type[T]  # subclass: model = Product; __init__(self, session: AsyncSession)
    def select(self) -> Select[tuple[T]]: ...  # sudah berisi tenant_id = konteks AND deleted_at IS NULL
    async def get(self, id: UUID) -> T | None: ...
    async def list(self, *, page: int = 1, page_size: int = 50) -> Page[T]: ...  # page_size <= 200
    async def create(self, values: Mapping[str, Any]) -> T: ...
    async def update(self, id: UUID, values: Mapping[str, Any], *, expected_version: int) -> T: ...
    async def soft_delete(self, id: UUID, *, expected_version: int, batch_id: UUID | None = None) -> UUID: ...
```
- Tiap metode memanggil `ensure_tenant_bound()`; query subclass disusun dari `self.select()`, bukan `sa.select(Model)`.
  Kolom sistem (`tenant_id`, `version`, `created_*`, `updated_*`, `deleted_*`) ditolak di `values`. Record tenant lain
  → `None`/404 (bukan 403). Repository hanya `flush()`; commit milik dependency sesi/service.
- `update`: `version` termuat ≠ `expected_version` (`If-Match`; `version_id_col` hanya memakai versi termuat) →
  `VersionConflict`; balapan sesudahnya = `StaleDataError` saat `flush()` → `VersionConflict`. Handler global:
  404 `record.not_found`, 409 `record.version_conflict` (Problem Details, pesan i18n id+en).
- `soft_delete`: isi `deleted_*`, `delete_batch_id = batch_id or uuid7()`, kembalikan batch id (restore massal §7.0);
  `version`/`updated_at` ikut naik → terlihat oleh sinkron (§6.6). `list`: offset, `ORDER BY id DESC` (UUIDv7 ≈
  waktu), `Page` = `items` + `has_more` (ambil `page_size + 1`, tanpa `COUNT`).

### 8. Strategi test
- PostgreSQL nyata (CI service / `scripts/dev_pg.sh`); tanpa `DATABASE_URL` skip di lokal, **gagal** bila `CI=true`.
  Engine fixture `admin` (superuser: DDL, role, seed) + `app` yang `SET ROLE app_rls_test` saat connect → **semua**
  test integrasi berjalan dengan RLS aktif (aman: tanpa pooler). Role & tabel uji dibuat idempotent.
- Wajib: guard `rolsuper = rolbypassrls = false` untuk role aktif (cegah hijau palsu); tanpa konteks → 0 baris & INSERT
  ditolak; A tak bisa SELECT/UPDATE/DELETE baris B; INSERT `tenant_id` B di konteks A ditolak; konteks hilang setelah
  commit; `ensure_tenant_bound`; soft delete tersembunyi; konflik versi → 409; `uuid7()` versi/varian/urutan.
  Meta-test setelah `alembic upgrade head`: tiap tabel ber-`tenant_id` punya RLS + FORCE, policy `tenant_isolation`,
  dan ≥ 1 indeks berawalan `tenant_id`.

## Konsekuensi
- Modul cukup `class X(BaseRecord, Base)` + `enable_tenant_rls()` di migrasi; lupa set konteks = error / 0 baris, bukan
  kebocoran. Biaya: 1 round-trip `set_config` per transaksi. Kerja lintas tenant (purge trial) wajib loop per tenant.
- Risiko utama: RLS produksi baru efektif setelah `app_runtime` diprovisikan (gerbang PO); sampai itu hanya lapis 1.
  PK unik global: id klien yang bentrok lintas tenant → 409 (tebakan tidak praktis, 74 bit acak).

## Alternatif yang ditolak
- **Schema/DB per tenant** — migrasi N skema & pool per tenant, mahal di Neon free; tetap opsi enterprise P3 (PRD §6.4).
- **RLS saja tanpa filter aplikasi** — tak efektif selama role bypass (CI superuser, role default Neon), gagal senyap
  (0 baris) sulit di-debug; PRD §6.4 mewajibkan berlapis. **Filter aplikasi saja** — satu bug query = kebocoran.
- **`SET LOCAL app.tenant_id = '…'`** — tidak menerima bind parameter, nilai harus diinterpolasi (risiko injeksi);
  `set_config(..., true)` setara dan terparameter. **`SET` level sesi / saat checkout** — bocor di PgBouncer transaksi.
- **Set per query (`do_orm_execute`)** — round-trip ekstra. **PK komposit `(tenant_id, id)`** — FK rumit. **`uuidv7()`
  PostgreSQL 18 / pustaka `uuid6`** — id harus dibuat klien (offline) dan tanpa dependensi baru.
