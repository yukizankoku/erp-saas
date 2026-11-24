import time
from uuid import UUID

import pytest

from app.core.ids import uuid7, uuid7_unix_ms


def test_uuid7_version_and_variant_bits() -> None:
    for _ in range(1000):
        value = uuid7()
        assert isinstance(value, UUID)
        assert value.version == 7
        assert (value.int >> 76) & 0xF == 0x7
        assert (value.int >> 62) & 0b11 == 0b10  # varian RFC 9562
        assert value.variant == "specified in RFC 4122"


def test_uuid7_embeds_current_unix_ms() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= uuid7_unix_ms(value) <= after


def test_uuid7_is_time_ordered_across_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(1_700_000_000_000, 1_700_000_000_000 + 500))
    monkeypatch.setattr(time, "time_ns", lambda: next(clock) * 1_000_000)
    generated = [uuid7() for _ in range(500)]
    assert generated == sorted(generated)
    assert sorted(generated, key=str) == generated  # urutan string = urutan waktu (indeks teks)
    assert [uuid7_unix_ms(v) for v in generated] == list(
        range(1_700_000_000_000, 1_700_000_000_000 + 500)
    )


def test_uuid7_random_bits_are_unique_within_same_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000 * 1_000_000)
    generated = {uuid7() for _ in range(10_000)}
    assert len(generated) == 10_000
    assert {uuid7_unix_ms(v) for v in generated} == {1_700_000_000_000}


def test_uuid7_unix_ms_rejects_other_versions() -> None:
    with pytest.raises(ValueError):
        uuid7_unix_ms(UUID("00000000-0000-4000-8000-000000000000"))
