"""Pembuat ID UUIDv7 (RFC 9562) tanpa dependensi — ADR 0004 §1.

Tata letak 128 bit: 48 bit unix epoch milidetik · 4 bit versi (0111) · 12 bit acak ·
2 bit varian (10) · 62 bit acak. Total 74 bit acak dari `os.urandom`.

Urutan waktu antar milidetik terjamin (bagus untuk lokalitas indeks B-tree); di dalam
milidetik yang sama urutannya acak (tidak monoton) — cukup untuk PK, jangan dipakai sebagai
penanda urutan kejadian. Ganti dengan `uuid.uuid7` setelah pindah ke Python 3.14.
"""

import os
import time
from uuid import UUID

_TIMESTAMP_MASK = (1 << 48) - 1
_RAND_A_BITS = 12
_RAND_B_BITS = 62


def uuid7() -> UUID:
    """Buat UUID versi 7 baru berbasis waktu saat ini."""
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bit, dipakai 74
    rand_a = (rand >> _RAND_B_BITS) & ((1 << _RAND_A_BITS) - 1)
    rand_b = rand & ((1 << _RAND_B_BITS) - 1)
    value = (
        (unix_ms & _TIMESTAMP_MASK) << 80
        | 0x7 << 76  # versi 7
        | rand_a << 64
        | 0b10 << 62  # varian RFC 9562
        | rand_b
    )
    return UUID(int=value)


def uuid7_unix_ms(value: UUID) -> int:
    """Ambil stempel waktu (unix milidetik) dari UUIDv7."""
    if value.version != 7:
        raise ValueError("bukan UUIDv7")
    return value.int >> 80
