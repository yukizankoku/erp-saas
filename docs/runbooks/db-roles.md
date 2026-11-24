# Runbook — Role database & RLS

Rujukan: ADR 0004 §5–§6, PRD §6.4, §6.7. Jalur sensitif: perubahan isi runbook ini wajib review `security`.

> **Gerbang PO.** Membuat/mengubah role di database bersama (Neon preview/staging/produksi) dan menyimpan
> password barunya (secret baru) butuh persetujuan PO. Agent hanya menjalankan SQL ini di database uji lokal/CI.

## Ringkasan role

| Role | Atribut | Dipakai oleh | Env |
|---|---|---|---|
| owner (mis. `neondb_owner`) | pemilik tabel; idealnya BYPASSRLS | Alembic (DDL + migrasi data) | `DATABASE_URL_DIRECT` / `DATABASE_URL_UNPOOLED` |
| `app_runtime` | LOGIN NOSUPERUSER NOBYPASSRLS, DML saja | aplikasi (koneksi pooled) | `DATABASE_URL` |
| `app_rls_test` | NOLOGIN NOSUPERUSER NOBYPASSRLS | test integrasi (`SET ROLE`, dibuat otomatis fixture) | — |

- Superuser dan role BYPASSRLS **selalu** melewati RLS, termasuk `FORCE ROW LEVEL SECURITY`. Selama aplikasi
  masih memakai role seperti itu, isolasi tenant hanya lapis 1 (filter repository) — RLS belum efektif.
- Pemilik tabel tanpa BYPASSRLS tetap terkena RLS karena setiap tabel tenant memakai `FORCE`.
- **Jangan** memberi `TRUNCATE` ke `app_runtime`: TRUNCATE tidak melewati policy RLS.
- Alembic membaca URL hanya dari `-x db_url=...` / env proses (`DATABASE_URL_UNPOOLED` → `DATABASE_URL_DIRECT` →
  `DATABASE_URL`), bukan `.env.local`. Ke Neon harus eksplisit dari `backend/`:
  `(set -a; . ../.env.local; set +a; uv run alembic upgrade head)` — cek host tujuan yang dicetak ke stderr.

## 1. Provisioning `app_runtime` (idempotent)

Jalankan dengan `psql` sebagai **owner** (role pembuat tabel lewat Alembic) pada koneksi *direct*:

```bash
psql "$DATABASE_URL_DIRECT" -v owner=neondb_owner -v dbname=neondb -f app_runtime.sql
```

Skrip sengaja **tidak** menyebut `NOSUPERUSER`/`NOBYPASSRLS`/`NOREPLICATION`/`NOCREATEDB`: di PostgreSQL 16+ owner
non-superuser (mis. `neondb_owner`) ditolak bila menyebut atribut itu di `ALTER ROLE`, walau nilainya "NO". Atribut
default `CREATE ROLE` sudah aman; DO-block memverifikasinya dan menghentikan skrip bila tidak (perbaikan butuh
superuser — eskalasi ke PO). Teruji di PostgreSQL 16 dengan owner non-superuser (CREATEROLE CREATEDB), dua kali jalan.

`app_runtime.sql`:

```sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
    CREATE ROLE app_runtime LOGIN;  -- default: NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION
  END IF;
END
$$;

-- Hentikan bila role (sudah ada / dibuat pihak lain) dapat melewati RLS atau mengelola role/database.
DO $$
DECLARE
  r record;
  privileged text[];
BEGIN
  SELECT oid, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication
    INTO STRICT r FROM pg_roles WHERE rolname = 'app_runtime';
  IF r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication THEN
    RAISE EXCEPTION 'app_runtime has unsafe attributes (super=%, bypassrls=%, createrole=%, createdb=%, replication=%)',
      r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication;
  END IF;
  SELECT array_agg(g.rolname::text ORDER BY g.rolname) INTO privileged
    FROM pg_roles g
   WHERE g.oid <> r.oid AND pg_has_role(r.oid, g.oid, 'MEMBER')
     AND (g.rolsuper OR g.rolbypassrls OR g.rolcreaterole OR g.rolcreatedb OR g.rolreplication);
  IF privileged IS NOT NULL THEN
    RAISE EXCEPTION 'app_runtime is a member of privileged roles: %', privileged;
  END IF;
END
$$;
ALTER ROLE app_runtime LOGIN;

GRANT CONNECT ON DATABASE :"dbname" TO app_runtime;
-- PostgreSQL < 15 (dan database hasil upgrade darinya) memberi CREATE di schema public ke PUBLIC.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_runtime;
REVOKE CREATE ON SCHEMA public FROM app_runtime;

-- Tabel & sequence yang sudah ada.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;
-- Tabel & sequence yang dibuat migrasi berikutnya oleh owner.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

-- Aplikasi tidak boleh mengubah status migrasi.
DO $$
BEGIN
  IF to_regclass('public.alembic_version') IS NOT NULL THEN
    REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM app_runtime;
  END IF;
END
$$;

-- Verifikasi akhir: gagal = skrip berhenti dengan error.
DO $$
BEGIN
  IF has_schema_privilege('public', 'public', 'CREATE')
     OR has_schema_privilege('app_runtime', 'public', 'CREATE') THEN
    RAISE EXCEPTION 'schema public is still writable (CREATE) by PUBLIC or app_runtime';
  END IF;
  IF COALESCE(has_table_privilege('app_runtime', to_regclass('public.alembic_version'),
                                  'INSERT, UPDATE, DELETE, TRUNCATE'), false) THEN
    RAISE EXCEPTION 'app_runtime can still modify public.alembic_version';
  END IF;
END
$$;
```

**Setelah migrasi pertama:** `alembic_version` dibuat owner oleh `alembic upgrade head` pertama, dan `ALTER DEFAULT
PRIVILEGES` di atas otomatis memberi `INSERT, UPDATE, DELETE` ke `app_runtime`. Bila skrip dijalankan sebelum migrasi
pertama, **jalankan ulang skrip yang sama** (idempotent) sesudahnya, lalu ulangi verifikasi §2 — `can_touch_alembic`
harus `f`.

Password: set lewat `\password app_runtime` di psql (dikirim sebagai hash, tidak masuk log/history), simpan
langsung di secret manager (Vercel/hosting) sebagai `DATABASE_URL` pooled. Jangan menulis password ke file, commit,
issue, atau laporan.

**Neon:** buat `app_runtime` lewat SQL di atas, bukan lewat Console/API — role dari Console/API otomatis menjadi
anggota `neon_superuser` (dapat `SET ROLE` ke role ber-BYPASSRLS). DO-block di atas dan query verifikasi mendeteksinya.

## 2. Verifikasi (wajib setelah provisioning, setelah migrasi pertama, dan setelah mengganti `DATABASE_URL`)

Sebagai owner:

```sql
SELECT r.rolname, r.rolcanlogin, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication,
       ARRAY(SELECT g.rolname FROM pg_roles g
             WHERE g.oid <> r.oid AND pg_has_role(r.oid, g.oid, 'MEMBER')
               AND (g.rolsuper OR g.rolbypassrls)) AS privileged_memberships,
       (SELECT count(*) FROM pg_class c WHERE c.relowner = r.oid) AS owned_relations,
       has_schema_privilege('public', 'public', 'CREATE') AS public_can_create,
       has_schema_privilege(r.oid, 'public', 'CREATE') AS runtime_can_create,
       has_table_privilege(r.oid, to_regclass('public.alembic_version'), 'INSERT, UPDATE, DELETE, TRUNCATE')
         AS can_touch_alembic
FROM pg_roles r WHERE r.rolname = 'app_runtime';
-- Harus: t | f | f | f | f | f | {} | 0 | f | f | f
-- (can_touch_alembic NULL = alembic_version belum ada → ulangi setelah migrasi pertama)
```

Dari koneksi aplikasi (`DATABASE_URL`):

```sql
SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
-- Harus: app_runtime | f | f
SELECT count(*) FROM <tabel_tenant>;          -- tanpa konteks tenant: harus 0
```

Rollback provisioning (hanya bila belum dipakai aplikasi): `DROP OWNED BY app_runtime; DROP ROLE app_runtime;`
dijalankan owner di setiap database tempat role diberi hak.

## 3. Pedoman objek database di bawah RLS

- Tabel tenant baru: model `BaseRecord`, indeks berawalan `tenant_id`, lalu `enable_tenant_rls("<tabel>")` di
  `upgrade()` dan `disable_tenant_rls("<tabel>")` di `downgrade()` (`app/core/tenancy/rls.py`). Meta-test
  `tests/integration/test_rls_meta.py` gagal bila ada tabel ber-`tenant_id` tanpa RLS + FORCE + policy + indeks.
- View di atas tabel tenant wajib `WITH (security_invoker = true)`; tanpa itu view berjalan dengan hak pemiliknya.
- Hindari fungsi `SECURITY DEFINER` yang membaca tabel tenant; bila terpaksa, filter `tenant_id` eksplisit dan
  review `security`.
- Jangan `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` sementara untuk pekerjaan data, dan jangan `SET row_security`.

## 4. Pedoman migrasi data di bawah RLS

Migrasi data **terpisah** dari migrasi skema (satu revisi Alembic sendiri). Bila owner tidak ber-BYPASSRLS, setiap
DML tanpa konteks tenant adalah **no-op senyap** (owner + FORCE = 0 baris), jadi:

1. Iterasi tenant dari tabel global (tanpa RLS), set konteks per tenant dengan `set_config(..., true)`.
2. Tetap tulis `WHERE tenant_id = :tid` eksplisit — benar baik di bawah RLS maupun saat owner ber-BYPASSRLS.
3. Verifikasi hasil per tenant (sisa baris yang belum termigrasi harus 0) dan `raise` bila tidak — jangan
   mengandalkan rowcount 0 sebagai "tidak ada data".
4. Kosongkan konteks di akhir: seluruh migrasi Alembic berjalan dalam satu transaksi, jadi nilai `is_local`
   bertahan sampai commit dan bisa terbawa ke revisi berikutnya.
5. Tabel besar: proses bertahap (batch per tenant / per N baris) lewat job, bukan satu migrasi panjang.

```python
def upgrade() -> None:
    conn = op.get_bind()
    set_tenant = sa.text("SELECT set_config('app.tenant_id', :tid, true)")
    for tenant_id in conn.execute(sa.text("SELECT id FROM core_tenants")).scalars():
        conn.execute(set_tenant, {"tid": str(tenant_id)})
        conn.execute(
            sa.text("UPDATE inv_products SET uom = 'pcs' WHERE tenant_id = :tid AND uom IS NULL"),
            {"tid": tenant_id},
        )
        remaining = conn.execute(
            sa.text("SELECT count(*) FROM inv_products WHERE tenant_id = :tid AND uom IS NULL"),
            {"tid": tenant_id},
        ).scalar_one()
        if remaining:
            raise RuntimeError(f"data migration incomplete for tenant {tenant_id}: {remaining} rows")
    conn.execute(set_tenant, {"tid": ""})
```

Pengujian: di CI migrasi berjalan sebagai superuser (melewati RLS), sehingga lupa set konteks **tidak** terdeteksi di
sana. Uji migrasi data juga dengan role pemilik tanpa BYPASSRLS (mis. `SET ROLE` ke role non-superuser pemilik
tabel uji) atau di branch Neon preview, dan bandingkan jumlah baris per tenant sebelum/sesudah.
