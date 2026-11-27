"""Larang API ORM berisiko di kode aplikasi (keputusan PO #36, ADR 0004 §7a).

Filter tenant ORM (lapis 1) belum menutup tiga jalur berikut; lapis 2 (RLS) tetap menahannya,
tetapi kode bisnis tidak boleh memakainya sama sekali:

- DML di dalam CTE: `insert(...)/update(...)/delete(...)....cte(...)` (lolos cek tulis).
- `make_transient_to_detached` (identitas dibuat manual → UPDATE/DELETE tanpa kriteria tenant).
- `Session.merge(..., load=False)` (sama: objek tidak pernah dimuat lewat query terfilter).

Pemakaian: `python scripts/check_orm_banned_apis.py [path ...]` (default `backend/app`).
`backend/app/framework/` dikecualikan (internal framework). Exit 1 bila ada temuan.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

DML_FUNCS = frozenset({"insert", "update", "delete", "pg_insert"})
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "backend" / "app"
EXCLUDED_PARTS = ("framework",)


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


def _is_false(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def find_violations(source: str, filename: str = "<string>") -> Iterator[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "cte" and _chain_root_is_dml(node.func.value):
                yield node.lineno, "DML di dalam CTE pada statement ORM"
            if attr == "merge" and any(
                kw.arg == "load" and _is_false(kw.value) for kw in node.keywords
            ):
                yield node.lineno, "merge(..., load=False)"
        if isinstance(node, ast.Name | ast.Attribute):
            if _func_name(node) == "make_transient_to_detached":
                yield node.lineno, "make_transient_to_detached"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "make_transient_to_detached":
                    yield node.lineno, "make_transient_to_detached"


def _python_files(paths: list[Path]) -> Iterator[Path]:
    for path in paths:
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file in files:
            if not any(part in EXCLUDED_PARTS for part in file.parts):
                yield file


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or [DEFAULT_ROOT]
    found = 0
    for file in _python_files(paths):
        for lineno, message in find_violations(file.read_text(encoding="utf-8"), str(file)):
            print(f"{file}:{lineno}: API ORM terlarang: {message} (lihat ADR 0004 §7a, #36)")
            found += 1
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
