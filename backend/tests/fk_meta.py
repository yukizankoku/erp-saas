"""Pemeriksa meta skema: UNIQUE `(tenant_id, id)` dan FK komposit antar-tabel ber-tenant (#37)."""

from sqlalchemy import MetaData, Table, UniqueConstraint


def _has_tenant_id(table: Table) -> bool:
    return "tenant_id" in table.c


def check_tenant_fk_rules(metadata: MetaData) -> list[str]:
    """Kembalikan daftar pelanggaran (kosong = patuh)."""
    problems: list[str] = []
    for table in metadata.tables.values():
        if not _has_tenant_id(table):
            continue
        uniques = {
            tuple(c.name for c in con.columns)
            for con in table.constraints
            if isinstance(con, UniqueConstraint)
        }
        if ("tenant_id", "id") not in uniques:
            problems.append(f"{table.name}: tidak ada UNIQUE (tenant_id, id)")
        for fk_con in table.foreign_key_constraints:
            referred = fk_con.referred_table
            if not _has_tenant_id(referred):
                continue
            local = [c.name for c in fk_con.columns]
            remote = [e.column.name for e in fk_con.elements]
            if "tenant_id" not in local or "tenant_id" not in remote or len(local) < 2:
                problems.append(
                    f"{table.name}: FK ke {referred.name} bukan komposit tenant_id "
                    f"({local} -> {remote})"
                )
            elif local.index("tenant_id") != remote.index("tenant_id"):
                problems.append(f"{table.name}: FK ke {referred.name} salah urutan tenant_id")
    return problems
