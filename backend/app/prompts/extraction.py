"""
prompts/extraction.py
---------------------
Extraction prompts for the AI Understanding Layer.

Design principles:
  1. One prompt per document type — specific field lists improve accuracy
  2. Output is ALWAYS raw JSON — no markdown, no explanation
  3. Every field has a normalisation instruction — AI returns clean values
  4. Missing fields return null, not empty strings
  5. Confidence is 0-100 based on how clearly the field was found

Adding a new document type = add one entry to DOCUMENT_FIELD_SPECS.
"""

from backend.app.schemas.documents import DocumentType

# ── Field specifications per document type ────────────────────────────────────
# Format: field_name → (description, normalisation_rule)

DOCUMENT_FIELD_SPECS: dict[str, dict[str, tuple[str, str]]] = {

    DocumentType.AADHAAR: {
        "name":           ("Full name of the cardholder",
                           "Title case, e.g. Ravi Kumar"),
        "father_name":    ("Father's or guardian's name (S/O, D/O, W/O)",
                           "Title case"),
        "gender":         ("Gender",
                           "Exactly one of: Male, Female, Other"),
        "dob":            ("Date of birth",
                           "YYYY-MM-DD format"),
        "year_of_birth":  ("Year of birth if full DOB not visible",
                           "4-digit year, e.g. 1990"),
        "aadhaar_number": ("12-digit Aadhaar number",
                           "Digits only, no spaces, exactly 12 chars"),
        "mobile":         ("Mobile number if printed",
                           "10 digits, no country code"),
        "address":        ("Full address if on back of card",
                           "Single line, comma-separated"),
    },

    DocumentType.PAN: {
        "name":        ("Full name of the PAN cardholder",
                        "Title case"),
        "father_name": ("Father's name",
                        "Title case"),
        "dob":         ("Date of birth",
                        "YYYY-MM-DD format"),
        "pan_number":  ("PAN number",
                        "Uppercase, format AAAAA0000A exactly 10 chars"),
    },

    DocumentType.PASSPORT: {
        "name":            ("Full name as printed",
                            "Title case, surname first if MRZ format"),
        "passport_number": ("Passport number",
                            "Uppercase alphanumeric"),
        "nationality":     ("Nationality",
                            "e.g. Indian"),
        "dob":             ("Date of birth",
                            "YYYY-MM-DD"),
        "doi":             ("Date of issue",
                            "YYYY-MM-DD"),
        "doe":             ("Date of expiry",
                            "YYYY-MM-DD"),
        "place_of_birth":  ("Place of birth",
                            "City, State"),
        "gender":          ("Gender",
                            "Male, Female, or Other"),
        "file_number":     ("File number",
                            "Alphanumeric"),
    },

    DocumentType.DRIVING_LICENSE: {
        "name":      ("Full name",
                      "Title case"),
        "dob":       ("Date of birth",
                      "YYYY-MM-DD"),
        "doi":       ("Date of issue",
                      "YYYY-MM-DD"),
        "doe":       ("Date of expiry",
                      "YYYY-MM-DD"),
        "dl_number": ("Driving licence number",
                      "Uppercase, format varies by state"),
        "address":   ("Address",
                      "Single line"),
        "vehicle_classes": ("Authorised vehicle classes",
                            "Comma-separated, e.g. LMV, MCWG"),
    },

    DocumentType.VOTER_ID: {
        "name":          ("Voter's full name",
                          "Title case"),
        "father_name":   ("Father's or husband's name",
                          "Title case"),
        "gender":        ("Gender",
                          "Male, Female, or Other"),
        "dob":           ("Date of birth or age",
                          "YYYY-MM-DD if full date, else age as string"),
        "voter_id":      ("Electoral photo ID card number (EPIC number)",
                          "Uppercase alphanumeric"),
        "address":       ("Residential address",
                          "Single line"),
        "constituency":  ("Assembly or parliamentary constituency",
                          "As printed"),
    },

    DocumentType.CERTIFICATE_SSC: {
        "name":          ("Student name", "Title case"),
        "father_name":   ("Father name", "Title case"),
        "mother_name":   ("Mother name", "Title case"),
        "dob":           ("Date of birth", "YYYY-MM-DD"),
        "roll_number":   ("Exam roll number", "As printed"),
        "hall_ticket":   ("Hall ticket number", "As printed"),
        "school":        ("School name", "As printed"),
        "board":         ("Board of education", "e.g. BSEAP, CBSE, ICSE"),
        "year":          ("Year of passing", "4-digit year"),
        "percentage":    ("Overall percentage", "Decimal, e.g. 85.6"),
        "grade":         ("Grade or division", "e.g. A+, First Class"),
    },

    DocumentType.CERTIFICATE_DEGREE: {
        "name":          ("Student name", "Title case"),
        "father_name":   ("Father name", "Title case"),
        "degree":        ("Degree name", "e.g. Bachelor of Technology"),
        "specialization":("Branch or specialization", "e.g. Computer Science"),
        "university":    ("University name", "As printed"),
        "college":       ("College name", "As printed"),
        "year":          ("Year of passing", "4-digit year"),
        "roll_number":   ("Roll or registration number", "As printed"),
        "percentage":    ("Percentage or CGPA", "e.g. 78.5 or 8.2 CGPA"),
        "grade":         ("Class or grade", "e.g. First Class with Distinction"),
    },

    DocumentType.BANK_PASSBOOK: {
        "account_holder": ("Account holder name", "Title case"),
        "account_number": ("Bank account number", "Digits only"),
        "bank_name":      ("Bank name", "e.g. State Bank of India"),
        "branch":         ("Branch name", "As printed"),
        "ifsc":           ("IFSC code", "Uppercase, 11 chars"),
        "account_type":   ("Type of account", "e.g. Savings, Current"),
        "address":        ("Branch address", "Single line"),
    },

    DocumentType.SALARY_SLIP: {
        "employee_name":  ("Employee full name", "Title case"),
        "employee_id":    ("Employee ID or code", "As printed"),
        "designation":    ("Job designation", "As printed"),
        "department":     ("Department", "As printed"),
        "company":        ("Company or employer name", "As printed"),
        "month_year":     ("Salary month and year", "e.g. March 2024"),
        "basic_salary":   ("Basic salary amount", "Number only, no currency symbol"),
        "gross_salary":   ("Gross salary", "Number only"),
        "net_salary":     ("Net take-home salary", "Number only"),
        "pf_number":      ("PF or EPF account number", "As printed"),
    },

    # Generic fallback for unrecognised documents
    "generic": {
        "document_title": ("Title or heading of the document", "As printed"),
        "name":           ("Primary person or entity name", "Title case"),
        "id_number":      ("Any ID, reference, or certificate number", "As printed"),
        "date":           ("Primary date on the document", "YYYY-MM-DD"),
        "issuing_authority": ("Organisation or authority that issued it", "As printed"),
        "address":        ("Address if present", "Single line"),
    },
}


def get_extraction_prompt(document_type: str, ocr_text: str) -> str:
    """
    Build the extraction prompt for a given document type.

    Args:
        document_type: DocumentType string value
        ocr_text:      Raw OCR text from Tesseract or Surya

    Returns:
        Complete prompt string ready to send to Haiku.
    """
    fields = DOCUMENT_FIELD_SPECS.get(
        document_type,
        DOCUMENT_FIELD_SPECS["generic"]
    )

    field_lines = "\n".join(
        f'  "{name}": "{desc} | Normalise: {rule}"'
        for name, (desc, rule) in fields.items()
    )

    doc_label = document_type.replace("_", " ").title()

    return f"""You are a document data extraction specialist for Indian documents.

Document type: {doc_label}

OCR Text extracted from the document:
---
{ocr_text}
---

Extract the following fields from the OCR text above.

Fields to extract:
{{{field_lines}
}}

Rules:
1. Return ONLY a valid JSON object. No explanation, no markdown, no code blocks.
2. For each field, return an object with "value" and "confidence" keys.
3. "value": the extracted string, or null if not found.
4. "confidence": integer 0-100 indicating how clearly the field was found.
   - 95-100: field label and value clearly visible
   - 80-94:  value found but label unclear or slightly garbled
   - 60-79:  inferred from context
   - 40-59:  uncertain, needs review
   - 0-39:   guessed
5. Apply the normalisation rule for each field exactly.
6. Never invent values. If not found, return null.

Example output format:
{{
  "name": {{"value": "Ravi Kumar", "confidence": 95}},
  "dob": {{"value": "1990-05-15", "confidence": 88}},
  "pan_number": {{"value": null, "confidence": 0}}
}}

Return the JSON now:"""
