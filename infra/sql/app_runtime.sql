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

-- Password (opsional): dikirim lewat variabel psql `app_runtime_password`, tidak pernah ditulis di file/log.
-- Contoh: psql "$OWNER_URL" -v owner=neondb_owner -v dbname=neondb -v app_runtime_password="$PW" -f app_runtime.sql
\if :{?app_runtime_password}
ALTER ROLE app_runtime PASSWORD :'app_runtime_password';
\endif
