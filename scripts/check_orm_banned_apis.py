"""Larang API ORM berisiko di kode aplikasi (keputusan PO #36, ADR 0004 §7a).

Filter tenant ORM (lapis 1) belum menutup tiga jalur berikut; lapis 2 (RLS) tetap menahannya,
tetapi kode bisnis tidak boleh memakainya sama sekali:

- DML di dalam CTE: `insert(...)/update(...)/delete(...)....cte(...)` (lolos cek tulis).
- `make_transient_to_detached` (identitas dibuat manual → UPDATE/DELETE tanpa kriteria tenant).
- `Session.merge/merge_all(..., load=<selain True>)` (objek tidak dimuat lewat query terfilter).
- `session.query(...)` (API legacy; di luar jalur `select()` yang difilter `orm_filter`). Heuristik
  (AST, tanpa tipe): `X.query(...)` dengan nama akhir penerima `X` mengandung "session"
  (`session`, `self.session`, `db_session`), atau `.query(Model, ...)` dengan argumen pertama
  `Name` berawalan huruf kapital (kelas model). `db.query("...")` pada objek non-sesi dengan
  argumen string tidak ditandai (minim false positive; alias sesi bernama lain bisa lolos).

Pemakaian: `python scripts/check_orm_banned_apis.py [path ...]` (default `backend/app`).
`<root>/framework/` dikecualikan (internal framework). Exit 1 bila ada temuan; exit 2 bila path
tidak ada.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

DML_FUNCS = frozenset({"insert", "update", "delete", "pg_insert"})
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "backend" / "app"
MERGE_FUNCS = frozenset({"merge", "merge_all"})
EXCLUDED_DIR = "framework"  # relatif terhadap root pemindaian (mis. backend/app/framework)


def _func_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _chain_root_is_dml(node: ast.expr) -> bool:
    """`insert(X).values(...).returning(...)` → akar rantai pemanggilan adalah `insert(...)`."""
    while True:
        if isinstance(node, ast.Call):
            if _func_name(node.func) in DML_FUNCS:
                return True
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return False


def _is_legacy_query(node: ast.Call) -> bool:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "query"):
        return False
    receiver = _func_name(func.value)
    if receiver is not None and "session" in receiver.lower():
        return True
    first = node.args[0] if node.args else None
    return isinstance(first, ast.Name) and first.id[:1].isupper()


def _is_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _unsafe_merge(node: ast.Call) -> bool:
    """`load=` selain literal `True`, atau `**kwargs` (nilai `load` tidak diketahui)."""
    return any(
        kw.arg is None or (kw.arg == "load" and not _is_true(kw.value)) for kw in node.keywords
    )


def find_violations(source: str, filename: str = "<string>") -> Iterator[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _func_name(node.func)
            # `insert(...)….cte()` (metode) atau `sa.cte(insert(...)…)` (fungsi).
            if name == "cte" and (
                (isinstance(node.func, ast.Attribute) and _chain_root_is_dml(node.func.value))
                or (node.args and _chain_root_is_dml(node.args[0]))
            ):
                yield node.lineno, "DML di dalam CTE pada statement ORM"
            if _is_legacy_query(node):
                yield node.lineno, "session.query(...) (API legacy)"
            if name in MERGE_FUNCS and isinstance(node.func, ast.Attribute) and _unsafe_merge(node):
                yield node.lineno, f"{name}(..., load=False)"
        if isinstance(node, ast.Name | ast.Attribute):
            if _func_name(node) == "make_transient_to_detached":
                yield node.lineno, "make_transient_to_detached"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "make_transient_to_detached":
                    yield node.lineno, "make_transient_to_detached"


def _python_files(paths: list[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        excluded = path / EXCLUDED_DIR
        for file in sorted(path.rglob("*.py")):
            if not file.is_relative_to(excluded):
                yield file


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or [DEFAULT_ROOT]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"path tidak ditemukan: {', '.join(map(str, missing))}")
        return 2
    found = 0
    for file in _python_files(paths):
        for lineno, message in find_violations(file.read_text(encoding="utf-8"), str(file)):
            print(f"{file}:{lineno}: API ORM terlarang: {message} (lihat ADR 0004 §7a, #36)")
            found += 1
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
