"""
scripts/seed_exams.py
-----------------------
One-time seed script. Run manually after the new tables exist:

    python -m backend.scripts.seed_exams

What it does:
  1. Migrates the 5 exams that were hardcoded in upload.html's old
     EXAM_PRESETS JS object (upsc, ssc_cgl, ibps_po, gate, neet_ug) into
     the exam_photo_specs table, preserving the exact same pixel/KB
     values — so photo/signature auto-resize keeps working identically
     once upload.html is switched to fetch specs from the API.
  2. Adds one new exam (rrb_ntpc) as a worked example of field-level
     mapping, since that was the concrete example used to design this
     feature (SSC wants "Full Name", RRB wants separate First/Last Name).
  3. Adds a basic field layout for ssc_cgl and rrb_ntpc so there's a
     real example to look at in the admin UI and on the review page,
     rather than an empty field list.

Safe to re-run — skips any exam whose code already exists.
"""

from backend.app.db.database import SessionLocal, init_db
from backend.app.db.models import Exam, ExamField, ExamPhotoSpec

# (code, display_name, category, photo_spec, signature_spec)
# Values copied verbatim from upload.html's old EXAM_PRESETS.
LEGACY_PRESETS = [
    ("upsc", "UPSC Civil Services Examination", "upsc",
     dict(width_px=350, height_px=350, min_kb=20, max_kb=300),
     dict(width_px=350, height_px=150, min_kb=20, max_kb=300)),
    ("ssc_cgl", "SSC Combined Graduate Level Examination", "ssc",
     dict(width_px=100, height_px=120, min_kb=20, max_kb=50),
     dict(width_px=40, height_px=60, min_kb=10, max_kb=20)),
    ("ibps_po", "IBPS Probationary Officer Recruitment", "banking",
     dict(width_px=200, height_px=230, min_kb=20, max_kb=50),
     dict(width_px=140, height_px=60, min_kb=10, max_kb=20)),
    ("gate", "Graduate Aptitude Test in Engineering (GATE)", "other",
     dict(width_px=480, height_px=640, min_kb=5, max_kb=1024),
     dict(width_px=160, height_px=560, min_kb=5, max_kb=300)),
    ("neet_ug", "National Eligibility cum Entrance Test (UG)", "other",
     dict(width_px=200, height_px=230, min_kb=10, max_kb=200),
     dict(width_px=200, height_px=80, min_kb=4, max_kb=30)),
]

NEW_EXAMPLE_EXAM = (
    "rrb_ntpc", "RRB NTPC", "railway",
    dict(width_px=200, height_px=230, min_kb=20, max_kb=50),
    dict(width_px=140, height_px=60, min_kb=10, max_kb=20),
)

# Basic demo field layouts — real deployments should extend these via
# the admin UI to cover every field the actual application form needs.
SSC_FIELDS = [
    dict(display_label="Full Name", field_key="full_name", source_profile_key="name",
         transform="verbatim", field_type="text", required=True, sort_order=1),
    dict(display_label="Father's Name", field_key="father_name", source_profile_key="father_name",
         transform="verbatim", field_type="text", required=True, sort_order=2),
    dict(display_label="Date of Birth", field_key="dob", source_profile_key="dob",
         transform="date_ddmmyyyy", field_type="date", required=True, sort_order=3),
    dict(display_label="Gender", field_key="gender", source_profile_key="gender",
         transform="verbatim", field_type="dropdown", required=True, sort_order=4),
    dict(display_label="Mobile Number", field_key="mobile", source_profile_key="mobile",
         transform="verbatim", field_type="text", required=True, sort_order=5),
    dict(display_label="Email Address", field_key="email", source_profile_key="email",
         transform="verbatim", field_type="text", required=False, sort_order=6),
]

RRB_FIELDS = [
    dict(display_label="First Name", field_key="first_name", source_profile_key="name",
         transform="split_first_word", field_type="text", required=True, sort_order=1),
    dict(display_label="Last Name", field_key="last_name", source_profile_key="name",
         transform="split_remaining_words", field_type="text", required=True, sort_order=2),
    dict(display_label="Father's Name", field_key="father_name", source_profile_key="father_name",
         transform="verbatim", field_type="text", required=True, sort_order=3),
    dict(display_label="Date of Birth", field_key="dob", source_profile_key="dob",
         transform="date_ddmmyyyy", field_type="date", required=True, sort_order=4),
    dict(display_label="Gender", field_key="gender", source_profile_key="gender",
         transform="verbatim", field_type="dropdown", required=True, sort_order=5),
    dict(display_label="Mobile Number", field_key="mobile", source_profile_key="mobile",
         transform="verbatim", field_type="text", required=True, sort_order=6),
]


def seed():
    init_db()
    db = SessionLocal()
    try:
        created, skipped = [], []

        for code, name, category, photo, sig in LEGACY_PRESETS:
            if db.query(Exam).filter(Exam.code == code).first():
                skipped.append(code)
                continue
            exam = Exam(code=code, display_name=name, category=category, active=True)
            db.add(exam)
            db.flush()  # get exam.id before adding specs
            db.add(ExamPhotoSpec(exam_id=exam.id, asset_type="photo", **photo))
            db.add(ExamPhotoSpec(exam_id=exam.id, asset_type="signature", **sig))
            if code == "ssc_cgl":
                for f in SSC_FIELDS:
                    db.add(ExamField(exam_id=exam.id, **f))
            created.append(code)

        code, name, category, photo, sig = NEW_EXAMPLE_EXAM
        if not db.query(Exam).filter(Exam.code == code).first():
            exam = Exam(code=code, display_name=name, category=category, active=True)
            db.add(exam)
            db.flush()
            db.add(ExamPhotoSpec(exam_id=exam.id, asset_type="photo", **photo))
            db.add(ExamPhotoSpec(exam_id=exam.id, asset_type="signature", **sig))
            for f in RRB_FIELDS:
                db.add(ExamField(exam_id=exam.id, **f))
            created.append(code)
        else:
            skipped.append(code)

        db.commit()
        print(f"Seeded {len(created)} exam(s): {created}")
        if skipped:
            print(f"Skipped (already exist): {skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
