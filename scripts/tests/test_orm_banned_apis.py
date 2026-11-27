import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_orm_banned_apis as chk


def violations(src: str) -> list[str]:
    return [msg for _, msg in chk.find_violations(src)]


@pytest.mark.parametrize(
    "src",
    [
        "c = insert(Item).values(name='x').returning(Item.id).cte()",
        "c = sa.insert(Item).values(name='x').cte('c')",
        "c = pg_insert(Item).values(name='x').returning(Item.id).cte()",
        "c = update(Item).where(Item.id == 1).values(name='x').returning(Item.id).cte()",
        "c = sa.delete(Item).returning(Item.id).cte()",
        "c = sa.cte(insert(Item).values(name='x').returning(Item.id))",
        "c = cte(update(Item).values(name='x').returning(Item.id), name='u')",
    ],
)
def test_dml_cte_is_banned(src: str) -> None:
    assert violations(src) == ["DML di dalam CTE pada statement ORM"]


@pytest.mark.parametrize(
    "src",
    [
        "from sqlalchemy.orm import make_transient_to_detached",
        "from sqlalchemy.orm import make_transient_to_detached as mtd",
        "import sqlalchemy.orm as o\no.make_transient_to_detached(obj)",
    ],
)
def test_make_transient_to_detached_is_banned(src: str) -> None:
    assert "make_transient_to_detached" in violations(src)


@pytest.mark.parametrize(
    "src",
    [
        "session.merge(obj, load=False)",
        "await s.merge(obj, options=[], load=False)",
        "session.merge(obj, load=flag)",
        "session.merge(obj, **kw)",
    ],
)
def test_merge_without_load_is_banned(src: str) -> None:
    assert violations(src) == ["merge(..., load=False)"]


def test_merge_all_without_load_is_banned() -> None:
    assert violations("session.merge_all(objs, load=False)") == ["merge_all(..., load=False)"]


@pytest.mark.parametrize(
    "src",
    [
        "q = select(Item.id).cte()",
        "q = sa.select(Item).where(Item.id == 1).cte('x')",
        "session.merge(obj)",
        "session.merge(obj, load=True)",
        "session.merge_all(objs)",
        "c = sa.cte(select(Item.id))",
        "d.update(x=1)",
        "stmt = insert(Item).values(name='x').returning(Item.id)",
        "x = make_transient(obj)",
    ],
)
def test_allowed_code_passes(src: str) -> None:
    assert violations(src) == []


def test_main_scans_tree_and_skips_framework(tmp_path: Path) -> None:
    bad = tmp_path / "app" / "modules" / "inv" / "services.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("session.merge(obj, load=False)\n")
    internal = tmp_path / "app" / "framework" / "repository.py"
    internal.parent.mkdir(parents=True)
    internal.write_text("from sqlalchemy.orm import make_transient_to_detached\n")
    assert chk.main([str(tmp_path / "app")]) == 1
    nested = tmp_path / "app" / "modules" / "x" / "framework" / "util.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("from sqlalchemy.orm import make_transient_to_detached\n")
    bad.write_text("session.merge(obj)\n")
    assert chk.main([str(tmp_path / "app")]) == 1  # hanya <root>/framework yang dikecualikan
    nested.write_text("x = 1\n")
    assert chk.main([str(tmp_path / "app")]) == 0


def test_missing_path_is_an_error(tmp_path: Path) -> None:
    assert chk.main([str(tmp_path / "nope")]) == 2


def test_application_code_is_clean() -> None:
    assert chk.main([]) == 0
