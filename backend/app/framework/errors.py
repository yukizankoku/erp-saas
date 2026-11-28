"""Error Record Framework dan handler FastAPI-nya (ADR 0004 §7).

Respons berbentuk Problem Details (RFC 9457, `application/problem+json`) dengan `code` stabil
untuk klien dan pesan i18n id+en (dipilih dari `Accept-Language`, default `id`). Pesan dipindah
ke paket i18n bersama saat P1-17 tersedia.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_JSON = "application/problem+json"
DEFAULT_LANGUAGE = "id"
_SUPPORTED_LANGUAGES = ("id", "en")

_MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    # code → bahasa → (title, detail)
    "record.not_found": {
        "id": ("Data tidak ditemukan", "Data yang diminta tidak ada atau sudah dihapus."),
        "en": ("Record not found", "The requested record does not exist or has been deleted."),
    },
    "record.version_conflict": {
        "id": (
            "Konflik versi data",
            "Data telah diubah oleh pengguna lain. Muat ulang lalu coba lagi.",
        ),
        "en": (
            "Record version conflict",
            "The record was changed by someone else. Reload it and try again.",
        ),
    },
    "record.conflict": {
        # Sengaja generik: id yang sama milik tenant sendiri maupun tenant lain → pesan identik.
        "id": (
            "ID data sudah dipakai",
            "ID data ini sudah digunakan. Periksa apakah data sudah tersimpan, "
            "atau buat dengan ID baru.",
        ),
        "en": (
            "Record ID already in use",
            "This record ID is already in use. Check whether it was already saved, "
            "or create it with a new ID.",
        ),
    },
    "record.reference_invalid": {
        # Sengaja generik: referensi tak ada ≡ milik tenant lain (tanpa oracle keberadaan).
        "id": (
            "Referensi data tidak valid",
            "Data yang dirujuk tidak ditemukan. Periksa kembali isian yang merujuk data lain.",
        ),
        "en": (
            "Invalid record reference",
            "The referenced record was not found. Check the fields that refer to other records.",
        ),
    },
    "org.code_conflict": {
        "id": (
            "Kode sudah dipakai",
            "Kode ini sudah digunakan oleh data lain. Gunakan kode berbeda.",
        ),
        "en": (
            "Code already in use",
            "This code is already used by another record. Use another code.",
        ),
    },
    "org.in_use": {
        "id": (
            "Data masih dipakai",
            "Data tidak dapat dihapus karena masih memiliki cabang atau gudang aktif.",
        ),
        "en": (
            "Record still in use",
            "The record cannot be deleted because it still has active branches or warehouses.",
        ),
    },
}


class RecordError(Exception):
    code: str = "record.error"
    status_code: int = 500


class RecordNotFound(RecordError):
    """Record tidak ada, sudah dihapus, atau milik tenant lain (sengaja tak dibedakan → 404)."""

    code = "record.not_found"
    status_code = 404


class VersionConflict(RecordError):
    """`expected_version` (If-Match) tidak cocok, atau record berubah bersamaan saat flush."""

    code = "record.version_conflict"
    status_code = 409


class RecordConflict(RecordError):
    """`id` record baru sudah dipakai — oleh tenant sendiri **atau** tenant lain (PK global).

    Kedua kasus sengaja identik (kode, status, pesan) agar keberadaan baris tenant lain tidak
    bocor. Klien sinkron: `GET` id tersebut; 200 = sudah tersimpan (retry), 404 = buat id baru.
    """

    code = "record.conflict"
    status_code = 409


class RecordReferenceInvalid(RecordError):
    """FK (sqlstate 23503) dari input klien: target tak ada **atau** milik tenant lain.

    Kedua kasus identik (kode, status, pesan) agar FK tidak menjadi oracle keberadaan.
    """

    code = "record.reference_invalid"
    status_code = 422


class CodeConflict(RecordError):
    """Kode unik (per tenant / company) sudah dipakai record aktif lain."""

    code = "org.code_conflict"
    status_code = 409


class RecordInUse(RecordError):
    """Record induk tidak boleh dihapus selama masih punya anak aktif."""

    code = "org.in_use"
    status_code = 409


def preferred_language(accept_language: str | None) -> str:
    """Pilih `id`/`en` dari header Accept-Language (menghormati bobot q)."""
    candidates: list[tuple[float, int, str]] = []
    for position, part in enumerate((accept_language or "").split(",")):
        tag, _, params = part.strip().partition(";")
        lang = tag.strip().lower().split("-")[0]
        q = 1.0
        params = params.strip().replace(" ", "")
        if params.startswith("q="):
            try:
                q = float(params[2:])
            except ValueError:
                continue
        if lang in _SUPPORTED_LANGUAGES and q > 0:
            candidates.append((q, -position, lang))
    return max(candidates)[2] if candidates else DEFAULT_LANGUAGE


def problem_response(exc: RecordError, request: Request) -> JSONResponse:
    lang = preferred_language(request.headers.get("accept-language"))
    title, detail = _MESSAGES[exc.code][lang]
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "about:blank",
            "title": title,
            "status": exc.status_code,
            "detail": detail,
            "code": exc.code,
        },
        media_type=PROBLEM_JSON,
        headers={"Content-Language": lang},
    )


async def _record_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RecordError)
    return problem_response(exc, request)


def register_record_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RecordNotFound, _record_error_handler)
    app.add_exception_handler(VersionConflict, _record_error_handler)
    app.add_exception_handler(RecordConflict, _record_error_handler)
    app.add_exception_handler(RecordReferenceInvalid, _record_error_handler)
    app.add_exception_handler(CodeConflict, _record_error_handler)
    app.add_exception_handler(RecordInUse, _record_error_handler)
