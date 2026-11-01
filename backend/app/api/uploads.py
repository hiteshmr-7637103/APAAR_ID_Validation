"""CSV upload and maker-checker endpoints (spec 8; FR-2, FR-3, FR-17).

- `GET  /templates/students.csv`          spec 7.2 columns
- `POST /uploads`                         maker: create an upload (own institution)
- `GET  /uploads/{id}`                    status and received chunks (resume)
- `PUT  /uploads/{id}/chunks/{n}`         maker: raw chunk bytes as the request body
- `POST /uploads/{id}/finalize`           maker: assemble (idempotent)
- `POST /uploads/{id}/dry-run`            maker: row report + projected bucket impact
- `GET  /uploads/{id}/report`             stored row report (severity filter)
- `POST /uploads/{id}/submit`             maker: commit to the repository (idempotent)
- `GET  /batches`, `GET /batches/{id}`, `GET /batches/{id}/records`
- `POST /batches/{id}/approve|reject`     checker, never the batch's own maker

Institution users only ever see their own institution's batches (404 otherwise, D7).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.events import event_bus
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles, scope_to_institution
from app.db.models import BatchStatus, Role, UploadBatch, User
from app.db.session import get_db
from app.services import uploads

router = APIRouter(tags=["uploads"])

_STAFF = require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)
_MAKER = require_roles(Role.INST_MAKER)
_CHECKER = require_roles(Role.INST_CHECKER)


class UploadCreate(BaseModel):
    filename: str | None = Field(None, max_length=255)
    total_chunks: int | None = Field(None, ge=1)
    academic_year: str | None = Field(None, pattern=r"^\d{4}-\d{2}$")


class FinalizeIn(BaseModel):
    total_chunks: int | None = Field(None, ge=1)


class SubmitIn(BaseModel):
    exclude_error_rows: bool = Field(
        False, description="Submit the rows without errors and leave the error rows out"
    )
    justification: str | None = Field(
        None, max_length=2000, description="Applies to every row with a warning"
    )
    row_justifications: dict[int, str] = Field(
        default_factory=dict, description="Per-row justification (row number -> text)"
    )


class DecisionIn(BaseModel):
    note: str = Field(..., min_length=3, max_length=2000)


def _not_found() -> ApiError:
    return ApiError(404, "BATCH_NOT_FOUND", "Upload or batch not found.", "errors.batch_not_found")


def load_batch(db: Session, user: User, batch_id: int, *, lock: bool = False) -> UploadBatch:
    """The batch if the user may see it (institution scoping via the shared helper)."""
    stmt = scope_to_institution(
        select(UploadBatch).where(UploadBatch.id == batch_id), user, UploadBatch.institution_id
    )
    if lock:
        stmt = stmt.with_for_update()
    batch = db.scalar(stmt)
    if batch is None:
        raise _not_found()
    return batch


def _own_upload(db: Session, user: User, batch_id: int, *, lock: bool = True) -> UploadBatch:
    batch = load_batch(db, user, batch_id, lock=lock)
    if batch.maker_id != user.id:
        raise ApiError(
            403,
            "NOT_BATCH_MAKER",
            "Only the maker who created this upload can change or submit it.",
            "errors.not_batch_maker",
        )
    return batch


@router.get(
    "/templates/students.csv",
    summary="CSV template (spec 7.2 columns)",
    response_class=Response,
)
def students_template(_: User = Depends(_STAFF)) -> Response:
    return Response(
        content=uploads.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="students_template.csv"'},
    )


@router.post("/uploads", status_code=201, summary="Start a chunked CSV upload")
def create_upload(
    body: UploadCreate | None = Body(None),
    user: User = Depends(_MAKER),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = body or UploadCreate()
    batch = uploads.create_upload(db, user, body.filename, body.total_chunks, body.academic_year)
    db.commit()
    return {
        **uploads.batch_dict(db, batch),
        "received_chunks": [],
        "max_chunk_bytes": settings.upload_max_chunk_bytes,
    }


@router.get("/uploads/{upload_id}", summary="Upload status and received chunks (resume)")
def get_upload(
    upload_id: int, user: User = Depends(_STAFF), db: Session = Depends(get_db)
) -> dict[str, Any]:
    batch = load_batch(db, user, upload_id)
    return {
        **uploads.batch_dict(db, batch),
        "received_chunks": uploads.received_chunks(db, batch.id),
        "max_chunk_bytes": settings.upload_max_chunk_bytes,
    }


@router.put("/uploads/{upload_id}/chunks/{index}", summary="Store one chunk (raw bytes)")
async def put_chunk(
    upload_id: int,
    index: int,
    request: Request,
    user: User = Depends(_MAKER),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = await request.body()
    batch = _own_upload(db, user, upload_id)
    out = uploads.put_chunk(db, batch, index, payload)
    db.commit()
    return {**out, "received_chunks": uploads.received_chunks(db, upload_id)}


@router.post("/uploads/{upload_id}/finalize", summary="Assemble the chunks (idempotent)")
def finalize_upload(
    upload_id: int,
    body: FinalizeIn | None = Body(None),
    user: User = Depends(_MAKER),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    batch = _own_upload(db, user, upload_id)
    out = uploads.finalize(db, batch, (body or FinalizeIn()).total_chunks, user)
    db.commit()
    return out


@router.post(
    "/uploads/{upload_id}/dry-run",
    summary="Dry run: row report + projected seat-bucket impact (writes nothing)",
)
def dry_run(
    upload_id: int, user: User = Depends(_MAKER), db: Session = Depends(get_db)
) -> dict[str, Any]:
    batch = _own_upload(db, user, upload_id)
    out = uploads.dry_run(db, batch, user)
    db.commit()
    return out


@router.get("/uploads/{upload_id}/report", summary="Stored per-row report")
def upload_report(
    upload_id: int,
    severity: str | None = Query(None, pattern="^(ERROR|WARNING|INFO|OK)$"),
    limit: int = Query(500, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    batch = load_batch(db, user, upload_id)
    return uploads.report(db, batch, severity, limit, offset)


@router.post("/uploads/{upload_id}/submit", summary="Submit for approval (idempotent)")
def submit_upload(
    upload_id: int,
    body: SubmitIn | None = Body(None),
    user: User = Depends(_MAKER),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = body or SubmitIn()
    batch = _own_upload(db, user, upload_id)  # FOR UPDATE: concurrent submits serialise
    out = uploads.submit(
        db,
        batch,
        user,
        exclude_error_rows=body.exclude_error_rows,
        justification=body.justification,
        row_justifications=body.row_justifications,
    )
    db.commit()
    return out


# --------------------------------------------------------------------------- batches


@router.get("/batches", summary="Batches (institution users: own institution only)")
def list_batches(
    status: BatchStatus | None = None,
    institution_id: int | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = scope_to_institution(select(UploadBatch), user, UploadBatch.institution_id)
    if status is not None:
        stmt = stmt.where(UploadBatch.status == status)
    if institution_id is not None:
        stmt = stmt.where(UploadBatch.institution_id == institution_id)
    stmt = stmt.order_by(UploadBatch.id.desc()).limit(limit).offset(offset)
    return [uploads.batch_dict(db, b) for b in db.scalars(stmt)]


@router.get("/batches/{batch_id}", summary="Batch detail")
def get_batch(
    batch_id: int, user: User = Depends(_STAFF), db: Session = Depends(get_db)
) -> dict[str, Any]:
    batch = load_batch(db, user, batch_id)
    out = uploads.batch_dict(db, batch)
    out["can_decide"] = (
        user.role == Role.INST_CHECKER
        and batch.maker_id != user.id
        and batch.status == BatchStatus.PENDING_APPROVAL
    )
    out["is_own_batch"] = batch.maker_id == user.id
    out["bucket_impact"] = (batch.dry_run_json or {}).get("bucket_impact", [])
    return out


@router.get("/batches/{batch_id}/records", summary="What the batch puts into the repository")
def get_batch_records(
    batch_id: int,
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    batch = load_batch(db, user, batch_id)
    return uploads.batch_records(db, batch, limit, offset)


@router.post("/batches/{batch_id}/approve", summary="Checker approves (never the maker)")
def approve_batch(
    batch_id: int,
    body: DecisionIn,
    user: User = Depends(_CHECKER),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    batch = load_batch(db, user, batch_id, lock=True)
    out = uploads.decide(db, batch, user, True, body.note)
    db.commit()
    event_bus.publish_threadsafe("batch_decided", {"batch_id": batch.id, "approved": True})
    return out


@router.post("/batches/{batch_id}/reject", summary="Checker rejects; seats are released")
def reject_batch(
    batch_id: int,
    body: DecisionIn,
    user: User = Depends(_CHECKER),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    batch = load_batch(db, user, batch_id, lock=True)
    out = uploads.decide(db, batch, user, False, body.note)
    db.commit()
    event_bus.publish_threadsafe("batch_decided", {"batch_id": batch.id, "approved": False})
    return out
