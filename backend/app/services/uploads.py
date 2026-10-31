"""CSV bulk upload pipeline (FR-2, FR-3, FR-17; spec 8).

Lifecycle of an `upload_batches` row (source UPLOAD):

    POST /uploads                 -> DRAFT (no chunks)
    PUT  /uploads/{id}/chunks/{n} -> chunk n stored (Fernet-encrypted); re-sending a chunk
                                     before finalize replaces it (resumable uploads)
    POST /uploads/{id}/finalize   -> chunks 0..n-1 assembled into one CSV (header checked,
                                     SHA-256 stored); idempotent, chunks frozen afterwards
    POST /uploads/{id}/dry-run    -> DRY_RUN: every row through the rule engine plus a
                                     projected Seat Ledger impact, computed in memory;
                                     nothing is written to student_records / the ledger
    POST /uploads/{id}/submit     -> PENDING_APPROVAL: rules -> store (T0) -> seats (flag
                                     mode) -> T1/T2 -> [ML triage: Phase 5]; idempotent
    POST /batches/{id}/approve    -> APPROVED, records ACTIVE (checker, never the maker)
    POST /batches/{id}/reject     -> REJECTED, records REJECTED, seats released

Rows within one file are checked against each other as well as against stored data (an
in-memory overlay of the rows above them), so a student listed twice is caught in the dry
run, exactly as the second copy would be at submission.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import audit
from app.core.config import settings
from app.core.crypto import decrypt, decrypt_bytes, encrypt_bytes
from app.core.errors import ApiError
from app.db.models import (
    BatchSource,
    BatchStatus,
    InstitutionProgramme,
    LedgerKey,
    Notification,
    Programme,
    RecordStatus,
    SeatLedgerBalance,
    Severity,
    Student,
    StudentRecord,
    StudentRecordVersion,
    UploadBatch,
    UploadChunk,
    User,
    ValidationResult,
)
from app.services import seat_ledger, trust
from app.services.cases import persist_case_specs
from app.services.quota import active_rules
from app.services.records import insert_records_bulk, outcome_rows
from app.services.rule_engine import (
    APAAR_RE,
    DbRuleContext,
    Outcome,
    RecordInput,
    RuleContext,
    ValidationReport,
    outcome,
    validate_record,
)
from app.services.timeline import EnrolmentView

# Spec 7.2, in order. `dob` is YYYY-MM-DD (the header may also be written `dob(YYYY-MM-DD)`).
TEMPLATE_COLUMNS: tuple[str, ...] = (
    "apaar_id",
    "student_college_id",
    "full_name",
    "dob",
    "gender",
    "programme_code",
    "shift",
    "admission_date",
    "admission_category",
    "entry_type",
    "mode",
    "nationality",
    "is_nri",
    "nri_sponsor",
    "is_oci",
    "oci_number",
    "is_foreign_national",
    "phone",
    "email",
    "address",
)
# Accepted but not in the template: the FN passport/ID note (D43) and an explicit year.
OPTIONAL_COLUMNS: tuple[str, ...] = ("id_document_note", "academic_year")
HEADER_ALIASES = {"dob(yyyy-mm-dd)": "dob", "dob (yyyy-mm-dd)": "dob"}

BOOL_COLUMNS = ("is_nri", "is_oci", "is_foreign_national")
DATE_COLUMNS = ("dob", "admission_date")
UPPER_COLUMNS = ("gender", "programme_code", "shift", "admission_category", "entry_type", "mode")
DEFAULTS = {"shift": "REGULAR", "entry_type": "REGULAR", "mode": "FULL_TIME"}
MAX_LEN = {
    "apaar_id": 32,
    "student_college_id": 64,
    "full_name": 255,
    "gender": 8,
    "programme_code": 64,
    "shift": 16,
    "admission_category": 16,
    "entry_type": 16,
    "mode": 16,
    "nationality": 64,
    "nri_sponsor": 255,
    "oci_number": 64,
    "id_document_note": 255,
    "phone": 32,
    "email": 255,
    "address": 500,
}
LABELS = {
    "apaar_id": "APAAR ID",
    "student_college_id": "Student ID",
    "full_name": "Full name",
    "dob": "Date of birth",
    "admission_date": "Admission date",
    "is_nri": "is_nri",
    "is_oci": "is_oci",
    "is_foreign_national": "is_foreign_national",
}
TRUE_VALUES = frozenset({"true", "t", "yes", "y", "1"})
FALSE_VALUES = frozenset({"false", "f", "no", "n", "0", ""})
_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Suggested fixes for codes whose rule does not carry a specific one (FR-3).
FIX_HINTS: dict[str, str] = {
    "APAAR_INVALID": "Enter the student's 12-digit APAAR ID exactly as printed on the APAAR card.",
    "APAAR_SUSPICIOUS": "Re-check the APAAR ID with the student; placeholders are not accepted.",
    "APAAR_NOT_FOUND": "Check the 12 digits with the student; the ID is not in the registry.",
    "APAAR_MISMATCH": (
        "Correct the name or date of birth to match the student's APAAR record, or add a "
        "justification."
    ),
    "DUP_EXACT_SAME_INST": "Remove the duplicate row, or update the existing record instead.",
    "FN_NATIONALITY_INVALID": "Enter the student's country of citizenship.",
    "FN_ID_DOCUMENT_MISSING": "Add an id_document_note column with the passport/ID reference.",
    "OCI_NUMBER_MISSING": "Fill in oci_number with the OCI card number.",
    "NRI_SPONSOR_MISSING": "Fill in nri_sponsor with the sponsor's name and relation.",
    "ADMISSION_OUTSIDE_WINDOW": (
        "Correct the admission date, or add a justification for a late admission."
    ),
    "DOB_IMPLAUSIBLE": "Check the date of birth (YYYY-MM-DD) and the programme.",
    "REQUIRED_FIELD_MISSING": "Fill in the missing value.",
    "FIELD_INVALID": "Use one of the allowed values.",
    "PROGRAMME_NOT_APPROVED": "Use one of your approved programme codes and shifts.",
    "TIMELINE_OVERLAP": "Check the admission date and whether the other enrolment has ended.",
    "TIMELINE_TOO_SHORT": "Check the admission and end dates.",
    "TIMELINE_LATERAL_NO_PRIOR": (
        "Check the entry type; for a genuine lateral entry add a justification (prior diploma)."
    ),
    "TIMELINE_INVALID_DATE": "Correct the dates.",
    "SEAT_OVER_CAP": (
        "Check the admission category and entry type. If they are correct, add a "
        "justification: the record will be stored, flagged and held for AICTE review."
    ),
}
SEVERITY_RANK = {Severity.ERROR: 3, Severity.WARNING: 2, Severity.INFO: 1}


# --------------------------------------------------------------------------- errors


def _err(status: int, code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(status, code, message, f"errors.{code.lower()}", details)


def template_csv() -> str:
    return ",".join(TEMPLATE_COLUMNS) + "\r\n"


# --------------------------------------------------------------------------- chunks


def create_upload(
    db: Session,
    actor: User,
    filename: str | None,
    total_chunks: int | None,
    academic_year: str | None,
) -> UploadBatch:
    if total_chunks is not None and not (1 <= total_chunks <= settings.upload_max_chunks):
        raise _err(422, "UPLOAD_INVALID", "total_chunks is out of range.")
    batch = UploadBatch(
        institution_id=actor.institution_id,
        maker_id=actor.id,
        status=BatchStatus.DRAFT,
        source=BatchSource.UPLOAD,
        filename=(filename or "upload.csv")[:255],
        total_chunks=total_chunks,
        academic_year=academic_year or settings.current_academic_year,
        row_count=0,
        error_count=0,
        warning_count=0,
    )
    db.add(batch)
    db.flush()
    audit.record(
        db,
        "UPLOAD_CREATE",
        actor=actor,
        entity_type="upload_batch",
        entity_id=batch.id,
        detail={"institution_id": batch.institution_id, "total_chunks": total_chunks},
    )
    return batch


def received_chunks(db: Session, batch_id: int) -> list[int]:
    return list(
        db.scalars(
            select(UploadChunk.chunk_index)
            .where(UploadChunk.upload_id == batch_id)
            .order_by(UploadChunk.chunk_index)
        )
    )


def put_chunk(db: Session, batch: UploadBatch, index: int, payload: bytes) -> dict[str, Any]:
    """Store chunk `index`. Before finalize a re-sent chunk replaces the stored one (resume
    after a dropped connection); after finalize chunks are frozen, and re-sending an
    identical chunk is a harmless no-op (idempotent retry). Does not commit."""
    if batch.source != BatchSource.UPLOAD:
        raise _err(409, "UPLOAD_NOT_CHUNKED", "This batch was not created by a CSV upload.")
    limit = batch.total_chunks or settings.upload_max_chunks
    if not (0 <= index < limit):
        raise _err(422, "CHUNK_INDEX_INVALID", f"Chunk index must be between 0 and {limit - 1}.")
    if not payload:
        raise _err(422, "CHUNK_EMPTY", "The chunk is empty.")
    if len(payload) > settings.upload_max_chunk_bytes:
        raise _err(
            413,
            "CHUNK_TOO_LARGE",
            f"A chunk may be at most {settings.upload_max_chunk_bytes} bytes.",
        )
    existing = db.scalar(
        select(UploadChunk).where(
            UploadChunk.upload_id == batch.id, UploadChunk.chunk_index == index
        )
    )
    if batch.finalized_at is not None:
        if existing is not None and decrypt_bytes(existing.payload) == payload:
            return {"chunk_index": index, "bytes": len(payload), "status": "unchanged"}
        raise _err(
            409,
            "UPLOAD_FINALIZED",
            "This upload is already finalized; its chunks cannot change. Start a new upload.",
        )
    enc = encrypt_bytes(payload)
    db.execute(
        pg_insert(UploadChunk)
        .values(upload_id=batch.id, chunk_index=index, payload=enc)
        .on_conflict_do_update(
            constraint="uq_upload_chunk", set_={"payload": enc, "received_at": func.now()}
        )
    )
    return {
        "chunk_index": index,
        "bytes": len(payload),
        "status": "replaced" if existing is not None else "stored",
    }


def _assemble(db: Session, batch: UploadBatch, total: int | None) -> tuple[bytes, int]:
    rows = db.execute(
        select(UploadChunk.chunk_index, UploadChunk.payload)
        .where(UploadChunk.upload_id == batch.id)
        .order_by(UploadChunk.chunk_index)
    ).all()
    indexes = [r[0] for r in rows]
    n = total or batch.total_chunks or len(rows)
    missing = sorted(set(range(n)) - set(indexes))
    extra = [i for i in indexes if i >= n]
    if not rows or missing or extra:
        raise _err(
            409,
            "UPLOAD_INCOMPLETE",
            "Some chunks are missing; upload them and finalize again.",
            {"expected_chunks": n, "missing": missing[:100], "unexpected": extra[:100]},
        )
    data = b"".join(decrypt_bytes(p) for _, p in rows)
    if len(data) > settings.upload_max_file_bytes:
        raise _err(413, "UPLOAD_TOO_LARGE", "The file is too large.")
    return data, n


def decode_csv(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _err(422, "CSV_ENCODING", "The file must be UTF-8 encoded CSV.") from None


def _norm_header(name: str) -> str:
    h = name.strip().lower()
    return HEADER_ALIASES.get(h, h)


def read_csv(text: str) -> tuple[list[str], list[tuple[int, dict[str, str]]], list[str]]:
    """Header check and rows. Returns (header, [(row_number, values)], notes). Row numbers
    count data rows: row 1 is the first student under the header. Blank lines are
    skipped."""
    reader = csv.reader(io.StringIO(text))
    try:
        raw_header = next(reader)
    except StopIteration:
        raise _err(422, "CSV_EMPTY", "The file is empty.") from None
    header = [_norm_header(h) for h in raw_header]
    missing = [c for c in TEMPLATE_COLUMNS if c not in header]
    dupes = sorted({h for h in header if header.count(h) > 1 and h})
    if missing or dupes:
        raise _err(
            422,
            "CSV_HEADER_INVALID",
            "The header does not match the template (download it from the Upload Wizard).",
            {"missing_columns": missing, "duplicate_columns": dupes},
        )
    known = set(TEMPLATE_COLUMNS) | set(OPTIONAL_COLUMNS)
    ignored = [h for h in header if h and h not in known]
    notes = [f"Ignored unknown column '{h}'." for h in ignored]
    rows = []
    n = 0
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        n += 1
        if n > settings.upload_max_rows:
            raise _err(
                422,
                "CSV_TOO_MANY_ROWS",
                f"A file may hold at most {settings.upload_max_rows} rows.",
            )
        padded = values + [""] * (len(header) - len(values))
        rows.append((n, {h: padded[i] for i, h in enumerate(header) if h in known}))
    if not rows:
        raise _err(422, "CSV_EMPTY", "The file has a header but no student rows.")
    return header, rows, notes


def finalize(db: Session, batch: UploadBatch, total_chunks: int | None, actor: User) -> dict:
    """Assemble the chunks into one CSV (caller holds the batch row lock). Idempotent:
    finalizing again returns the stored result and changes nothing. Does not commit."""
    if batch.source != BatchSource.UPLOAD:
        raise _err(409, "UPLOAD_NOT_CHUNKED", "This batch was not created by a CSV upload.")
    if batch.finalized_at is not None:
        if total_chunks is not None and total_chunks != batch.total_chunks:
            raise _err(
                409,
                "UPLOAD_FINALIZED",
                "This upload was finalized with a different number of chunks.",
                {"total_chunks": batch.total_chunks},
            )
        return {**finalize_info(batch), "idempotent_replay": True}
    data, n = _assemble(db, batch, total_chunks)
    _, rows, notes = read_csv(decode_csv(data))
    batch.csv_enc = encrypt_bytes(data)
    batch.content_sha256 = hashlib.sha256(data).hexdigest()
    batch.total_chunks = n
    batch.row_count = len(rows)
    batch.finalized_at = datetime.now(UTC)
    batch.dry_run_json = {"header_notes": notes}
    db.flush()
    audit.record(
        db,
        "UPLOAD_FINALIZE",
        actor=actor,
        entity_type="upload_batch",
        entity_id=batch.id,
        detail={"rows": len(rows), "chunks": n, "bytes": len(data), "sha256": batch.content_sha256},
    )
    return {**finalize_info(batch), "idempotent_replay": False}


def finalize_info(batch: UploadBatch) -> dict[str, Any]:
    return {
        "upload_id": batch.id,
        "rows": batch.row_count,
        "total_chunks": batch.total_chunks,
        "sha256": batch.content_sha256,
        "finalized_at": batch.finalized_at.isoformat() if batch.finalized_at else None,
        "header_notes": (batch.dry_run_json or {}).get("header_notes", []),
    }


# --------------------------------------------------------------------------- parsing rows


@dataclass
class ParsedRow:
    row: int
    raw: dict[str, str]
    inp: RecordInput
    parse_outcomes: list[Outcome] = field(default_factory=list)


def _parse_date(name: str, value: str, out: list[Outcome]) -> date | None:
    v = value.strip()
    if not v:
        return None
    if _ISO.match(v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            pass
    fix: dict[str, Any] = {"hint": "Use the YYYY-MM-DD format, e.g. 2007-04-02."}
    m = _DMY.match(v)
    if m:
        try:
            iso = date(int(m[3]), int(m[2]), int(m[1])).isoformat()
            fix = {"value": iso, "hint": f"Did you mean {iso}? Dates must be YYYY-MM-DD."}
        except ValueError:
            pass
    out.append(
        outcome(
            "FIELD_INVALID",
            name,
            "date",
            label=LABELS.get(name, name),
            value=v,
            evidence={"field": name, "value": v},
            suggested_fix=fix,
        )
    )
    return None


def parse_row(row: int, raw: dict[str, str], institution_id: int, year: str) -> ParsedRow:
    out: list[Outcome] = []
    vals: dict[str, Any] = {}
    for name in (*TEMPLATE_COLUMNS, *OPTIONAL_COLUMNS):
        v = (raw.get(name) or "").strip()
        if name in MAX_LEN and len(v) > MAX_LEN[name]:
            out.append(
                outcome(
                    "FIELD_INVALID",
                    name,
                    "too_long",
                    label=LABELS.get(name, name),
                    max=MAX_LEN[name],
                    evidence={"field": name, "length": len(v)},
                    suggested_fix={"hint": f"Shorten it to {MAX_LEN[name]} characters."},
                )
            )
            v = v[: MAX_LEN[name]]
        vals[name] = v
    for name in BOOL_COLUMNS:
        v = vals[name].lower()
        if v in TRUE_VALUES:
            vals[name] = True
        elif v in FALSE_VALUES:
            vals[name] = False
        else:
            out.append(
                outcome(
                    "FIELD_INVALID",
                    name,
                    "boolean",
                    label=LABELS[name],
                    value=vals[name],
                    evidence={"field": name, "value": vals[name]},
                    suggested_fix={"allowed": ["true", "false"]},
                )
            )
            vals[name] = False
    for name in DATE_COLUMNS:
        vals[name] = _parse_date(name, vals[name], out)
    for name in UPPER_COLUMNS:
        vals[name] = vals[name].upper() or None
    for name, default in DEFAULTS.items():
        vals[name] = vals[name] or default
    text_cols = set(TEMPLATE_COLUMNS) - set(BOOL_COLUMNS) - set(DATE_COLUMNS) - set(UPPER_COLUMNS)
    for name in (*text_cols, "id_document_note"):
        vals[name] = vals[name] or None
    row_year = vals.pop("academic_year") or year
    inp = RecordInput(institution_id=institution_id, academic_year=row_year, **vals)
    return ParsedRow(row, raw, inp, out)


def parse_batch(batch: UploadBatch) -> tuple[list[ParsedRow], list[str]]:
    if batch.csv_enc is None:
        raise _err(409, "UPLOAD_NOT_FINALIZED", "Finalize the upload first.")
    _, rows, notes = read_csv(decode_csv(decrypt_bytes(batch.csv_enc)))
    year = batch.academic_year or settings.current_academic_year
    return [parse_row(n, raw, batch.institution_id, year) for n, raw in rows], notes


# --------------------------------------------------------------------------- validation


class BatchContext(RuleContext):
    """The stored data plus the rows above the current one in this file (overlay), so rows
    of one file are checked against each other. Dry-run rows carry pseudo ids (-row)."""

    def __init__(self, base: DbRuleContext) -> None:
        self.base = base
        self.today = base.today
        self.rules = base.rules
        self._extra: dict[str, list[EnrolmentView]] = defaultdict(list)

    def approved_ips(self, institution_id, academic_year):
        return self.base.approved_ips(institution_id, academic_year)

    def ip_by_id(self, ip_id):
        return self.base.ip_by_id(ip_id)

    def programme(self, code):
        return self.base.programme(code)

    def registry(self, apaar_id):
        return self.base.registry(apaar_id)

    def enrolments(self, apaar_id):
        base = self.base.enrolments(apaar_id)
        seen = {e.record_id for e in base}
        return base + [e for e in self._extra.get(apaar_id, []) if e.record_id not in seen]

    def add(self, inp: RecordInput, report: ValidationReport, record_id: int) -> None:
        apaar = (inp.apaar_id or "").strip()
        ip = report.ip
        if not APAAR_RE.match(apaar) or ip is None or inp.admission_date is None:
            return
        self._extra[apaar].append(
            EnrolmentView(
                record_id=record_id,
                student_id=None,
                institution_id=inp.institution_id,
                institution_programme_id=ip.id,
                institution_code=ip.institution_code,
                programme_code=ip.programme_code,
                level=ip.level,
                duration_years=ip.duration_years,
                academic_year=ip.academic_year,
                admission_date=inp.admission_date,
                end_date=inp.end_date,
                status=RecordStatus.PENDING_APPROVAL.value,
                mode=inp.mode or "FULL_TIME",
                entry_type=inp.entry_type or "REGULAR",
            )
        )


def make_context(db: Session, parsed: list[ParsedRow]) -> BatchContext:
    """Large files prefetch every enrolment plus the registry entries of the file's APAAR
    IDs in a few queries (NFR-5); small ones look up per row."""
    base = DbRuleContext(db)
    if len(parsed) >= settings.upload_prefetch_rows:
        base.prefetch_all()
        base.prefetch_registry((p.inp.apaar_id or "").strip() for p in parsed)
    return BatchContext(base)


def _rewrite_batch_refs(outcomes: list[Outcome]) -> None:
    """Dry-run pseudo ids (-row) become 'row N of this file' in institution messages."""
    for o in outcomes:
        oid = o.evidence.get("other_record_id")
        if isinstance(oid, int) and oid < 0:
            o.evidence = {**o.evidence, "other_row": -oid}
            o.evidence.pop("other_record_id", None)
            o.message_en = o.message_en.replace(f"record #{oid}", f"row {-oid} of this file")


def _with_fix(o: Outcome) -> Outcome:
    if not o.suggested_fix and o.code in FIX_HINTS:
        o.suggested_fix = {"hint": FIX_HINTS[o.code]}
    return o


def check_row(p: ParsedRow, ctx: BatchContext) -> tuple[ValidationReport, list[Outcome]]:
    report = validate_record(p.inp, ctx)
    parsed_fields = {o.field for o in p.parse_outcomes}
    outcomes = list(p.parse_outcomes) + [
        o
        for o in report.outcomes
        if not (o.code == "REQUIRED_FIELD_MISSING" and o.field in parsed_fields)
    ]
    _rewrite_batch_refs(outcomes)
    report.outcomes = [_with_fix(o) for o in outcomes]
    return report, report.outcomes


def worst(outcomes: list[Outcome]) -> str:
    if not outcomes:
        return "OK"
    return str(max(outcomes, key=lambda o: SEVERITY_RANK[o.severity]).severity)


class SeatProjector:
    """In-memory Seat Ledger simulation for the dry run: starts from the current balances
    (read without locking) and counts this file's rows in order. Nothing is reserved; the
    authoritative decision is made by `seat_ledger.admit` at submission."""

    def __init__(self, db: Session, rules: dict[str, Any]) -> None:
        self.db = db
        self.rules = rules
        self._meta: dict[int, seat_ledger.IPMeta] = {}
        self.buckets: dict[tuple[int, LedgerKey], dict[str, Any]] = {}

    def add(self, row: int, ip_id: int, key: LedgerKey) -> Outcome | None:
        bk = (ip_id, key)
        if bk not in self.buckets:
            meta = self._meta.get(ip_id) or seat_ledger.ip_meta(self.db, ip_id)
            self._meta[ip_id] = meta
            used = self.db.scalar(
                select(SeatLedgerBalance.used).where(
                    SeatLedgerBalance.institution_programme_id == ip_id,
                    SeatLedgerBalance.ledger_key == key,
                )
            )
            self.buckets[bk] = {
                "meta": meta,
                "cap": seat_ledger.cap_for(meta, key, self.rules),
                "used_before": used or 0,
                "rows": [],
                "over_rows": [],
            }
        b = self.buckets[bk]
        b["rows"].append(row)
        used_after = b["used_before"] + len(b["rows"])
        if used_after <= b["cap"]:
            return None
        b["over_rows"].append(row)
        meta, excess = b["meta"], used_after - b["cap"]
        return outcome(
            "SEAT_OVER_CAP",
            "admission_category",
            "projected",
            row=row,
            bucket=str(key),
            programme=meta.programme_code,
            year=meta.academic_year,
            cap=b["cap"],
            used=used_after,
            excess=excess,
            evidence={
                "projected": True,
                "bucket": str(key),
                "cap": b["cap"],
                "used_after": used_after,
                "excess": excess,
            },
            suggested_fix={"hint": FIX_HINTS["SEAT_OVER_CAP"]},
        )

    def impact(self) -> list[dict[str, Any]]:
        out = []
        for (ip_id, key), b in sorted(self.buckets.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            meta = b["meta"]
            used_after = b["used_before"] + len(b["rows"])
            excess = max(0, used_after - b["cap"])
            over = b["over_rows"]
            shown = ", ".join(str(r) for r in over[:10]) + (" ..." if len(over) > 10 else "")
            out.append(
                {
                    "institution_programme_id": ip_id,
                    "programme_code": meta.programme_code,
                    "academic_year": meta.academic_year,
                    "shift": meta.shift,
                    "ledger_key": str(key),
                    "cap": b["cap"],
                    "used_before": b["used_before"],
                    "rows_in_file": len(b["rows"]),
                    "used_after": used_after,
                    "excess_after": excess,
                    "over_cap_rows": over,
                    "would_be_flagged": bool(over),
                    "message_en": (
                        f"Row{'s' if len(over) > 1 else ''} {shown}: would exceed {key} by "
                        f"{excess} ({meta.programme_code}, {meta.shift}: cap {b['cap']}, "
                        f"{used_after} after this file). Flagged rows are stored and held "
                        "for AICTE review if submitted."
                        if over
                        else f"{meta.programme_code} {key}: {used_after} of {b['cap']} seats "
                        "after this file; within the cap."
                    ),
                }
            )
        return out


def _row_view(p: ParsedRow, report: ValidationReport | None, outcomes: list[Outcome]) -> dict:
    return {
        "row": p.row,
        "apaar_id": p.inp.apaar_id,
        "student_college_id": p.inp.student_college_id,
        "full_name": p.inp.full_name,
        "programme_code": p.inp.programme_code,
        "shift": p.inp.shift,
        "admission_category": p.inp.admission_category,
        "entry_type": p.inp.entry_type,
        "ledger_key": str(report.ledger_key) if report and report.ledger_key else None,
        "severity": worst(outcomes),
        "outcomes": [o.to_dict() for o in outcomes],
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    sev = Counter(r["severity"] for r in rows)
    return {
        "rows": len(rows),
        "rows_with_errors": sev.get("ERROR", 0),
        "rows_with_warnings": sev.get("WARNING", 0),
        "rows_info_only": sev.get("INFO", 0),
        "rows_clean": sev.get("OK", 0),
        "rows_submittable": len(rows) - sev.get("ERROR", 0),
    }


def dry_run(db: Session, batch: UploadBatch, actor: User) -> dict[str, Any]:
    """FR-3. Every row through the rule engine and an in-memory seat projection. Writes only
    the batch's own report (validation_results with no record, the summary on the batch);
    never student_records or the Seat Ledger. Caller holds the batch lock and commits."""
    if batch.status not in (BatchStatus.DRAFT, BatchStatus.DRY_RUN):
        raise _err(409, "BATCH_ALREADY_SUBMITTED", "This upload has already been submitted.")
    parsed, notes = parse_batch(batch)
    ctx = make_context(db, parsed)
    projector = SeatProjector(db, ctx.rules)
    views, result_rows = [], []
    for p in parsed:
        report, outcomes = check_row(p, ctx)
        if report.ip is not None and not report.has_error:
            ctx.add(p.inp, report, -p.row)
            if report.ledger_key is not None:
                seat = projector.add(p.row, report.ip.id, report.ledger_key)
                if seat is not None:
                    outcomes.append(seat)
        views.append(_row_view(p, report, outcomes))
        result_rows.extend(outcome_rows(outcomes, None, batch.id, p.row))
    db.execute(delete(ValidationResult).where(ValidationResult.batch_id == batch.id))
    if result_rows:
        db.execute(insert(ValidationResult), result_rows)
    summary = _summary(views)
    impact = projector.impact()
    batch.status = BatchStatus.DRY_RUN
    batch.row_count = summary["rows"]
    batch.error_count = summary["rows_with_errors"]
    batch.warning_count = summary["rows_with_warnings"]
    batch.dry_run_at = datetime.now(UTC)
    batch.dry_run_json = {
        "summary": summary,
        "bucket_impact": impact,
        "header_notes": notes,
        "ran_at": batch.dry_run_at.isoformat(),
        "sha256": batch.content_sha256,
    }
    db.flush()
    audit.record(
        db,
        "UPLOAD_DRY_RUN",
        actor=actor,
        entity_type="upload_batch",
        entity_id=batch.id,
        detail={
            **summary,
            "buckets_over_cap": sum(1 for b in impact if b["would_be_flagged"]),
        },
    )
    return {
        "upload_id": batch.id,
        "status": str(batch.status),
        "writes_to_repository": False,
        "summary": summary,
        "bucket_impact": impact,
        "header_notes": notes,
        "rows": views,
    }


def report(
    db: Session,
    batch: UploadBatch,
    severity: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """The stored per-row report of a batch (dry run, or the submission's own results)."""
    by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for v in db.scalars(
        select(ValidationResult)
        .where(ValidationResult.batch_id == batch.id)
        .order_by(ValidationResult.row_number, ValidationResult.id)
    ):
        by_row[v.row_number or 0].append(
            {
                "code": v.code,
                "severity": str(v.severity),
                "field": v.field,
                "message_key": v.message_key,
                "message_en": v.message_en,
                "evidence": v.evidence_json or {},
                "suggested_fix": v.suggested_fix_json,
                "justification": v.resolution_note if not v.resolved else None,
                "student_record_id": v.student_record_id,
            }
        )
    rows: list[dict[str, Any]] = []
    if batch.csv_enc is not None:
        parsed, _ = parse_batch(batch)
        for p in parsed:
            outs = by_row.get(p.row, [])
            sev = max((SEVERITY_RANK[Severity(o["severity"])] for o in outs), default=0)
            rows.append(
                {
                    "row": p.row,
                    "apaar_id": p.inp.apaar_id,
                    "student_college_id": p.inp.student_college_id,
                    "full_name": p.inp.full_name,
                    "programme_code": p.inp.programme_code,
                    "admission_category": p.inp.admission_category,
                    "entry_type": p.inp.entry_type,
                    "severity": {3: "ERROR", 2: "WARNING", 1: "INFO"}.get(sev, "OK"),
                    "outcomes": outs,
                }
            )
    summary = _summary(rows) if rows else (batch.dry_run_json or {}).get("summary")
    if severity:
        rows = [r for r in rows if r["severity"] == severity.upper()]
    return {
        "upload_id": batch.id,
        "status": str(batch.status),
        "summary": summary,
        "bucket_impact": (batch.dry_run_json or {}).get("bucket_impact", []),
        "total": len(rows),
        "rows": rows[offset : offset + limit],
    }


# --------------------------------------------------------------------------- submit


def submit(
    db: Session,
    batch: UploadBatch,
    actor: User,
    *,
    exclude_error_rows: bool = False,
    justification: str | None = None,
    row_justifications: dict[int, str] | None = None,
) -> dict[str, Any]:
    """FR-17 on submit: rules -> store (T0) -> seat reserve (flag mode) -> T1/T2 ->
    [ML triage, Phase 5] -> PENDING_APPROVAL. Caller holds the batch row lock (so two
    concurrent submits serialise) and commits. Re-submitting a submitted batch returns the
    stored result and writes nothing (idempotent)."""
    if batch.status in (BatchStatus.PENDING_APPROVAL, BatchStatus.APPROVED, BatchStatus.REJECTED):
        return {**(batch.submit_json or {}), "status": str(batch.status), "idempotent_replay": True}
    if batch.status != BatchStatus.DRY_RUN:
        raise _err(409, "DRY_RUN_REQUIRED", "Run the dry run before submitting.")
    row_justifications = {int(k): v for k, v in (row_justifications or {}).items() if v}
    justification = (justification or "").strip() or None

    parsed, _ = parse_batch(batch)
    ctx = make_context(db, parsed)
    error_rows: list[int] = []
    excluded_results: list[dict] = []
    needs_just: list[int] = []
    keep: list[tuple[ParsedRow, ValidationReport, str | None]] = []
    for p in parsed:
        report, outcomes = check_row(p, ctx)
        if report.has_error:
            error_rows.append(p.row)
            excluded_results.extend(outcome_rows(outcomes, None, batch.id, p.row))
            continue
        # Rows of this file are seen by the rows below them under a pseudo id (-row),
        # exactly as in the dry run; the ids are mapped to the stored records below.
        ctx.add(p.inp, report, -p.row)
        just = row_justifications.get(p.row) or justification
        if any(o.severity == Severity.WARNING for o in outcomes) and not just:
            needs_just.append(p.row)
        keep.append((p, report, just))

    if error_rows and not exclude_error_rows:
        raise _err(
            422,
            "BATCH_HAS_ERRORS",
            "Some rows have errors. Fix them and upload again, or submit without those rows.",
            {"error_rows": error_rows[:500], "count": len(error_rows)},
        )
    if needs_just:
        raise _err(
            422,
            "JUSTIFICATION_REQUIRED",
            "Rows with warnings need a justification before they can be submitted.",
            {"rows": needs_just[:500], "count": len(needs_just)},
        )
    if not keep:
        raise _err(422, "NOTHING_TO_SUBMIT", "No row can be submitted: every row has errors.")

    # Store at T0 (set-based), then open/merge the AICTE-only cases with the real ids.
    ids = insert_records_bulk(
        db,
        [(p.inp, report, p.row) for p, report, _ in keep],
        actor,
        batch_id=batch.id,
        source=BatchSource.UPLOAD,
    )
    real = {-p.row: rid for (p, _, _), rid in zip(keep, ids, strict=True)}
    stored: list[tuple[ParsedRow, ValidationReport, int, str | None]] = []
    for (p, report, just), rid in zip(keep, ids, strict=True):
        if report.cases:
            for spec in report.cases:
                spec.enrolments = [
                    {**e, "record_id": real.get(e.get("record_id"), e.get("record_id"))}
                    for e in spec.enrolments
                ]
            persist_case_specs(db, report.cases, rid)
        stored.append((p, report, rid, just))

    # Seat Ledger (flag mode): one bucket at a time in (programme row, bucket) order, which
    # is also the order rejection releases them in, so two batches cannot deadlock.
    by_bucket: dict[tuple[int, LedgerKey], list[int]] = defaultdict(list)
    for _, report, rid, _ in stored:
        by_bucket[(report.ip.id, report.ledger_key)].append(rid)
    seat_by_record: dict[int, seat_ledger.SeatResult] = {}
    for (ip_id, key), ids in sorted(by_bucket.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        meta = seat_ledger.ip_meta(db, ip_id)
        for res in seat_ledger.admit_many(db, meta, key, ids, rules=ctx.rules, batch_id=batch.id):
            seat_by_record[res.record_id] = res
    flagged = set(
        db.scalars(
            select(StudentRecord.id).where(
                StudentRecord.batch_id == batch.id, StudentRecord.over_cap.is_(True)
            )
        )
    )

    result_rows = list(excluded_results) if exclude_error_rows else []
    tier_items: list[tuple[int, int]] = []
    over_rows, warn_rows = [], 0
    for p, report, rid, just in stored:
        outcomes = list(report.outcomes)
        if rid in flagged:
            res = seat_by_record[rid]
            outcomes.append(
                seat_ledger.over_cap_outcome(res.meta, res.ledger_key, res.cap, res.used)
            )
            over_rows.append(p.row)
        if any(o.severity == Severity.WARNING for o in outcomes):
            warn_rows += 1
        result_rows.extend(outcome_rows(outcomes, rid, batch.id, p.row, just))
        tier_items.append((rid, trust.earned_tier(False, report.apaar_status)))
    db.execute(delete(ValidationResult).where(ValidationResult.batch_id == batch.id))
    for i in range(0, len(result_rows), 5000):
        db.execute(insert(ValidationResult), result_rows[i : i + 5000])
    tiers = trust.bulk_apply(db, tier_items)
    # FR-17: ML triage (blocking + calibrated pairwise scoring) runs here as a background
    # task from Phase 5. It only opens cases; it never changes status or tier (NFR-8).

    now = datetime.now(UTC)
    batch.status = BatchStatus.PENDING_APPROVAL
    batch.submitted_at = now
    batch.justification = justification
    batch.row_count = len(parsed)
    batch.error_count = len(error_rows)
    batch.warning_count = warn_rows
    result = {
        "upload_id": batch.id,
        "stored": len(stored),
        "excluded_error_rows": error_rows[:500],
        "excluded_error_count": len(error_rows),
        "over_cap_rows": over_rows[:500],
        "over_cap_count": len(over_rows),
        "rows_with_warnings": warn_rows,
        "trust_tiers": {f"T{t}": n for t, n in sorted(tiers.items())},
        "buckets": [
            {
                "institution_programme_id": ip_id,
                "ledger_key": str(key),
                "admitted": len(ids),
            }
            for (ip_id, key), ids in sorted(by_bucket.items(), key=lambda kv: kv[0][0])
        ],
        "ml_triage": "deferred to Phase 5",
        "submitted_at": now.isoformat(),
    }
    batch.submit_json = result
    db.flush()
    audit.record(
        db,
        "UPLOAD_SUBMIT",
        actor=actor,
        entity_type="upload_batch",
        entity_id=batch.id,
        detail={
            "stored": len(stored),
            "excluded_error_rows": len(error_rows),
            "over_cap_rows": len(over_rows),
            "trust_tiers": result["trust_tiers"],
            "sha256": batch.content_sha256,
        },
    )
    return {**result, "status": str(batch.status), "idempotent_replay": False}


# --------------------------------------------------------------------------- checker


def _bucket_state(db: Session, keys: set[tuple[int, str]]) -> dict[tuple[int, str], dict]:
    out = {}
    for ip_id, key in keys:
        bal = db.scalar(
            select(SeatLedgerBalance).where(
                SeatLedgerBalance.institution_programme_id == ip_id,
                SeatLedgerBalance.ledger_key == key,
            )
        )
        if bal is not None:
            out[(ip_id, key)] = {"used": bal.used, "cap": bal.cap}
    return out


def decide(db: Session, batch: UploadBatch, actor: User, approve: bool, note: str) -> dict:
    """Checker decision (FR-17). The caller has checked role and institution and holds the
    batch row lock. A maker can never decide their own batch. Does not commit."""
    if batch.maker_id == actor.id:
        raise _err(
            403,
            "MAKER_CANNOT_APPROVE_OWN_BATCH",
            "You submitted this batch, so you cannot approve or reject it. Another registrar "
            "(checker) must decide.",
        )
    if batch.status != BatchStatus.PENDING_APPROVAL:
        raise _err(
            409,
            "BATCH_NOT_PENDING",
            "This batch is not awaiting approval.",
            {"status": str(batch.status)},
        )
    now = datetime.now(UTC)
    records = db.execute(
        select(
            StudentRecord.id,
            StudentRecord.status,
            StudentRecord.institution_programme_id,
            StudentRecord.ledger_key,
        )
        .where(StudentRecord.batch_id == batch.id)
        .order_by(
            StudentRecord.institution_programme_id, StudentRecord.ledger_key, StudentRecord.id
        )
    ).all()
    detail: dict[str, Any]
    if approve:
        pending = [r.id for r in records if r.status == RecordStatus.PENDING_APPROVAL]
        if pending:
            db.execute(
                update(StudentRecord)
                .where(StudentRecord.id.in_(pending))
                .values(status=RecordStatus.ACTIVE)
            )
        # FR-19 (Phase 7): ACTIVE records will get an enrolment confirmation request here.
        batch.status = BatchStatus.APPROVED
        detail = {
            "activated": len(pending),
            "unchanged": len(records) - len(pending),
            "over_cap_still_flagged": int(
                db.scalar(
                    select(func.count()).where(
                        StudentRecord.batch_id == batch.id,
                        StudentRecord.over_cap.is_(True),
                        StudentRecord.over_cap_reviewed.is_(False),
                    )
                )
            ),
        }
    else:
        affected = [
            r
            for r in records
            if r.status in (RecordStatus.PENDING_APPROVAL, RecordStatus.NEEDS_CORRECTION)
        ]
        keys = {(r.institution_programme_id, str(r.ledger_key)) for r in affected}
        before = _bucket_state(db, keys)
        released = 0
        if affected:
            rules = active_rules(db)
            ids = [r.id for r in affected]
            for i in range(0, len(ids), 5000):
                db.execute(
                    update(StudentRecord)
                    .where(StudentRecord.id.in_(ids[i : i + 5000]))
                    .values(status=RecordStatus.REJECTED)
                )
            groups: dict[tuple[int, str], list[int]] = defaultdict(list)
            for r in affected:
                groups[(r.institution_programme_id, str(r.ledger_key))].append(r.id)
            # same (programme row, bucket) order as submission, one lock per bucket
            for (ip_id, key), rids in sorted(groups.items()):
                meta = seat_ledger.ip_meta(db, ip_id)
                released += len(
                    seat_ledger.release_many(
                        db, meta, LedgerKey(key), rids, rules=rules, batch_id=batch.id
                    )
                )
        after = _bucket_state(db, keys)
        batch.status = BatchStatus.REJECTED
        detail = {
            "rejected": len(affected),
            "seats_released": released,
            "buckets": [
                {
                    "institution_programme_id": ip_id,
                    "ledger_key": key,
                    "cap": after[(ip_id, key)]["cap"],
                    "used_before": before[(ip_id, key)]["used"],
                    "used_after": after[(ip_id, key)]["used"],
                    "excess_after": max(
                        0, after[(ip_id, key)]["used"] - after[(ip_id, key)]["cap"]
                    ),
                }
                for ip_id, key in sorted(keys)
                if (ip_id, key) in after
            ],
        }
    batch.checker_id = actor.id
    batch.decided_at = now
    batch.decision_note = note
    db.add(
        Notification(
            user_id=batch.maker_id,
            kind="BATCH_APPROVED" if approve else "BATCH_REJECTED",
            payload_json={
                "message_key": "notification.batch_decided",
                "message_en": (
                    f"Batch #{batch.id} was {'approved' if approve else 'rejected'} by the "
                    f"registrar. Note: {note}"
                ),
                "batch_id": batch.id,
                "channel": "MOCK",
            },
        )
    )
    db.flush()
    audit.record(
        db,
        "BATCH_APPROVE" if approve else "BATCH_REJECT",
        actor=actor,
        entity_type="upload_batch",
        entity_id=batch.id,
        detail={**detail, "maker_id": batch.maker_id, "source": str(batch.source)},
    )
    return {"batch_id": batch.id, "status": str(batch.status), **detail}


# --------------------------------------------------------------------------- views


def batch_dict(db: Session, batch: UploadBatch) -> dict[str, Any]:
    status_counts = dict(
        db.execute(
            select(StudentRecord.status, func.count())
            .where(StudentRecord.batch_id == batch.id)
            .group_by(StudentRecord.status)
        ).all()
    )
    over = db.scalar(
        select(func.count()).where(
            StudentRecord.batch_id == batch.id, StudentRecord.over_cap.is_(True)
        )
    )
    maker = db.get(User, batch.maker_id)
    if batch.finalized_at is None and batch.source == BatchSource.UPLOAD:
        stage = "UPLOADING"
    elif batch.status == BatchStatus.DRAFT:
        stage = "FINALIZED"
    else:
        stage = str(batch.status)
    return {
        "id": batch.id,
        "institution_id": batch.institution_id,
        "source": str(batch.source),
        "status": str(batch.status),
        "stage": stage,
        "filename": batch.filename,
        "academic_year": batch.academic_year,
        "maker_id": batch.maker_id,
        "maker_name": maker.full_name or maker.email if maker else None,
        "checker_id": batch.checker_id,
        "row_count": batch.row_count,
        "error_count": batch.error_count,
        "warning_count": batch.warning_count,
        "total_chunks": batch.total_chunks,
        "sha256": batch.content_sha256,
        "records_by_status": {str(k): v for k, v in status_counts.items()},
        "records": sum(status_counts.values()),
        "over_cap_records": over,
        "justification": batch.justification,
        "decision_note": batch.decision_note,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "finalized_at": batch.finalized_at.isoformat() if batch.finalized_at else None,
        "dry_run_at": batch.dry_run_at.isoformat() if batch.dry_run_at else None,
        "submitted_at": batch.submitted_at.isoformat() if batch.submitted_at else None,
        "decided_at": batch.decided_at.isoformat() if batch.decided_at else None,
        "dry_run_summary": (batch.dry_run_json or {}).get("summary"),
        "submit_result": batch.submit_json,
    }


def batch_records(db: Session, batch: UploadBatch, limit: int, offset: int) -> dict[str, Any]:
    """Preview of what the batch puts into the repository (approvals screen)."""
    sr = StudentRecord
    total = db.scalar(select(func.count()).where(sr.batch_id == batch.id))
    rows = db.execute(
        select(
            sr.id,
            sr.student_college_id,
            sr.admission_date,
            sr.admission_category,
            sr.entry_type,
            sr.ledger_key,
            sr.over_cap,
            sr.over_cap_reviewed,
            sr.trust_tier,
            sr.status,
            sr.current_version,
            Student.apaar_id,
            Student.name_enc,
            Programme.canonical_code,
            InstitutionProgramme.shift,
            StudentRecordVersion.data_json,
        )
        .join(Student, Student.id == sr.student_id)
        .join(InstitutionProgramme, InstitutionProgramme.id == sr.institution_programme_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .outerjoin(
            StudentRecordVersion,
            (StudentRecordVersion.student_record_id == sr.id)
            & (StudentRecordVersion.version == sr.current_version),
        )
        .where(sr.batch_id == batch.id)
        .order_by(sr.id)
        .limit(limit)
        .offset(offset)
    ).all()
    ids = [r.id for r in rows]
    msgs: dict[int, list[dict]] = defaultdict(list)
    if ids:
        for v in db.scalars(
            select(ValidationResult).where(
                ValidationResult.student_record_id.in_(ids),
                ValidationResult.resolved.is_(False),
            )
        ):
            msgs[v.student_record_id].append(
                {
                    "code": v.code,
                    "severity": str(v.severity),
                    "message_en": v.message_en,
                    "justification": v.resolution_note,
                }
            )
    out = []
    for r in rows:
        snap = r.data_json or {}
        name = snap.get("submitted_name_enc")
        out.append(
            {
                "record_id": r.id,
                "row": snap.get("row"),
                "apaar_id": r.apaar_id,
                "student_college_id": r.student_college_id,
                "full_name": decrypt(name) if name else decrypt(r.name_enc),
                "programme_code": r.canonical_code,
                "shift": str(r.shift),
                "admission_date": r.admission_date.isoformat(),
                "admission_category": str(r.admission_category),
                "entry_type": str(r.entry_type),
                "ledger_key": str(r.ledger_key),
                "over_cap": r.over_cap,
                "over_cap_reviewed": r.over_cap_reviewed,
                "trust_tier": r.trust_tier,
                "status": str(r.status),
                "outcomes": msgs.get(r.id, []),
            }
        )
    return {"total": total, "records": out}
