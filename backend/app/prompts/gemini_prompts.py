"""
prompts/gemini_prompts.py
--------------------------
Two-stage Vision LLM prompts for Indian government documents.

Stage 1: CLASSIFY_PROMPT  → one-word document type
Stage 2: PROMPTS[type]    → structured JSON with all fields

Design principles:
- Every field has explicit instructions — no ambiguity for the LLM
- Format normalization is specified in the prompt (YYYY-MM-DD, digits only, etc.)
- Fields the LLM cannot see should return null, never invented
- Ignore decorative elements: QR codes, logos, watermarks, signatures, photos
"""

from backend.app.schemas.documents import DocumentType

# ── Stage 1: Classification ────────────────────────────────────────────────────

CLASSIFY_PROMPT = """Look at this Indian document image and identify what type it is.

Reply with ONLY one of these exact values — nothing else:
  aadhaar
  pan
  passport
  driving_license
  voter_id
  certificate_ssc
  certificate_inter
  certificate_degree
  bank_passbook
  salary_slip
  unknown

Key identifiers — read carefully to distinguish similar documents:

- aadhaar:            "GOVERNMENT OF INDIA" header, Aadhaar/UIDAI logo, large 12-digit number, photo of person
- pan:                "INCOME TAX DEPARTMENT", "Permanent Account Number", 10-char PAN like ABCDE1234F
- passport:           "Republic of India", "Passport", MRZ lines (<<<) at bottom, country code IND
- driving_license:    "Driving Licence", vehicle classes (LMV/MCWG), RTO/Transport Department
- voter_id:           "Election Commission of India", "EPIC Number", constituency name
- certificate_ssc:    "Board of Secondary Education", "SSC"/"10th Class", single subject table,
                      school name — NOT a college/university
- certificate_inter:  "Board of Intermediate Education"/"BIEAP"/"CBSE 12th", Junior College,
                      subjects like MPC/BiPC/CEC, 2-year programme — NOT a university degree
- certificate_degree: "University" + "Bachelor of Technology"/"B.Tech"/"B.E."/"B.Sc"/"Master",
                      "Consolidated Marks Memo"/"Credit Sheet"/"Provisional Certificate",
                      semester-wise marks table with I Year/II Year/III Year/IV Year,
                      CGPA, CMM No — this is a 3-4 year university degree
- bank_passbook:      Bank name, "Savings/Current Account", IFSC code, transaction entries
- salary_slip:        "Salary Slip"/"Pay Slip", Basic/HRA/PF/Gross/Net Pay columns

IMPORTANT: A marks memo from a college/university with "Bachelor of Technology" and CGPA
is certificate_degree, NOT certificate_inter — even if it has a Hall Ticket number.

Reply with one word only:"""

# ── Base rules shared by all extraction prompts ────────────────────────────────

_BASE = """You are extracting structured data from an Indian government document image.

STRICT RULES:
1. Return ONLY a valid JSON object. No explanation, no markdown, no ```json fences.
2. Every field must follow: {"value": "...", "confidence": 0-100}
3. Confidence guide:
   90-100 = text is clearly printed and unambiguous
   70-89  = visible but slightly unclear or partially obscured
   50-69  = inferred from partial text
   0      = field not visible — set value to null
4. NEVER invent or guess. If not clearly visible → null.
5. Ignore completely: QR codes, barcodes, holograms, watermarks, logos,
   signatures, photos of people, decorative borders.
6. Format dates as YYYY-MM-DD. If card shows DD-MM-YYYY or DD/MM/YYYY, convert it.
7. Strip leading/trailing spaces from all values.

"""

# ── Stage 2: Document-specific extraction prompts ──────────────────────────────

PROMPTS = {

    DocumentType.AADHAAR: _BASE + """
Document: Aadhaar Card (UIDAI — Unique Identification Authority of India)

Layout:
  FRONT — has: cardholder photo, name in English below Telugu/Hindi name,
               "DOB:" label, gender symbol (♀=Female ♂=Male), 12-digit Aadhaar number,
               optional mobile number, VID number below the Aadhaar number
  BACK  — has: C/O or S/O address block, repeats Aadhaar number, QR code

Extract ALL visible fields:
{
  "name":             "Full name in English only. Title case. e.g. Enuganti Kavya. Do NOT include Telugu/Hindi script.",
  "gender":           "Exactly one of: Male, Female, Transgender",
  "dob":              "Date of birth in YYYY-MM-DD. Card shows DD-MM-YYYY — convert it.",
  "aadhaar_number":   "12 digits printed in large bold font. Digits only, no spaces.",
  "mobile":           "10-digit mobile if printed. Digits only. null if not printed.",
  "father_name":      "Name after C/O or S/O in the address block. Title case. NOT the cardholder.",
  "address_line1":    "First address line: house/flat number and building. e.g. MD085 Block 7",
  "address_line2":    "Second address line: street or locality. e.g. KL Mahendra Nagar",
  "address_city":     "City/town/village name (VTC). TWO CASES — check which applies:\n     CASE A (e-Aadhaar, labels present): use the value next to the label literally printed 'VTC:'.\n     CASE B (physical/PVC card, address is one continuous unlabeled block): the VTC is usually the FIRST place-name-like word/phrase after the street/locality, often repeated once (e.g. '...Bhupalpalle Jayashankar Bhupalpally...' — 'Bhupalpalle' here is the VTC).\n     Title case.",
  "address_district": "District name. TWO CASES — check which applies:\n     CASE A (e-Aadhaar, labels present): use ONLY the value next to the label literally printed 'District:'. e-Aadhaar cards ALSO print a separate 'Sub District:' label nearby (a smaller division inside the district, e.g. a mandal/taluk) — that is a DIFFERENT value, do not use it here even though it sits right next to 'District:'. Verified example: card shows 'Sub District: Madhira' and 'District: Khammam' → address_district is 'Khammam', not 'Madhira'.\n     CASE B (physical/PVC card, no labels — address is one continuous run of place names before the state): district is normally the LAST place-name-like word/phrase immediately before the state name. Verified example: unlabeled address reads '...MD-85 K.L Mahendra Nagar, Bhupalpalle Jayashankar Bhupalpally, Telangana - 506169' → the word right before 'Telangana' is 'Jayashankar Bhupalpally' — that is the district (NOT 'Bhupalpalle', which appears earlier in the same string as the VTC/sub-district and is a different, smaller place).\n     If genuinely unsure which unlabeled word is the district, still give your best single guess rather than returning null — a downstream check will flag it for the operator to verify either way.\n     Title case.",
  "address_state":    "State name — the state is reliable to find even on unlabeled cards: it is normally the LAST place-name word before the PIN code (often after a comma or hyphen, e.g. '...Telangana - 506169'). Title case. e.g. Telangana",
  "address_pincode":  "6-digit PIN code. Digits only."
}
""",

    DocumentType.PAN: _BASE + """
Document: PAN Card (Permanent Account Number — Income Tax Department)

Layout (top to bottom):
  - "INCOME TAX DEPARTMENT" + "GOVT. OF INDIA" header
  - Cardholder photo (top left)
  - PAN number in large bold text: format AAAAA0000A (5 letters, 4 digits, 1 letter)
  - "नाम / Name" label → cardholder name on next line
  - "पिता का नाम / Father's Name" label → father name on next line
  - "जन्म की तारीख / Date of Birth" label → DOB on next line (DD/MM/YYYY format)
  - QR code top right

Extract:
{
  "pan_number":   "PAN number. Exactly 10 characters. Always uppercase. Format: AAAAA0000A.",
  "name":         "Cardholder name. Title case. Line immediately after 'Name' label.",
  "father_name":  "Father's name. Title case. Line after 'Father' label.",
  "dob":          "Date of birth. Convert DD/MM/YYYY to YYYY-MM-DD."
}
""",

    DocumentType.PASSPORT: _BASE + """
Document: Indian Passport — may include ONE OR TWO images: the front
personal data page (with photo) and/or the back page (family/address details).

CRITICAL — cross-verify DOB using the MRZ (machine-readable zone):
The two MRZ lines at the bottom of the front page are printed in plain
monospace font with NO background watermark interference, making them far
more reliable than the printed date fields (which often overlap a faint
ghost photo/security pattern and are easy to misread digit-by-digit).
The second MRZ line has this fixed format:
  <PassportNo><CheckDigit><CountryCode><YYMMDD><CheckDigit><Sex>...
Example: N6004402<1IND9406203M2512211<<<<<<<<<<<<<<8
                        ^^^^^^
The 7 characters right after the 3-letter country code (here "IND") are
YYMMDD + 1 check digit: "9406203" → YY=94, MM=06, DD=20 → DOB = 1994-06-20.
ALWAYS decode this and use it to verify (and correct, if they disagree) the
printed "Date of Birth" field — the MRZ digits are the source of truth if
the printed date looks at all ambiguous or unclear due to the background
pattern. Do the same digit-by-digit care for the printed Date of Issue and
Date of Expiry fields, which don't appear in the MRZ but sit in the same
visually-cluttered area and are equally prone to misreading — read each
digit individually and double-check before finalizing.

CRITICAL — these two pages have DIFFERENT "Name" fields. Do not confuse them:
  FRONT page (has photo):
    - "Surname" label → cardholder's surname
    - "Given Name(s)" label → cardholder's given name(s)
    - These two combine to form the CARDHOLDER's name → use for "name" field
  BACK page (no photo, has barcode):
    - "Name of Father / Legal Guardian" label → this is the FATHER's name,
      NOT the cardholder's name. Use for "father_name" field ONLY.
    - "Name of Mother" label → use for "mother_name" field ONLY.
  NEVER put a back-page "Name of Father/Mother" value into the "name" field.
  The "name" field must ALWAYS come from the FRONT page's Surname + Given Name(s).

If only the back page is visible (no front/photo page provided), set "name",
"surname", "given_name", "dob", "gender", "doi", "doe" to null — do not guess
or substitute the father's name as the cardholder's name.

Extract:
{
  "passport_number":  "Passport number. Uppercase. e.g. A1234567 or N6004402. Same on both pages if both visible.",
  "surname":          "FRONT page only. Surname under 'Surname' label. Uppercase.",
  "given_name":       "FRONT page only. Given name(s) under 'Given Name(s)' label. Uppercase.",
  "name":             "FRONT page only. Full name = surname + given name, e.g. Surname 'ENUGANTI' + Given Name 'PRASHANTH' → 'Enuganti Prashanth'. Title case. NEVER use the back page's father/mother/spouse name here.",
  "father_name":      "BACK page only. Name under 'Name of Father / Legal Guardian' label. Title case.",
  "mother_name":      "BACK page only. Name under 'Name of Mother' label. Title case.",
  "nationality":      "FRONT page. Nationality as printed. e.g. Indian.",
  "gender":           "FRONT page. M → Male, F → Female.",
  "dob":              "FRONT page. Date of birth. Cross-check against the MRZ-decoded date as instructed above — use the MRZ value if the printed date is at all unclear. Convert to YYYY-MM-DD.",
  "doi":              "FRONT page. Date of issue, from 'Date of Issue' label. Read each digit carefully — this field sits over a watermark pattern. Convert DD/MM/YYYY to YYYY-MM-DD.",
  "doe":              "FRONT page. Date of expiry, from 'Date of Expiry' label. Read each digit carefully — this field sits over a watermark pattern. Convert DD/MM/YYYY to YYYY-MM-DD.",
  "place_of_birth":   "FRONT page. Place of birth if printed. Title case.",
  "address_line1":    "BACK page. First line of address under 'Address' label, e.g. house/street. Title case.",
  "address_line2":    "BACK page. Second line of address if present (e.g. locality/city/district combined on one printed line). Title case.",
  "address_pincode":  "BACK page. 6-digit PIN code from the address block, often after 'PIN:'. Digits only.",
  "address_state":    "BACK page. State name from the address block. Title case.",
  "file_number":      "BACK page. File number under 'File No.' label. Uppercase."
}
""",

    DocumentType.DRIVING_LICENSE: _BASE + """
Document: Indian Driving Licence

DL number format varies by state:
  e.g. TS04 20150012345 or KA01-2011-0012345 or MH12-20110-012345

Extract:
{
  "dl_number":        "Driving Licence number. Uppercase, no spaces.",
  "name":             "Licence holder full name. Title case.",
  "father_name":      "Father or husband name if printed. Title case.",
  "dob":              "Date of birth. YYYY-MM-DD.",
  "doi":              "Date of issue. YYYY-MM-DD.",
  "doe":              "Non-transport validity date (date of expiry). YYYY-MM-DD.",
  "address_line1":    "First address line.",
  "address_line2":    "Second address line.",
  "address_city":     "City. Title case.",
  "address_state":    "State. Title case.",
  "address_pincode":  "PIN code. 6 digits only.",
  "vehicle_classes":  "Authorised vehicle classes comma-separated. e.g. LMV, MCWG, TRANS"
}
""",

    DocumentType.VOTER_ID: _BASE + """
Document: Voter ID Card (EPIC — Electoral Photo Identity Card)

Extract:
{
  "voter_id":     "EPIC number. Uppercase alphanumeric. e.g. ABC1234567.",
  "name":         "Voter name. Title case.",
  "father_name":  "Father or husband name. Title case.",
  "gender":       "Male, Female, or Other.",
  "dob":          "Date of birth if printed. YYYY-MM-DD.",
  "address":      "Residential address. Single line.",
  "constituency": "Assembly or parliamentary constituency name."
}
""",

    DocumentType.CERTIFICATE_SSC: _BASE + """
Document: SSC / 10th Class Certificate or Marks Memo (Board Examination)

Common boards: Board of Secondary Education, Telangana State; Board of
Secondary Education, Andhra Pradesh (BSEAP); CBSE; ICSE — but ALWAYS
transcribe the exact board name as it is printed on the document. Do NOT
guess or substitute an abbreviation or a rearranged/canonical form if the
full name is printed — copy the printed text exactly (e.g. a certificate
headed "Board of Secondary Education, Telangana State" must NOT be returned
as "Telangana State Board of Secondary Education"). Only use a short form
like "BSEAP" or "CBSE" if that is literally what is printed.

Extract:
{
  "ssc_name":       "Student full name exactly as printed. Title case.",
  "father_name":    "Father name. Title case.",
  "mother_name":    "Mother name if printed. Title case.",
  "dob":            "Date of birth. YYYY-MM-DD.",
  "ssc_roll":       "Hall ticket number or roll number as printed.",
  "ssc_school":     "School name as printed.",
  "ssc_board":      "Transcribe the board name exactly as printed at the top of the document, word for word. Do not abbreviate, rearrange, or substitute unless that is the literal printed text.",
  "ssc_year":       "Year of passing. 4-digit year only.",
  "ssc_percentage": "Overall percentage as printed. e.g. 92.5. Include % symbol.",
  "ssc_identification_mark_1": "The FIRST physical identification mark/remark printed on the memo — NOT exam marks or grades. SSC memos commonly print these as a numbered list, e.g. '1. A mole on the neck  2. A mole on left hand middle finger', or as two separate lines/fields on the document itself. Extract only the first one. Copy the exact text as printed (without the leading '1.' number). If none is printed, return null.",
  "ssc_identification_mark_2": "The SECOND physical identification mark/remark printed on the memo, if a second one exists (see ssc_identification_mark_1 for context). Copy the exact text as printed (without the leading '2.' number). If only one mark is printed, or none at all, return null — do NOT invent a second mark."
}
""",

    DocumentType.CERTIFICATE_INTER: _BASE + """
Document: Intermediate / 12th Class Certificate or Marks Memo (Junior College)

Common boards: Telangana State Board of Intermediate Education (Hyderabad),
Board of Intermediate Education Andhra Pradesh (BIEAP), CBSE, CISCE — but ALWAYS
transcribe the exact board name as it is printed on the document. Do NOT guess
or substitute an abbreviation if the full name is printed — copy the printed
text exactly (e.g. "Telangana State Board of Intermediate Education : Hyderabad").
Only use a short form like "BIEAP" or "CBSE" if that is literally what is printed.

Extract:
{
  "inter_name":       "Student full name. Title case.",
  "father_name":      "Father name. Title case.",
  "mother_name":      "Mother name if printed. Title case.",
  "dob":              "Date of birth. YYYY-MM-DD.",
  "inter_roll":       "Hall ticket number, registration number, or roll number — read the exact label and digits printed (e.g. 'REGD NUMBER').",
  "inter_college":    "Junior college name as printed, including DIST/COLLEGE code area if part of the address line.",
  "inter_board":      "Transcribe the board name exactly as printed at the top of the document. Do not abbreviate or substitute unless that is the literal printed text.",
  "inter_year":       "Year of passing. 4-digit year.",
  "inter_percentage": "Overall percentage. e.g. 88.3.",
  "inter_group":      "Group/stream exactly as printed. e.g. MPC, BiPC, CEC, MEC, HEC.",
  "inter_marks_identification": "Physical identification marks/remarks printed on the memo — NOT exam marks or grades. Usually a short note, e.g. 'Mole on left hand', 'Scar above right eyebrow'. Copy exact printed text. If none is printed, return null."
}
""",

    DocumentType.CERTIFICATE_DEGREE: _BASE + """
Document: Degree Certificate, Provisional Certificate, or Consolidated Marks Memo (University)

Common formats:
  - "Consolidated Marks Memo / Credit Sheet" — has CMM No, Hall Ticket No, CGPA, semester-wise table
  - "Provisional Certificate" — issued at convocation
  - "Degree Certificate" — issued after verification

IMPORTANT extraction rules:
  - degree_roll: Look for "Hall Ticket No" OR "CMM No" OR "Roll No" label.
    e.g. "Hall Ticket No : 12881A04B4" → extract exactly "12881A04B4" (alphanumeric, keep letters).
    Do NOT confuse with CMM No (211038801020) — CMM No is the certificate serial number.
  - degree_year: Look for "Month & Year of Final Exam" or "Year of Passing". Extract 4-digit year only.
    e.g. "April, 2016" → "2016"
  - degree_percentage: Look for "CGPA" label near the bottom. e.g. "CGPA : 7.02" → "7.02 CGPA".
    OR look for "Aggregate Marks" percentage. NOT the marks in words like "THREE NINE TWO ONE".
  - degree_grade: Look for "Class Awarded" label. e.g. "First Class", "First Class with Distinction".
    NOT "THREE NINE TWO ONE" (that is marks in words). NOT a subject grade like A1/A2.
  - degree_marks_identification: Some university memos print identification marks/remarks (less common
    than on SSC/Inter memos). Only extract if explicitly printed as a remark/identification note —
    do NOT confuse with subject names, grades, or seal/signature text.

Extract:
{
  "name":             "Student full name from 'Name :' label. Title case. e.g. Enuganti Prashanth.",
  "father_name":      "Father name if printed. Title case.",
  "degree_name":      "Full degree title from heading. e.g. Bachelor of Technology.",
  "degree_branch":    "Branch/specialization from heading. e.g. Electronics and Communications Engineering.",
  "degree_university":"University name. e.g. Jawaharlal Nehru Technological University Hyderabad.",
  "degree_college":   "College/institution name as printed. e.g. Vardhaman College of Engineering.",
  "degree_roll":      "Hall Ticket Number (alphanumeric). Look for 'Hall Ticket No' label. e.g. 12881A04B4.",
  "degree_year":      "4-digit year from 'Month & Year of Final Exam' or 'Year of Passing'. e.g. 2016.",
  "degree_percentage":"CGPA or percentage from 'CGPA' label at bottom. e.g. 7.02 CGPA or 74.5%.",
  "degree_grade":     "Class from 'Class Awarded' label. e.g. First Class, First Class with Distinction.",
  "degree_marks_identification": "Physical identification marks/remarks if explicitly printed (rare on university memos). If none is printed, return null."
}
""",

    DocumentType.BANK_PASSBOOK: _BASE + """
Document: Bank Passbook, Bank Statement front page, or Cancelled Cheque

Extract:
{
  "account_holder": "Account holder full name. Title case.",
  "account_number": "Bank account number. Digits only.",
  "bank_name":      "Bank name. e.g. State Bank of India, HDFC Bank.",
  "branch":         "Branch name. Title case.",
  "ifsc":           "IFSC code. Exactly 11 characters. Uppercase. Format: AAAA0XXXXXX.",
  "account_type":   "Account type. e.g. Savings, Current.",
  "address":        "Branch address if visible. Single line."
}
""",

    DocumentType.SALARY_SLIP: _BASE + """
Document: Salary Slip / Pay Slip

Extract:
{
  "employee_name":  "Employee full name. Title case.",
  "employee_id":    "Employee ID or staff number.",
  "designation":    "Job title. e.g. Software Engineer.",
  "department":     "Department name.",
  "company":        "Company or employer name.",
  "month_year":     "Salary month and year. e.g. March 2024.",
  "basic_salary":   "Basic salary. Number only, no currency symbol.",
  "gross_salary":   "Gross salary. Number only.",
  "net_salary":     "Net take-home salary. Number only.",
  "pf_number":      "PF or EPF account number if visible."
}
""",
}

# Generic fallback for unrecognised documents
PROMPTS["generic"] = _BASE + """
Document: Unknown Indian government or institutional document

Extract:
{
  "document_title":    "Title or heading of the document.",
  "name":              "Primary person name. Title case.",
  "id_number":         "Any ID, reference, or certificate number.",
  "date":              "Primary date. YYYY-MM-DD.",
  "issuing_authority": "Organisation that issued the document.",
  "address":           "Address if present. Single line."
}
"""

PROMPTS[DocumentType.AUTO] = PROMPTS["generic"]


def get_prompt(document_type) -> str:
    """Return the extraction prompt for a given document type."""
    if hasattr(document_type, "value"):
        key = document_type
    else:
        # String passed — try to match enum
        from backend.app.schemas.documents import DocumentType as DT
        try:
            key = DT(str(document_type))
        except (ValueError, TypeError):
            return PROMPTS["generic"]
    return PROMPTS.get(key, PROMPTS["generic"])