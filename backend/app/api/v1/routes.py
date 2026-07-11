"""
api/v1/routes.py — Milestone 2
"""
from __future__ import annotations
import difflib
import re
import time
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse

from backend.app.core.exceptions import (
    CorruptedFileError, FileTooLargeError, UnsupportedFileTypeError,
)
from backend.app.core.auth import require_login, redirect_if_not_logged_in
from backend.app.core.validators import validate_mobile, validate_email
from backend.app.core.logging import get_logger
from backend.app.schemas.documents import (
    DOCUMENT_LABELS, MILESTONE_2_TYPES, DocumentType, UploadedFile,
)
from backend.app.schemas.errors import ErrorCode, ErrorResponse
from backend.app.schemas.extraction import (
    ExtractionResult, DocumentStatus, ProcessingResponse,
)
from backend.app.services.merger.field_merger import FieldMerger
from backend.app.services.pipeline.orchestrator import DocumentPipeline
from backend.app.utils.file_validator import validate_and_save

logger = get_logger(__name__)
router = APIRouter()

_pipeline_instance: Optional[DocumentPipeline] = None
_merger_instance:   Optional[FieldMerger]       = None


def get_pipeline() -> DocumentPipeline:
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = DocumentPipeline()
    return _pipeline_instance


def get_merger() -> FieldMerger:
    global _merger_instance
    if _merger_instance is None:
        _merger_instance = FieldMerger()
    return _merger_instance


PipelineDep = Annotated[DocumentPipeline, Depends(get_pipeline)]
MergerDep   = Annotated[FieldMerger,      Depends(get_merger)]


def _error(http_status: int, error_code: str, message: str,
           details: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error_code=error_code, message=message,
                         details=details or {})
    return JSONResponse(status_code=http_status, content=body.model_dump())


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    html_path = Path(__file__).parents[4] / "frontend" / "login.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>login.html not found</h1>", status_code=404)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page():
    # Deliberately public — no login required. Chrome Web Store reviewers,
    # and anyone considering using the extension, need to be able to read
    # this without an account.
    html_path = Path(__file__).parents[4] / "frontend" / "privacy.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>privacy.html not found</h1>", status_code=404)


@router.get("/app", response_class=HTMLResponse, include_in_schema=False)
async def upload_page(request: Request):
    redirect = redirect_if_not_logged_in(request)
    if redirect:
        return redirect
    html_path = Path(__file__).parents[4] / "frontend" / "upload.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>upload.html not found</h1>", status_code=404)


@router.get("/review", response_class=HTMLResponse, include_in_schema=False)
async def review_page(request: Request):
    redirect = redirect_if_not_logged_in(request)
    if redirect:
        return redirect
    html_path = Path(__file__).parents[4] / "frontend" / "review.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>review.html not found</h1>", status_code=404)


# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/api/v1/health")
async def health_check() -> dict:
    return {"status": "ok", "service": "Mitra Fill", "version": "2.0.0"}


# ── Session profile storage ───────────────────────────────────────────────────
# Per-operator session store, keyed by the logged-in operator's email —
# replaces the previous single global dict, which was a real correctness
# bug: with one shared slot, Operator A's customer data could be
# silently overwritten by or served to Operator B the moment two
# operators used the app at the same time. Each operator now only ever
# sees their own in-progress customer profile.
#
# Still in-memory (not Redis/a DB table) — fine for a single-process
# pilot deployment, but note two real limits: (1) restarting the server
# clears every operator's in-progress session, and (2) this won't work
# correctly if you ever run more than one server process/worker, since
# each process would have its own separate dict. Both are acceptable
# for now; revisit before a larger multi-instance deployment.
_sessions: dict[str, dict] = {}


@router.post("/api/v1/save-session")
async def save_session(request: Request, email: str = Depends(require_login)) -> JSONResponse:
    """Called by review page when operator clicks 'Use This Data'."""
    body = await request.json()
    profile = body.get("profile", {})
    if not profile:
        return JSONResponse({"ok": False, "error": "Empty profile"}, status_code=400)
    _sessions[email] = profile
    field_count = sum(1 for v in profile.values() if v and isinstance(v, str))
    logger.info("Session saved | operator=%s | fields=%d", email, field_count)
    return JSONResponse({"ok": True, "fields": field_count})


@router.get("/api/v1/get-session")
async def get_session(email: str = Depends(require_login)) -> JSONResponse:
    """
    Called by the Chrome Extension popup to get the current operator's
    profile. Requires login (via credentials: 'include' on the
    extension's fetch call) specifically so it can look up THIS
    operator's session, not just whatever the last person to use the
    app saved.
    """
    profile = _sessions.get(email)
    if not profile:
        return JSONResponse({"ok": False, "profile": None})
    return JSONResponse({"ok": True, "profile": profile})


@router.delete("/api/v1/clear-session")
async def clear_session(email: str = Depends(require_login)) -> JSONResponse:
    """Clear the current operator's active session only — not everyone's."""
    _sessions.pop(email, None)
    return JSONResponse({"ok": True})


@router.get("/api/v1/document-types")
async def document_types() -> dict:
    return {
        "types": [
            {"value": dt.value, "label": DOCUMENT_LABELS[dt]}
            for dt in MILESTONE_2_TYPES
            if dt != DocumentType.AUTO
        ]
    }


@router.post("/api/v1/process-session")
async def process_session(
    pipeline: PipelineDep,
    merger: MergerDep,
    request: Request,
    files: list[UploadFile] = File(default=[]),
    document_types: str = Form(default=""),
    manual_mobile: str = Form(default=""),
    manual_email: str = Form(default=""),
    _email: str = Depends(require_login),
) -> JSONResponse:
    """
    Process one file per document type slot.
    document_types: comma-separated list matching files order.
    e.g. "aadhaar,pan,certificate_degree"
    """
    start_ms = time.monotonic() * 1000

    if len(files) > 10:
        return _error(400, "TOO_MANY_FILES", "Maximum 10 files per session.")
    # Allow session with no files if manual inputs provided
    if not files and not manual_mobile and not manual_email:
        return _error(400, ErrorCode.NO_FILE_PROVIDED, "Provide at least one document or contact detail.")

    # Validate manually-typed contact details BEFORE they ever reach the
    # profile. These are operator-typed (not AI-extracted), so unlike
    # extracted fields — where we flag-for-review rather than block —
    # a hard reject here is appropriate: it's a same-turn, fixable typo,
    # not something requiring the operator to re-examine a document.
    if manual_mobile and manual_mobile.strip():
        is_valid, reason = validate_mobile(manual_mobile.strip())
        if not is_valid:
            return _error(422, "INVALID_MOBILE", reason)
    if manual_email and manual_email.strip():
        is_valid, reason = validate_email(manual_email.strip())
        if not is_valid:
            return _error(422, "INVALID_EMAIL", reason)

    # Parse declared types
    type_list = [t.strip() for t in document_types.split(",") if t.strip()]
    while len(type_list) < len(files):
        type_list.append("aadhaar")  # default — never auto

    # Validate + save each file
    uploaded: list[UploadedFile] = []
    filenames: list[str] = []
    doc_type_enums: list[DocumentType] = []

    for upload, dt_str in zip(files, type_list):
        try:
            try:
                dt = DocumentType(dt_str)
            except ValueError:
                dt = DocumentType.AUTO
            validated = await validate_and_save(upload, dt)
            uploaded.append(validated)
            filenames.append(upload.filename or f"doc_{len(uploaded)}")
            doc_type_enums.append(dt)
        except FileTooLargeError as exc:
            return _error(413, ErrorCode.FILE_TOO_LARGE, exc.message, exc.details)
        except UnsupportedFileTypeError as exc:
            return _error(400, ErrorCode.UNSUPPORTED_FILE_TYPE, exc.message, exc.details)
        except CorruptedFileError as exc:
            return _error(400, ErrorCode.CORRUPTED_FILE, exc.message, exc.details)

    # Run pipeline on each file
    try:
        pipeline_response = await pipeline.process(uploaded)
    except Exception as exc:
        logger.error("Pipeline error | %s", exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Processing error. Please try again.")

    # Build ExtractionResult list for merger — one per uploaded file
    # Build ExtractionResult list for merger
    # IMPORTANT: use pipeline_response.documents (full extraction with ALL fields)
    # not just .aadhaar/.pan typed schemas (which only have 6 fields each)
    extraction_results: list[ExtractionResult] = []

    # First add results that have full DocumentExtraction (all fields including address)
    doc_idx = 0
    for i, dt in enumerate(doc_type_enums):
        # Try to find matching DocumentExtraction in documents list
        doc_extraction = None
        if doc_idx < len(pipeline_response.documents):
            doc_extraction = pipeline_response.documents[doc_idx]
            doc_idx += 1

        result = ExtractionResult(
            document_type=dt.value,
            status=DocumentStatus.SUCCESS,
        )

        # Set full extraction (has ALL fields: address, mobile, etc.)
        # Use detected type from extraction (handles AUTO classification)
        if doc_extraction:
            result.extraction = doc_extraction
            detected_type = doc_extraction.document_type  # e.g. "pan", "aadhaar"
            result.document_type = detected_type           # override "auto"
        else:
            detected_type = dt.value

        # Set typed schemas using detected type, not declared type
        if detected_type == "aadhaar" and pipeline_response.aadhaar:
            result.aadhaar = pipeline_response.aadhaar
        elif detected_type == "pan" and pipeline_response.pan:
            result.pan = pipeline_response.pan

        extraction_results.append(result)

    # Merge fields
    try:
        profile = merger.merge(extraction_results, filenames)
        profile.processing_time_ms = round((time.monotonic() * 1000) - start_ms, 1)
    except Exception as exc:
        logger.error("Merge error | %s", exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, f"Field merging failed: {exc}")

    # Document summary for UI
    doc_summary = []
    for result, filename in zip(extraction_results, filenames):
        # Use detected type from extraction if available, never show "auto"
        display_type = result.document_type
        if display_type == "auto" and result.extraction:
            display_type = result.extraction.document_type
        doc_summary.append({
            "type":       display_type,
            "filename":   filename,
            "status":     result.status,
            "confidence": result.avg_confidence,
            "engine":     result.ocr_engine_used,
        })

    logger.info(
        "Session complete | docs=%d | fields=%d | verified=%d | time=%.0fms",
        len(files), profile.total_fields_extracted,
        profile.verified_fields, profile.processing_time_ms,
    )

    # Inject manually entered contact details directly into profile,
    # preserving the same ProfileField shape ({value, confidence, ...})
    # as every other field, so the review page renders them consistently.
    profile_dict = profile.model_dump()
    if manual_mobile and manual_mobile.strip():
        profile_dict["mobile"] = {
            "value": manual_mobile.strip(), "confidence": 100.0,
            "needs_review": False, "source_doc": "manual",
            "candidates": [], "match_count": 1,
        }
    if manual_email and manual_email.strip():
        profile_dict["email"] = {
            "value": manual_email.strip(), "confidence": 100.0,
            "needs_review": False, "source_doc": "manual",
            "candidates": [], "match_count": 1,
        }

    return JSONResponse(status_code=200, content={
        "status":          "success",
        "profile":         profile_dict,
        "documents":       doc_summary,
        "processing_time_ms": profile.processing_time_ms,
    })


# ── Milestone 1 compatibility ─────────────────────────────────────────────────

@router.post("/api/v1/process", response_model=ProcessingResponse)
async def process_documents(
    pipeline: PipelineDep,
    aadhaar_file: Optional[UploadFile] = File(default=None),
    pan_file: Optional[UploadFile] = File(default=None),
) -> JSONResponse:
    if not aadhaar_file and not pan_file:
        return _error(400, ErrorCode.NO_FILE_PROVIDED, "At least one document required.")

    uploaded: list[UploadedFile] = []
    for upload, dt in [(aadhaar_file, DocumentType.AADHAAR), (pan_file, DocumentType.PAN)]:
        if not upload:
            continue
        try:
            uploaded.append(await validate_and_save(upload, dt))
        except FileTooLargeError as exc:
            return _error(413, ErrorCode.FILE_TOO_LARGE, exc.message, exc.details)
        except UnsupportedFileTypeError as exc:
            return _error(400, ErrorCode.UNSUPPORTED_FILE_TYPE, exc.message, exc.details)
        except CorruptedFileError as exc:
            return _error(400, ErrorCode.CORRUPTED_FILE, exc.message, exc.details)

    try:
        response = await pipeline.process(uploaded)
    except Exception as exc:
        logger.error("Pipeline error | %s", exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Processing error.")

    return JSONResponse(status_code=200, content=response.model_dump())


# ── AI Field Mapping endpoint ─────────────────────────────────────────────────

def _normalize_label(s: str) -> str:
    """Lowercase, strip punctuation/numbering, collapse whitespace — so
    '5. Date Of Birth (DD-MM-YYYY)' and 'Date Of Birth (DD-MM-YYYY)'
    compare as near-identical despite the leading question number."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# Confident-match threshold. Tested against real cases before choosing this:
# genuine matches (admin's configured label vs. the real DOM field's label,
# differing only by a leading "5. " question number) scored 0.96; a
# deliberately unrelated field scored 0.29 — a wide, safe margin.
_LABEL_MATCH_THRESHOLD = 0.85


def _match_exam_fields_directly(fields: list, exam_fields_meta: list) -> dict:
    """
    Deterministically match real DOM form fields to exam-configured fields
    by comparing their labels directly — BEFORE any AI involvement.

    This exists because relying on the AI to choose the right profile key
    among several plausible, similarly-named candidates (e.g. a raw 'dob'
    vs. an exam-specific correctly-formatted date field) proved fragile in
    practice — fixing it for one field repeatedly left another exposed to
    the same ambiguity. Matching directly against what the admin actually
    configured for this exam removes that ambiguity entirely for any field
    it confidently matches: whatever is shown/configured on the review
    page for this exam is what gets used, deterministically, not an AI's
    best guess.

    Returns a mapping dict for whatever it confidently matched. Fields
    that don't match anything here are left for the AI fallback that
    follows, unaffected by this pass.
    """
    mapping: dict = {}
    matched_field_ids: set = set()

    for ef in exam_fields_meta:
        label = ef.get("display_label", "")
        field_key = ef.get("field_key", "")
        if not label or not field_key:
            continue
        norm_target = _normalize_label(label)

        best_field = None
        best_score = 0.0
        for f in fields:
            if f["id"] in matched_field_ids:
                continue
            score = difflib.SequenceMatcher(
                None, norm_target, _normalize_label(f.get("label", ""))
            ).ratio()
            if score > best_score:
                best_score = score
                best_field = f

        if best_field and best_score >= _LABEL_MATCH_THRESHOLD:
            mapping[best_field["id"]] = field_key
            matched_field_ids.add(best_field["id"])

    return mapping


@router.post("/api/v1/map-fields", summary="Map page form fields to customer profile")
async def map_fields(request: Request) -> JSONResponse:
    request = await request.json()
    """
    Takes scanned form fields from the Chrome Extension and the customer
    profile, returns a mapping of field selectors to profile keys.

    Uses Haiku to intelligently match form field labels to profile fields —
    handles different languages, abbreviations, and field orderings used
    by different government portals.
    """
    import anthropic
    from backend.app.core.config import get_settings

    settings = get_settings()
    profile: dict = request.get("profile", {})
    fields: list = request.get("fields", [])

    # Deterministic pass FIRST — match real DOM fields against the admin's
    # exam configuration by label similarity, before any AI involvement.
    # See _match_exam_fields_directly for why: this is what actually
    # guarantees "whatever is shown/configured for this exam is what gets
    # used" rather than depending on an AI's judgment call every time.
    exam_fields_meta = profile.get("_exam_fields_meta", [])
    direct_mapping = (
        _match_exam_fields_directly(fields, exam_fields_meta) if exam_fields_meta else {}
    )
    if direct_mapping:
        logger.info("Direct exam-field matches | count=%d | fields=%s",
                    len(direct_mapping), list(direct_mapping.values()))
        directly_matched_ids = set(direct_mapping.keys())
        fields = [f for f in fields if f["id"] not in directly_matched_ids]

    # Synthesize a generic 'marks_identification' key for forms that have
    # ONE generic "Identification Marks" / "Visible Identification Marks"
    # field rather than separate SSC/Inter/Degree-specific ones (the common
    # case — government forms ask for this once, not per-certificate).
    # SSC memos are now split into two numbered marks (ssc_identification_mark_1/2)
    # since that's how they're actually printed — combine them back into one
    # string here for forms that want a single field.
    #
    # Skip this entirely when exam-specific fields are configured for this
    # session (_exam_field_keys present) — if the operator has an exam set
    # up, its own fields are the intended source of truth, and adding this
    # generic synthesized candidate into the same pool only gives the AI
    # another similarly-named option to wrongly prefer over the exam's
    # actual configured field.
    exam_field_keys = profile.get("_exam_field_keys", [])
    if not exam_field_keys and not profile.get("marks_identification"):
        ssc_marks = ", ".join(filter(None, [
            profile.get("ssc_identification_mark_1"),
            profile.get("ssc_identification_mark_2"),
        ]))
        for key, value in (
            ("degree_marks_identification", profile.get("degree_marks_identification")),
            ("inter_marks_identification", profile.get("inter_marks_identification")),
            ("ssc_marks_identification", ssc_marks),
        ):
            if value:
                profile["marks_identification"] = value
                break

    if not fields:
        # Nothing left for the AI, but we may still have direct matches
        if exam_field_keys:
            return JSONResponse({
                "mapping": direct_mapping,
                "matched": len(direct_mapping),
                "strict": True,
                "exam_total": len(exam_field_keys),
                "exam_matched": len(direct_mapping),
            })
        return JSONResponse({"mapping": direct_mapping, "matched": len(direct_mapping)})

    # Exam-specific field keys/metadata (see review.html's getFinal()) —
    # pulled out here so they don't leak into the profile listing below as
    # fake "fields" (they're metadata, not real profile values), and so
    # the ones NOT already handled by the direct-match pass above can
    # still get an explicit priority mention in the prompt for the AI
    # fallback.
    profile.pop("_exam_fields_meta", None)
    exam_field_keys = profile.pop("_exam_field_keys", [])

    # Build profile summary for the AI. applicant_photo/applicant_signature
    # hold full base64 image data — never dump that into the prompt, just
    # flag that they exist so the model can still map file-upload fields.
    profile_lines = [
        f"  {k}: {v}"
        for k, v in profile.items()
        if v and k not in (
            "documents_processed", "fields_needing_review",
            "applicant_photo", "applicant_signature",
        )
    ]
    if profile.get("applicant_photo"):
        profile_lines.append("  applicant_photo: <image attached>")
    if profile.get("applicant_signature"):
        profile_lines.append("  applicant_signature: <image attached>")
    profile_summary = "\n".join(profile_lines)

    exam_priority_note = ""
    strict_mode_constraint = ""
    if exam_field_keys:
        exam_priority_note = (
            "\n\nIMPORTANT — the following profile keys were specifically "
            "configured and formatted for the exam/application the operator "
            "selected: " + ", ".join(exam_field_keys) + ". "
            "If a form field could plausibly match one of these keys OR a "
            "more generic-looking key for the same real-world data, ALWAYS "
            "prefer the key listed here — it was deliberately configured to "
            "be correct for this exact form, even if its name looks less "
            "standard than a generic alternative."
        )
        strict_mode_constraint = (
            "\n\n*** STRICT MODE ***\n"
            "An exam is configured for this form. YOU MAY ONLY MATCH FORM "
            "FIELDS TO THESE PROFILE KEYS:\n"
            "  " + ", ".join(exam_field_keys) + "\n"
            "Do NOT match form fields to any other profile keys, even if they "
            "seem like good matches (e.g. 'Email' should NOT match 'email' if "
            "'email' is not in the list above — leave it unmapped instead). "
            "A correctly-unmapped field is better than a wrongly-matched one "
            "on a government form."
        )

    # Build field list for the AI
    field_lines = [
        f"  [{i}] id={f['id']!r} label={f['label']!r} type={f.get('type','text')} name={f.get('name','')!r}"
        for i, f in enumerate(fields)
    ]
    field_list = "\n".join(field_lines)

    prompt = f"""You are mapping HTML form fields to customer profile data for Indian government application forms.

Customer Profile (only these keys exist):
{profile_summary}{exam_priority_note}{strict_mode_constraint}

Form Fields Found on Page:
{field_list}

Map each form field to the correct profile key. Consider all common labels used by Indian government portals.

Profile keys:
  PERSONAL:  name, father_name, mother_name, gender, dob, nationality, place_of_birth
  CONTACT:   mobile, email, address, address_line1, address_line2, address_city,
             address_district, address_state, address_pincode
  IDs:       aadhaar_number, pan_number, passport_number, voter_id, dl_number
  PASSPORT:  doi (date of issue), doe (date of expiry)
  EDUCATION: degree, specialization, university, college, year_of_passing, percentage,
             ssc_roll, ssc_percentage, inter_roll, inter_percentage,
             ssc_identification_mark_1, ssc_identification_mark_2, inter_marks_identification, degree_marks_identification,
             marks_identification (generic — use this for a single combined
             "Identification Marks" field that isn't certificate-specific)
  BANKING:   account_number, ifsc, bank_name, branch
  ASSETS:    applicant_photo (file upload), applicant_signature (file upload)

Matching rules — common Indian govt portal field names:
  name         → "Applicant Name", "Full Name", "Name as per Aadhaar", "Name of Candidate"
  father_name  → "Father Name", "Father's Name", "S/O", "D/O", "Guardian Name"
  dob          → "Date of Birth", "DOB", "Birth Date", "Date of Birth (DD/MM/YYYY)"
  aadhaar_number → "Aadhaar Number", "Aadhaar No", "UID Number", "UIDAI Number"
  pan_number   → "PAN Number", "PAN No", "Permanent Account Number"
  mobile       → "Mobile Number", "Mobile No", "Contact Number", "Phone Number", "Registered Mobile"
  email        → "Email ID", "Email Address", "E-Mail ID"
  gender       → "Gender", "Sex"
  address_pincode → "PIN Code", "Pincode", "PIN No", "Postal Code"
  doe          → "Date of Expiry", "Valid Till", "Expiry Date", "Passport Expiry"
  doi          → "Date of Issue", "Issue Date"
  marks_identification → "Identification Marks", "Visible Identification Marks", "Distinguishing Marks"
  applicant_photo (type=file) → "Upload Photo", "Applicant Photo", "Photograph", "Passport Size Photo"
  applicant_signature (type=file) → "Upload Signature", "Applicant Signature", "Signature"

NEVER map: captcha, OTP, password, confirm password, security code, search, or any other
file-upload field (e.g. "Upload Aadhaar Card", "Upload Marksheet") — the ONLY file-upload
fields that should ever be mapped are the applicant's own photo and signature, and only
when the profile actually has applicant_photo / applicant_signature attached.
Return null for fields with no profile data.

Return ONLY a JSON object like:
{{"#aadhaarInput": "aadhaar_number", "#mobileNo": "mobile", "#captcha": null}}

JSON now:"""

    try:
        if not settings.has_anthropic_key:
            # Fallback: simple keyword matching without AI
            mapping = _keyword_map_fields(fields, profile)
        else:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text if response.content else "{}"

            import json, re
            clean = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`")
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                clean = match.group()
            mapping = json.loads(clean)

        # Only keep mappings where the profile has a value
        mapping = {
            k: v for k, v in mapping.items()
            if v and profile.get(v)
        }
        # Merge in the deterministic exam-field matches from before the AI
        # call — these take priority and were already removed from the
        # `fields` list the AI saw, so there's no overlap/conflict here.
        mapping.update(direct_mapping)

        # STRICT MODE: if an exam is selected, only allow matches to
        # configured exam fields. The AI prompt includes this constraint,
        # but filter again as a safety net — better to unmatch than to
        # silently fill a non-configured field that might contradict the
        # form's other answers (like "New Name / Changed Name" example).
        if exam_field_keys:
            exam_set = set(exam_field_keys)
            mapping = {k: v for k, v in mapping.items() if v in exam_set}
            exam_matched = sum(1 for v in mapping.values() if v in exam_set and v not in ("applicant_photo", "applicant_signature"))
            file_matched = sum(1 for v in mapping.values() if v in ("applicant_photo", "applicant_signature"))
            logger.info(
                "Strict exam mode (AI constrained) | exam fields matched=%d/%d | file inputs=%d",
                exam_matched, len(exam_field_keys), file_matched,
            )
            return JSONResponse({
                "mapping": mapping,
                "matched": len(mapping),
                "strict": True,
                "exam_total": len(exam_field_keys),
                "exam_matched": exam_matched,
            })

        logger.info(
            "Field mapping | page_fields=%d | matched=%d | direct=%d",
            len(fields), len(mapping), len(direct_mapping),
        )
        return JSONResponse({"mapping": mapping, "matched": len(mapping)})

    except Exception as exc:
        logger.error("Field mapping error | %s", exc)
        # Return keyword-based fallback, still including direct matches
        mapping = _keyword_map_fields(fields, profile)
        mapping.update(direct_mapping)
        # Apply strict mode filter to fallback too
        if exam_field_keys:
            exam_set = set(exam_field_keys)
            mapping = {k: v for k, v in mapping.items() if v in exam_set}
            exam_matched = sum(1 for v in mapping.values() if v in exam_set and v not in ("applicant_photo", "applicant_signature"))
            return JSONResponse({
                "mapping": mapping,
                "matched": len(mapping),
                "strict": True,
                "exam_total": len(exam_field_keys),
                "exam_matched": exam_matched,
            })
        return JSONResponse({"mapping": mapping, "matched": len(mapping)})


def _keyword_map_fields(fields: list, profile: dict) -> dict:
    """
    Portal-aware keyword field mapper.
    Covers UIDAI, NSDL, Passport, eDistrict, and generic govt portals.
    Longer keyword wins (more specific match).
    """
    # Skip these always — never autofill
    SKIP = {
        "captcha","recaptcha","otp","one time","password","confirm password",
        "re-enter","retype","verification code","security code",
        "submit","search","upload","file","image","photo",
    }

    # (profile_key, [keywords ordered most-specific first])
    RULES = [
        # ── Identity ────────────────────────────────────────────────────────
        ("aadhaar_number", [
            "aadhaar number","aadhaar no","aadhar number","aadhar no",
            "uid number","uid no","uidai number","aadhaar","aadhar","uid",
        ]),
        ("pan_number", [
            "permanent account number","pan card number","pan number","pan no","pan",
        ]),
        ("passport_number", [
            "passport number","passport no","passport",
        ]),
        ("voter_id", [
            "epic number","voter id number","voter card","voter id","epic",
        ]),
        ("dl_number", [
            "driving licence number","driving license number","dl number","dl no",
            "driving licence","driving license","licence number",
        ]),

        # ── Name ────────────────────────────────────────────────────────────
        ("name", [
            "applicant full name","applicant name","candidate full name",
            "candidate name","full name of applicant","name of applicant",
            "name of student","name of candidate","name of holder",
            "account holder name","subscriber name","full name","your name",
            "name as per aadhaar","name as per pan","name",
        ]),
        ("father_name", [
            "father full name","father's full name","fathers full name",
            "father name","father's name","fathers name",
            "guardian name","guardian's name","s/o","d/o","w/o","parent name",
        ]),
        ("mother_name", [
            "mother full name","mother's full name","mother name","mother's name",
        ]),

        # ── DOB ─────────────────────────────────────────────────────────────
        ("dob", [
            "date of birth","birth date","d.o.b","dob","date of birth (dd/mm/yyyy)",
            "birth date (dd/mm/yyyy)","date of birth dd mm yyyy",
        ]),

        # ── Gender ──────────────────────────────────────────────────────────
        ("gender", ["gender","sex"]),

        # ── Contact ─────────────────────────────────────────────────────────
        ("mobile", [
            "mobile number","mobile no","phone number","phone no",
            "contact number","contact no","cell number","whatsapp number",
            "registered mobile","registered mobile number",
            "mobile","phone","contact",
        ]),
        ("email", [
            "email address","email id","e-mail address","e-mail id",
            "email","e-mail","mail id","mail address",
        ]),

        # ── Address ─────────────────────────────────────────────────────────
        ("address_line1", [
            "address line 1","address line1","house number","flat number",
            "door number","plot number","street address line 1",
        ]),
        ("address_line2", [
            "address line 2","address line2","street","locality","area",
            "street address line 2",
        ]),
        ("address_city", [
            "city name","city / town","city/town","town name","city","town",
        ]),
        ("address_district", ["district name","district"]),
        ("address_state",    ["state name","state"]),
        ("address_pincode", [
            "pin code","pincode","zip code","postal code","pin no","pin",
        ]),
        ("address", [
            "permanent address","residential address","current address","address",
        ]),

        # ── Nationality ──────────────────────────────────────────────────────
        ("nationality", ["nationality","citizenship"]),

        # ── Passport-specific ────────────────────────────────────────────────
        ("doe", [
            "date of expiry","expiry date","valid till","valid upto",
            "passport expiry","expiry","valid until",
        ]),
        ("doi", [
            "date of issue","issue date","date of issue of passport",
        ]),
        ("place_of_birth", ["place of birth","birth place","city of birth"]),

        # ── Education ────────────────────────────────────────────────────────
        ("degree",          ["degree name","course name","programme","degree"]),
        ("specialization",  ["specialization","branch","stream","subject"]),
        ("university",      ["university name","university"]),
        ("college",         ["college name","institution","college"]),
        ("year_of_passing", ["year of passing","passing year","year of completion"]),
        ("percentage",      ["percentage","marks percentage","cgpa","gpa"]),
        ("ssc_roll",        ["ssc hall ticket","ssc roll","10th roll","hall ticket"]),
        ("ssc_percentage",  ["ssc percentage","10th percentage","class 10"]),
        ("inter_roll",      ["intermediate hall ticket","inter roll","12th roll"]),
        ("inter_percentage",["intermediate percentage","inter percentage","12th percentage"]),
        ("marks_identification", [
            "visible identification marks","identification marks","identification mark",
            "distinguishing marks","distinguishing mark","visible marks",
        ]),
        ("ssc_identification_mark_1", [
            "identification mark 1","identification mark1","visible mark of identification 1",
            "permanent visible mark of identification 1","distinguishing mark 1",
        ]),
        ("ssc_identification_mark_2", [
            "identification mark 2","identification mark2","visible mark of identification 2",
            "permanent visible mark of identification 2","distinguishing mark 2",
        ]),

        # ── Banking ──────────────────────────────────────────────────────────
        ("account_number",  ["account number","bank account number","acc no"]),
        ("ifsc",            ["ifsc code","ifsc"]),
        ("bank_name",       ["bank name","name of bank"]),
    ]

    # File-upload fields (photo/signature) — handled separately below since
    # the generic SKIP set (which blocks "upload"/"photo"/"file") would
    # otherwise hide them from every other file-upload field on the form.
    ASSET_RULES = [
        ("applicant_signature", [
            "applicant signature","upload signature","candidate signature",
            "upload your signature","signature",
        ]),
        ("applicant_photo", [
            "applicant photo","upload photo","photograph","passport size photo",
            "recent photograph","candidate photo","photo",
        ]),
    ]

    # Build combined text for each field
    def field_text(f):
        return " ".join([
            f.get("label",""), f.get("name",""),
            f.get("placeholder",""), f.get("ariaLabel",""),
        ]).lower()

    mapping = {}
    for field in fields:
        combined = field_text(field)

        # File-upload fields: only ever map the applicant's own photo/signature.
        if field.get("type") == "file":
            for profile_key, keywords in ASSET_RULES:
                if profile.get(profile_key) and any(kw in combined for kw in keywords):
                    mapping[field["id"]] = profile_key
                    break
            continue

        # Skip captcha/otp/password
        if any(s in combined for s in SKIP):
            continue
        if not profile:
            continue

        best_key = None
        best_score = 0

        for profile_key, keywords in RULES:
            if not profile.get(profile_key):
                continue
            for kw in keywords:
                if kw in combined:
                    score = len(kw)   # longer = more specific
                    if score > best_score:
                        best_score = score
                        best_key = profile_key
                    break  # first match wins for this profile_key

        if best_key:
            mapping[field["id"]] = best_key

    return mapping


# ── Document classifier endpoint ──────────────────────────────────────────────

@router.post("/api/v1/classify", summary="Classify document type from image")
async def classify_document(
    pipeline: PipelineDep,
    files: list[UploadFile] = File(default=[]),
    _email: str = Depends(require_login),
) -> JSONResponse:
    """
    Fast document type detection — runs OCR on a small thumbnail
    and uses keyword classifier to detect document type.
    Called by the upload page to show "✓ Aadhaar Detected" immediately.
    Does NOT run full extraction — just classifies.
    """
    from backend.app.services.ai.classifier import DocumentClassifier
    from backend.app.services.ocr.document_preprocessor import DocumentPreprocessor
    import pytesseract, tempfile, shutil
    from PIL import Image
    import numpy as np, cv2

    classifier = DocumentClassifier()
    preprocessor = DocumentPreprocessor()
    results = []

    for upload in files[:10]:
        tmp_path = None
        try:
            # Save to temp file
            suffix = Path(upload.filename or "doc.jpg").suffix or ".jpg"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            content_bytes = await upload.read()
            tmp.write(content_bytes)
            tmp.close()
            tmp_path = Path(tmp.name)

            # Quick preprocess — just resize to small thumbnail
            try:
                from PIL import Image as PILImage
                img = PILImage.open(tmp_path).convert("RGB")
                # Resize to 800px longest edge for fast OCR
                w, h = img.size
                scale = min(800 / max(w, h), 1.0)
                if scale < 1.0:
                    img = img.resize((int(w*scale), int(h*scale)), PILImage.LANCZOS)
                arr = np.array(img)
                grey = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                text = pytesseract.image_to_string(grey, config="--oem 3 --psm 6")
            except Exception:
                text = ""

            # Classify
            if text.strip():
                doc_type, confidence = classifier.classify(text)
            else:
                doc_type, confidence = DocumentType.AUTO, 0.0

            results.append({
                "filename": upload.filename,
                "doc_type": doc_type.value,
                "confidence": round(confidence, 2),
            })

        except Exception as exc:
            logger.warning("Classify failed | file=%s | %s", upload.filename, exc)
            results.append({
                "filename": upload.filename,
                "doc_type": "auto",
                "confidence": 0.0,
            })
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    return JSONResponse({"results": results})
