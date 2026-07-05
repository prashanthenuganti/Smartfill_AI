"""
api/v1/exam_routes.py
------------------------
Two audiences:

  Admin CRUD (gated by require_admin) — build/edit exam field layouts
  and photo specs from the admin UI.

  Public read (gated by require_login only) — used by upload.html
  (exam dropdown + photo/signature specs) and review.html (exam-shaped
  field rendering) for any logged-in operator.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from backend.app.core.auth import require_admin, require_login
from backend.app.core.logging import get_logger
from backend.app.db.database import get_db
from backend.app.db.models import Exam, ExamField, ExamPhotoSpec
from backend.app.schemas.exam import (
    TRANSFORMS, FIELD_TYPES, ASSET_TYPES, PROFILE_KEYS,
    ExamCreate, ExamUpdate, ExamOut, ExamDetailOut,
    ExamFieldCreate, ExamFieldUpdate, ExamFieldOut,
    ExamPhotoSpecCreate, ExamPhotoSpecOut,
)

logger = get_logger(__name__)
router = APIRouter(tags=["exams"])


def _get_exam_or_404(db: Session, exam_id: int) -> Exam:
    exam = db.query(Exam).options(joinedload(Exam.fields), joinedload(Exam.photo_specs)).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


# ── Admin: meta (transform list, field-type list, profile keys) ──────────────

@router.get("/api/v1/admin/exam-meta")
async def exam_meta(_email: str = Depends(require_admin)):
    """Static config the admin field-builder UI needs to populate its dropdowns."""
    return {
        "transforms": TRANSFORMS,
        "field_types": FIELD_TYPES,
        "asset_types": ASSET_TYPES,
        "profile_keys": PROFILE_KEYS,
    }


# ── Admin: Exam CRUD ──────────────────────────────────────────────────────────

@router.get("/api/v1/admin/exams", response_model=list[ExamOut])
async def admin_list_exams(db: Session = Depends(get_db), _email: str = Depends(require_admin)):
    exams = db.query(Exam).order_by(Exam.created_at.desc()).all()
    return [
        ExamOut(
            id=e.id, code=e.code, display_name=e.display_name,
            category=e.category, active=e.active, field_count=len(e.fields),
        )
        for e in exams
    ]


@router.post("/api/v1/admin/exams", response_model=ExamOut, status_code=201)
async def admin_create_exam(
    payload: ExamCreate, db: Session = Depends(get_db), _email: str = Depends(require_admin)
):
    if db.query(Exam).filter(Exam.code == payload.code).first():
        raise HTTPException(status_code=409, detail=f"Exam code '{payload.code}' already exists")
    if db.query(Exam).filter(func.lower(Exam.display_name) == payload.display_name.strip().lower()).first():
        raise HTTPException(
            status_code=409,
            detail=f"An exam named '{payload.display_name}' already exists. Display names must be unique.",
        )
    exam = Exam(**payload.model_dump())
    db.add(exam)
    db.commit()
    db.refresh(exam)
    logger.info("Exam created | code=%s | by=%s", exam.code, _email)
    return ExamOut(id=exam.id, code=exam.code, display_name=exam.display_name,
                    category=exam.category, active=exam.active, field_count=0)


@router.get("/api/v1/admin/exams/{exam_id}", response_model=ExamDetailOut)
async def admin_get_exam(exam_id: int, db: Session = Depends(get_db), _email: str = Depends(require_admin)):
    return _get_exam_or_404(db, exam_id)


@router.put("/api/v1/admin/exams/{exam_id}", response_model=ExamOut)
async def admin_update_exam(
    exam_id: int, payload: ExamUpdate, db: Session = Depends(get_db), _email: str = Depends(require_admin)
):
    exam = _get_exam_or_404(db, exam_id)
    updates = payload.model_dump(exclude_unset=True)
    if "display_name" in updates and updates["display_name"]:
        dupe = db.query(Exam).filter(
            func.lower(Exam.display_name) == updates["display_name"].strip().lower(),
            Exam.id != exam_id,
        ).first()
        if dupe:
            raise HTTPException(
                status_code=409,
                detail=f"An exam named '{updates['display_name']}' already exists. Display names must be unique.",
            )
    for k, v in updates.items():
        setattr(exam, k, v)
    db.commit()
    db.refresh(exam)
    return ExamOut(id=exam.id, code=exam.code, display_name=exam.display_name,
                    category=exam.category, active=exam.active, field_count=len(exam.fields))


@router.delete("/api/v1/admin/exams/{exam_id}", status_code=204)
async def admin_delete_exam(exam_id: int, db: Session = Depends(get_db), _email: str = Depends(require_admin)):
    exam = _get_exam_or_404(db, exam_id)
    db.delete(exam)
    db.commit()
    logger.info("Exam deleted | code=%s | by=%s", exam.code, _email)


# ── Admin: Exam fields ────────────────────────────────────────────────────────

@router.post("/api/v1/admin/exams/{exam_id}/fields", response_model=ExamFieldOut, status_code=201)
async def admin_add_field(
    exam_id: int, payload: ExamFieldCreate, db: Session = Depends(get_db), _email: str = Depends(require_admin)
):
    _get_exam_or_404(db, exam_id)
    if payload.transform not in TRANSFORMS:
        raise HTTPException(status_code=422, detail=f"Invalid transform. Must be one of: {TRANSFORMS}")
    if payload.field_type not in FIELD_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid field_type. Must be one of: {FIELD_TYPES}")

    has_source = bool(payload.source_profile_key)
    has_default = bool(payload.default_value)
    if has_source and has_default:
        raise HTTPException(
            status_code=422,
            detail="Set either a Source Profile Field OR a Default Value, not both.",
        )
    if not has_source and not has_default:
        raise HTTPException(
            status_code=422,
            detail="Set either a Source Profile Field (from extracted data) or a Default Value (fixed constant).",
        )

    field = ExamField(exam_id=exam_id, **payload.model_dump())
    db.add(field)
    db.commit()
    db.refresh(field)
    return field


@router.put("/api/v1/admin/exams/{exam_id}/fields/{field_id}", response_model=ExamFieldOut)
async def admin_update_field(
    exam_id: int, field_id: int, payload: ExamFieldUpdate,
    db: Session = Depends(get_db), _email: str = Depends(require_admin),
):
    field = db.query(ExamField).filter(ExamField.id == field_id, ExamField.exam_id == exam_id).first()
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    updates = payload.model_dump(exclude_unset=True)
    if "transform" in updates and updates["transform"] not in TRANSFORMS:
        raise HTTPException(status_code=422, detail=f"Invalid transform. Must be one of: {TRANSFORMS}")

    # Apply updates first, then validate the resulting state — simplest way
    # to correctly handle partial updates (e.g. only default_value sent).
    for k, v in updates.items():
        setattr(field, k, v)
    has_source = bool(field.source_profile_key)
    has_default = bool(field.default_value)
    if has_source and has_default:
        raise HTTPException(status_code=422, detail="Set either a Source Profile Field OR a Default Value, not both.")
    if not has_source and not has_default:
        raise HTTPException(status_code=422, detail="A field needs either a Source Profile Field or a Default Value.")

    db.commit()
    db.refresh(field)
    return field


@router.delete("/api/v1/admin/exams/{exam_id}/fields/{field_id}", status_code=204)
async def admin_delete_field(
    exam_id: int, field_id: int, db: Session = Depends(get_db), _email: str = Depends(require_admin)
):
    field = db.query(ExamField).filter(ExamField.id == field_id, ExamField.exam_id == exam_id).first()
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    db.delete(field)
    db.commit()


# ── Admin: Photo specs ────────────────────────────────────────────────────────

@router.post("/api/v1/admin/exams/{exam_id}/photo-specs", response_model=ExamPhotoSpecOut, status_code=201)
async def admin_add_photo_spec(
    exam_id: int, payload: ExamPhotoSpecCreate, db: Session = Depends(get_db), _email: str = Depends(require_admin)
):
    _get_exam_or_404(db, exam_id)
    # One spec per asset_type per exam — replace if it already exists
    existing = db.query(ExamPhotoSpec).filter(
        ExamPhotoSpec.exam_id == exam_id, ExamPhotoSpec.asset_type == payload.asset_type
    ).first()
    if existing:
        for k, v in payload.model_dump().items():
            setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return existing

    spec = ExamPhotoSpec(exam_id=exam_id, **payload.model_dump())
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return spec


@router.delete("/api/v1/admin/exams/{exam_id}/photo-specs/{spec_id}", status_code=204)
async def admin_delete_photo_spec(
    exam_id: int, spec_id: int, db: Session = Depends(get_db), _email: str = Depends(require_admin)
):
    spec = db.query(ExamPhotoSpec).filter(ExamPhotoSpec.id == spec_id, ExamPhotoSpec.exam_id == exam_id).first()
    if not spec:
        raise HTTPException(status_code=404, detail="Photo spec not found")
    db.delete(spec)
    db.commit()


# ── Public read: used by upload.html + review.html (any logged-in operator) ──

@router.get("/api/v1/exams")
async def list_active_exams(db: Session = Depends(get_db), _email: str = Depends(require_login)):
    """Populates the exam dropdown in upload.html."""
    exams = db.query(Exam).filter(Exam.active == True).order_by(Exam.display_name).all()  # noqa: E712
    return [{"code": e.code, "display_name": e.display_name, "category": e.category} for e in exams]


@router.get("/api/v1/exams/{code}/fields", response_model=list[ExamFieldOut])
async def get_exam_fields(code: str, db: Session = Depends(get_db), _email: str = Depends(require_login)):
    """Used by review.html to render exam-shaped fields instead of the generic list."""
    exam = db.query(Exam).options(joinedload(Exam.fields)).filter(Exam.code == code, Exam.active == True).first()  # noqa: E712
    if not exam:
        return []  # empty -> review.html falls back to generic universal field view
    return sorted(exam.fields, key=lambda f: f.sort_order)


@router.get("/api/v1/exams/{code}/photo-specs", response_model=list[ExamPhotoSpecOut])
async def get_exam_photo_specs(code: str, db: Session = Depends(get_db), _email: str = Depends(require_login)):
    """Used by upload.html to replace the old hardcoded EXAM_PRESETS object."""
    exam = db.query(Exam).options(joinedload(Exam.photo_specs)).filter(Exam.code == code, Exam.active == True).first()  # noqa: E712
    if not exam:
        return []  # empty -> upload.html falls back to its generic 50KB default
    return exam.photo_specs
