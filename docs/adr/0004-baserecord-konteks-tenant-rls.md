# 0004 — BaseRecord, konteks tenant, dan RLS

**Status:** Diusulkan (24 Nov 2026; direvisi pasca review `security` PR #33: savepoint, 409 id bentrok, URL migrasi)
— provisioning role DB produksi = gerbang PO ·
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
    if transaction.nested:  # SAVEPOINT: warisi binding induk, jangan set GUC/info
        if ctx is not None and ctx.tenant_id != session.info.get("rls_tenant_id"):
            raise TenantRebindError(...)
        return
    session.info["rls_tenant_id"] = ctx.tenant_id if ctx else None
    if ctx is not None:
        connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(ctx.tenant_id)})
```
- `is_local = true` = semantik `SET LOCAL` (PRD §6.4): hilang di akhir transaksi, tak bocor antar klien PgBouncer.
  **Dilarang** `SET` / `set_config(..., false)` level sesi untuk `app.*`. Tanpa konteks → tidak di-set (fail-closed).
- `async ensure_tenant_bound(session)` dipanggil repository sebelum query: `await session.connection()` lalu, bila
  `info["rls_tenant_id"] != require_tenant_id()` (transaksi dimulai sebelum konteks diset, mis. lookup auth), jalankan
  `set_config` yang sama di transaksi aktif — tanpa ini query pertama setelah auth diam-diam mengembalikan 0 baris.
  Sudah terikat ke tenant lain → `TenantRebindError` (satu transaksi per tenant).
- **Savepoint (`begin_nested`) tidak pernah mengikat/memindah tenant.** `set_config` di savepoint batal oleh
  `ROLLBACK TO SAVEPOINT` sedangkan `session.info` tidak → binding & GUC tak sinkron (0 baris senyap, atau transaksi
  tenant A berjalan sebagai B). Konteks ≠ binding induk (termasuk induk belum terikat) → `TenantRebindError` saat
  savepoint mulai; `ensure_tenant_bound` juga menolak bind pertama di dalam savepoint. Konteks kosong di savepoint =
  binding induk tetap. Ikat tenant (repository / `ensure_tenant_bound`) **sebelum** `begin_nested()`.

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

### 4a. FK komposit antar-tabel bisnis (amandemen, #37)
FK dan unique PostgreSQL **melewati RLS**: tanpa pengaman, record bisa merujuk baris tenant lain dan pesan error FK
menjadi oracle keberadaan. Aturannya:
- Setiap tabel `BaseRecord` otomatis punya `UNIQUE (tenant_id, id)` bernama `uq_<tabel>_tenant_id_id` (event mapper
  `instrument_class` pada mixin; `__table_args__` subclass tidak tersentuh). Ini target FK komposit.
- FK ke tabel bisnis wajib komposit `(tenant_id, x_id) → (tenant_id, id)`: `tenant_fk("tabel_induk", "x_id",
  ondelete=...)` di `__table_args__` + kolom `tenant_fk_column()`. FK satu kolom ke tabel ber-`tenant_id` ditolak meta-test
  (`tests/test_tenant_fk_meta.py` untuk metadata app & uji; `test_rls_meta.py` via `pg_constraint` pada skema hasil migrasi).
  FK nullable memakai MATCH SIMPLE (NULL = tidak dicek), aman karena `tenant_id` NOT NULL. `ondelete` hanya
  `CASCADE`/`RESTRICT`/`NO ACTION`: `SET NULL`/`SET DEFAULT` juga mengubah `tenant_id` sehingga ditolak helper.
- Repository: `create`/`update` mengisolasi flush dalam savepoint; sqlstate `23503` → `RecordReferenceInvalid`
  (422 `record.reference_invalid`), pesan identik untuk target tak ada maupun milik tenant lain (tanpa oracle); tulisan
  lain di transaksi tetap utuh. Kesalahan FK akibat delete/restrict di sisi induk bukan dari input klien dan tetap `IntegrityError`.
- Biaya: satu indeks unique tambahan `(tenant_id, id)` per tabel (dipakai juga sebagai indeks berawalan `tenant_id`).

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
  tenant (owner + FORCE = 0 baris, no-op senyap). **`app_runtime`** (LOGIN NOSUPERUSER NOBYPASSRLS, DML saja + `ALTER
  DEFAULT PRIVILEGES`) → aplikasi. **`app_rls_test`** (NOLOGIN NOSUPERUSER NOBYPASSRLS) → test via `SET ROLE`.
- Superuser & BYPASSRLS **selalu** melewati RLS, termasuk FORCE. Provisioning `app_runtime` di Neon = **gerbang PO**;
  sampai selesai, bila `rolsuper OR rolbypassrls` milik `current_user` = true, RLS **belum aktif** (hanya lapis 1).
- **URL Alembic** hanya dari koneksi pemanggil (`config.attributes["connection"]`), `-x db_url=...`, atau env proses
  `DATABASE_URL_UNPOOLED` → `DATABASE_URL_DIRECT` → `DATABASE_URL` (`core/migration_target.py`) — bukan `Settings`,
  yang juga membaca `.env.local` (Neon dari `neon env pull`). Tujuan (host/port/db/user, tanpa password) dicetak ke
  stderr sebelum migrasi; ke Neon wajib eksplisit `set -a; . ../.env.local; set +a`.
- Runbook `docs/runbooks/db-roles.md`: owner non-superuser PG16+ tak boleh menyebut `NOSUPERUSER`/`NOBYPASSRLS`/…
  di `ALTER ROLE` → DO-block memverifikasi atribut & keanggotaan lalu `RAISE EXCEPTION` bila tidak aman.

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
- `create` dengan `id` yang sudah dipakai (PK global; tenant sendiri, terhapus, **atau** tenant lain) → unique
  violation pada `pk_<tabel>` → `rollback()` sesi (di dalam savepoint pemanggil: hanya savepoint itu yang batal) +
  `RecordConflict` → 409 `record.conflict`, kode & pesan identik untuk semua kasus (tidak membocorkan baris tenant
  lain). Klien sinkron: `GET` id itu; 200 = sudah tersimpan (retry), 404 = buat id baru. Unique violation lain tetap
  `IntegrityError` (dipetakan per modul).
- `update`: `version` termuat ≠ `expected_version` (`If-Match`; `version_id_col` hanya memakai versi termuat) →
  `VersionConflict`; balapan sesudahnya = `StaleDataError` saat `flush()` → `VersionConflict`. Handler global:
  404 `record.not_found`, 409 `record.version_conflict` / `record.conflict` (Problem Details, pesan i18n id+en).
- `soft_delete`: isi `deleted_*` + `delete_batch_id = batch_id or uuid7()`, kembalikan batch id (restore massal §7.0);
  `version` ikut naik → terlihat sinkron (§6.6). `list`: offset, `ORDER BY id DESC`, `items` + `has_more` (tanpa `COUNT`).

### 7a. Filter tenant otomatis di ORM (amandemen, #36)
Lapis 1 tidak lagi hanya konvensi `TenantRepository.select()`: `core/tenancy/orm_filter.py` terdaftar global pada
`Session` (diimpor oleh `session.py`, jadi ikut setiap pemakai repository).
- `do_orm_execute`: **setiap** SELECT / bulk UPDATE / bulk DELETE ORM mendapat `with_loader_criteria(BaseRecord,
  tenant_id = <konteks>)` bila ada konteks (tanpa syarat `all_mappers`, yang tidak melihat subquery/EXISTS/UNION/`count()`);
  mencakup lazy load, selectin, refresh, alias. Nilai tenant = bound parameter (cache dibagi antar tenant). Tanpa
  konteks → `TenantContextMissing` bila statement mereferensikan tabel milik `BaseRecord`; statement lain jalan normal.
  Tidak ada opsi bypass (§2).
- Tulis (deny by default, `TenantMismatchError`): INSERT ke tabel `BaseRecord` tidak boleh membawa `tenant_id` eksplisit
  (`.values()`, bindparam, params dict/list; sumber satu-satunya = default kolom dari konteks), tanpa baris posisional,
  tanpa `INSERT ... FROM SELECT`, tanpa `ON CONFLICT` (upsert masa depan = helper repository, bukan statement bebas).
  UPDATE/DELETE: params berupa list (bulk-by-PK) ditolak; `tenant_id` di SET UPDATE ditolak. `TenantRepository.create`
  mengisi `tenant_id` pada objek ORM lewat flush (unit-of-work), bukan `insert()`, sehingga tidak terpengaruh.
- Identity map: `Session.get` dibungkus — tanpa konteks → error; hit milik tenant lain → expunge + `None`. Awal transaksi
  non-nested dan `ensure_tenant_bound` meng-expunge instance `BaseRecord` yang `tenant_id`-nya asing **atau tidak termuat**
  (expired; dimuat ulang lewat filter bila dipakai lagi).
- `before_flush`: objek baru/berubah/dihapus dengan `tenant_id` ≠ konteks → `TenantMismatchError`; objek persistent
  yang `tenant_id`-nya tidak termuat atau berubah (history) ditolak; flush tanpa konteks → `TenantContextMissing`.
  Transaksi yang sudah terikat tenant lain dipakai di konteks berbeda → `TenantRebindError` (execute, get, flush, merge).
  Binding `session.info` dibersihkan di akhir transaksi root.
- **Batas:** statement Core, `text()`, dan objek `Table` tidak difilter → lapis 2 (RLS) penahan terakhir + aturan kode
  bisnis hanya lewat repository. `session.query(...).get()` (legacy) melewati pembungkus `get`: `session.query` dilarang
  di kode bisnis. Test lapis 1 berjalan di engine superuser (RLS tidak berlaku).
- **Celah lapis 1 yang diketahui** (review security PR #44, keputusan PO 28 Nov: merge bertahap, lapis 2 aktif):
  DML di dalam CTE dan UPDATE/DELETE unit-of-work pada objek yang identitasnya dibuat manual
  (`make_transient_to_detached`, `merge(load=False)`). Ketiganya **dilarang di `backend/app/**`** (kecuali
  `app/framework/`) oleh `scripts/check_orm_banned_apis.py` (AST, langkah CI `backend`). Pemeriksaan hanya melihat
  rantai langsung `insert(...)….cte()`; statement DML yang disimpan di variabel lalu `.cte()` tidak terdeteksi →
  review kode. Perbaikan di lapis 1 dilacak di issue lanjutan.

### 8. Strategi test
- PostgreSQL nyata (CI service / `scripts/dev_pg.sh`); tanpa `DATABASE_URL` skip di lokal, **gagal** bila `CI=true`.
  Engine fixture `admin` (superuser: DDL, role, seed) + `app` yang `SET ROLE app_rls_test` saat connect → **semua**
  test integrasi berjalan dengan RLS aktif (aman: tanpa pooler). Role & tabel uji dibuat idempotent.
- Wajib: guard `rolsuper = rolbypassrls = false` untuk role aktif (cegah hijau palsu); tanpa konteks → 0 baris & INSERT
  ditolak; A tak bisa SELECT/UPDATE/DELETE baris B; INSERT `tenant_id` B di konteks A ditolak; konteks hilang setelah
  commit **dan rollback**; `ensure_tenant_bound`; savepoint tak bisa memindah/mengikat tenant; FORCE berlaku untuk
  pemilik tabel non-superuser; soft delete tersembunyi; konflik versi → 409; id bentrok (tenant sendiri/lain) → 409
  identik; `uuid7()` versi/varian/urutan; pemilih URL migrasi mengabaikan env file.
- Meta-test pasca `alembic upgrade head`: tiap tabel ber-`tenant_id` punya RLS + FORCE, policy, indeks awal `tenant_id`.
  `qual` & `with_check` di `pg_policies` dibandingkan **persis** dengan deparse predikat
  `(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)` — bukan LIKE, yang
  meloloskan `(<predikat>) OR true`; test negatif membuktikan policy longgar ditolak.

## Konsekuensi
- Modul cukup `class X(BaseRecord, Base)` + `enable_tenant_rls()` di migrasi; lupa set konteks = error / 0 baris, bukan
  kebocoran. Biaya: 1 round-trip `set_config` per transaksi. Kerja lintas tenant (purge trial) wajib loop per tenant.
- Risiko utama: RLS produksi baru efektif setelah `app_runtime` diprovisikan (gerbang PO); sampai itu hanya lapis 1.
  PK unik global: id klien yang bentrok lintas tenant → 409 `record.conflict` identik dengan bentrok di tenant
  sendiri (tebakan tidak praktis, 74 bit acak); `create` yang bentrok me-rollback transaksi sesi — bungkus dengan
  `begin_nested()` bila pekerjaan lain di transaksi yang sama harus dipertahankan.

## Alternatif yang ditolak
- **Schema/DB per tenant** — migrasi N skema & pool per tenant, mahal di Neon free; tetap opsi enterprise P3 (PRD §6.4).
- **RLS saja tanpa filter aplikasi** — tak efektif selama role bypass (CI superuser, role default Neon), gagal senyap
  (0 baris) sulit di-debug; PRD §6.4 mewajibkan berlapis. **Filter aplikasi saja** — satu bug query = kebocoran.
- **`SET LOCAL app.tenant_id = '…'`** — tidak menerima bind parameter, nilai harus diinterpolasi (risiko injeksi);
  `set_config(..., true)` setara dan terparameter. **`SET` level sesi / saat checkout** — bocor di PgBouncer transaksi.
- **Set per query (`do_orm_execute`)** — round-trip ekstra. **PK komposit `(tenant_id, id)`** — FK rumit. **`uuidv7()`
  PostgreSQL 18 / pustaka `uuid6`** — id harus dibuat klien (offline) dan tanpa dependensi baru.
