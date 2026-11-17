from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


# =============================================================================
# CONFIGURATION
# =============================================================================

PROCEDURE_REFERENCE_DEFAULT = "C1252-TAC-0274"
PROCEDURE_TITLE_DEFAULT = "Trackside ATC Low-Speed Dynamic Test Procedure"

MAX_PDF_SIZE_MB = 80
MAX_SOURCE_EVIDENCE_LENGTH = 1800
MAX_CHECKLIST_ITEMS = 18


EXTRACTED_EVENT_COLUMNS = [
    "row_number",
    "sector",
    "section",
    "subsection",
    "branch",
    "direction",
    "abscissa_m",
    "inter_singularity_m",
    "code",
    "event_name",
    "location_name",
    "participate_variant",
    "anticipate_rank",
    "source_page",
    "source_evidence",
    "remarks",
]


TEST_LIBRARY_COLUMNS = [
    "test_code",
    "test_id",
    "test_name",
    "short_name",
    "needs_abscissa",
    "checklist",
]


REGISTER_COLUMNS = [
    "test_code",
    "test_id",
    "test_name",
    "needs_abscissa",
    "sector",
    "section",
    "subsection",
    "branch",
    "direction",
    "abscissa_m",
    "participate_variant",
    "anticipate_variant",
    "anticipate_rank",
    "anticipate_distance_m",
    "anticipate_abscissa_m",
    "event_name",
    "location_name",
    "source_page",
    "source_evidence",
    "review_status",
    "remarks",
]


# =============================================================================
# BASIC HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    """Safely return stripped text."""
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def normalise_spaces(value: Any) -> str:
    """Normalise repeated whitespace."""
    return re.sub(r"\s+", " ", safe_text(value)).strip()


def clean_filename(value: str) -> str:
    """Create a safe filename."""
    value = safe_text(value)
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", "_", value)

    return value[:150] or "ATC_Test_Sheet"


def file_hash(file_bytes: bytes) -> str:
    """Create stable file hash for session tracking."""
    return hashlib.sha256(file_bytes).hexdigest()


def to_float(value: Any) -> Optional[float]:
    """Convert number-like values to float."""
    text = safe_text(value)

    if not text:
        return None

    text = (
        text.replace(",", "")
        .replace("m", "")
        .replace("M", "")
        .strip()
    )

    try:
        return float(text)
    except ValueError:
        return None


def to_bool(value: Any) -> bool:
    """Convert spreadsheet values to bool."""
    return safe_text(value).upper() in {
        "Y",
        "YES",
        "TRUE",
        "T",
        "1",
        "REQUIRED",
    }


def normalise_branch(value: Any) -> str:
    """
    Normalise branch strings.

    Examples:
        14-04-1-0       -> 14-04-1-0
        14 04 1 0       -> 14-04-1-0
        14 - 4 - 1 - 0  -> 14-04-1-0
    """
    text = safe_text(value)

    match = re.search(
        r"(\d{1,2})\s*[- ]\s*"
        r"(\d{1,2})\s*[- ]\s*"
        r"(\d{1,2})\s*[- ]\s*"
        r"(\d{1,3})",
        text,
    )

    if not match:
        return text

    sector, section, subsection, branch = match.groups()

    return (
        f"{int(sector):02d}-"
        f"{int(section):02d}-"
        f"{int(subsection)}-"
        f"{int(branch)}"
    )


def branch_parts(branch: str) -> Tuple[str, str, str]:
    """Return sector, section and subsection from branch."""
    parts = safe_text(branch).split("-")

    sector = parts[0] if len(parts) >= 1 else ""
    section = parts[1] if len(parts) >= 2 else ""
    subsection = "-".join(parts[:3]) if len(parts) >= 3 else ""

    return sector, section, subsection


def format_distance(value: Any) -> str:
    """Format a distance in metres."""
    numeric = to_float(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


def split_checklist(value: str) -> List[str]:
    """
    Split pipe-separated checklist items.

    Example:
        Confirm RM mode|Record beacon code|Confirm Total Loc
    """
    items = [
        normalise_spaces(item)
        for item in safe_text(value).split("|")
        if normalise_spaces(item)
    ]

    return items[:MAX_CHECKLIST_ITEMS]


# =============================================================================
# PDF TEXT EXTRACTION
#
# Optimised: uses pypdf selectable text only.
# It does NOT use pdfplumber table extraction and does NOT render PDF pages.
# =============================================================================

@st.cache_data(show_spinner=False, max_entries=4)
def extract_pdf_pages_cached(
    pdf_bytes: bytes,
) -> List[str]:
    """
    Extract selectable text from every PDF page.

    Cached by Streamlit. The same uploaded PDF will not be parsed again
    after normal Streamlit reruns.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'pypdf'. "
            "Add pypdf>=4.0.0 to requirements.txt."
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(
                "The PDF is encrypted and cannot be read."
            ) from exc

    return [
        page.extract_text() or ""
        for page in reader.pages
    ]


def extract_page_metadata(page_text: str) -> Dict[str, str]:
    """
    Extract general page metadata.

    This works for different station PDFs because sector and section are
    read from page text.
    """
    text = normalise_spaces(page_text)

    line_match = re.search(
        r"\bLINE\s*:\s*([A-Z0-9 _/-]+?)(?=\s+SECTOR|\s+SECTION|$)",
        text,
        flags=re.IGNORECASE,
    )

    sector_match = re.search(
        r"\bSECTOR\s*:\s*(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )

    section_match = re.search(
        r"\bSECTION\s*:\s*(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )

    direction = ""

    if re.search(r"\bUP\s+TRACK\b", text, re.IGNORECASE):
        direction = "Up"

    elif re.search(r"\bDN\s+TRACK\b", text, re.IGNORECASE):
        direction = "Down"

    elif re.search(r"\bDOWN\s+TRACK\b", text, re.IGNORECASE):
        direction = "Down"

    return {
        "line": safe_text(line_match.group(1)) if line_match else "",
        "sector": (
            f"{int(sector_match.group(1)):02d}"
            if sector_match
            else ""
        ),
        "section": (
            f"{int(section_match.group(1)):02d}"
            if section_match
            else ""
        ),
        "direction": direction,
    }


def looks_like_subdivision_page(page_text: str) -> bool:
    """
    Identify subdivision singularity-table pages.

    This is format detection, not station-specific logic.
    """
    upper = page_text.upper()

    markers = [
        "SINGULARITIES",
        "TEST ABSC",
        "INTER SINGUL",
        "SUB SECTION",
        "SUB-SECTION",
        "UP TRACK NORMAL DIRECTION",
        "DN TRACK NORMAL DIRECTION",
        "DOWN TRACK NORMAL DIRECTION",
    ]

    return any(marker in upper for marker in markers)


def extract_associated_variant(text: str) -> str:
    """Extract associated/participating variant text."""
    match = re.search(
        r"(?:ASSOCIATED|PARTICIPATE|PARTICIPATING)\s+VARIANTS?"
        r"(?:\s+NUMBER)?\s*[:=]?\s*"
        r"\(?([^()\n;]+?)\)?"
        r"(?=$|;|,?\s+(?:RANK|NUMBER|ANTICIPATE|SUB\s*SECTION|"
        r"NORMAL\s+BRANCH))",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    if match:
        return normalise_spaces(match.group(1))

    return ""


def extract_anticipate_rank(text: str) -> str:
    """Extract rank of first anticipated variant."""
    match = re.search(
        r"RANK\s+OF\s+FIRST\s+ANTICIPATED\s+VARIANT\s*:\s*(\d+)",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    return match.group(1) if match else ""


def extract_location_name(text: str) -> str:
    """
    Extract useful location labels.

    The entire event/remarks source text remains available separately.
    """
    patterns = [
        r"(?:RETURN\s+CHANNEL)\s*(?:NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:SIGNAL|SIG)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:TRACK\s*CIRCUIT|T/C)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:POINT|TURNOUT)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:PSR)\s*([0-9]+(?:\s*KPH)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, safe_text(text), flags=re.IGNORECASE)

        if match:
            return normalise_spaces(match.group(0))

    return ""


def infer_event_name(source_text: str) -> str:
    """
    Produce a descriptive event label from source text.

    This is not a test mapping. It only helps users review rows.
    """
    text = safe_text(source_text)
    upper = text.upper()

    labels = [
        ("SPACING STOPPING POINT", "Spacing Stopping Point"),
        ("SPECIAL STOPPING POINT", "Special Stopping Point"),
        ("PERMANENT SPEED RESTRICTION", "Permanent Speed Restriction"),
        ("RETURN CHANNEL", "Return Channel"),
        ("MOVING TRAIN INITIALISATION", "Moving Train Initialisation"),
        ("MOVING TRAIN INITIALIZATION", "Moving Train Initialisation"),
        ("STATIONARY TRAIN INITIALISATION", "Stationary Train Initialisation"),
        ("STATIONARY TRAIN INITIALIZATION", "Stationary Train Initialisation"),
        ("EMERGENCY ZONE", "Emergency Zone"),
        ("CHANGE OF END", "Change of End"),
        ("CONVERGENCE", "Convergence"),
        ("DIVERGENCE", "Divergence"),
        ("INTERMEDIATE LINK", "Intermediate Link"),
        ("CHAINED END", "Chained End"),
        ("SIMPLE END", "Simple End"),
        ("END OF SACEM", "End of SACEM"),
        ("BEACON TYPE", "Beacon"),
        ("BEACON", "Beacon"),
    ]

    for phrase, label in labels:
        if phrase in upper:
            return label

    # Do not make test applicability assumptions.
    return "Subdivision Event"


def extract_branch_from_source(
    source_text: str,
) -> str:
    """
    Extract the last branch/reference-like value from a source line.

    Supports:
        14-04-1-0
        14 04 1 0
    """
    hyphen_matches = re.findall(
        r"\b\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,3}\b",
        source_text,
    )

    if hyphen_matches:
        return normalise_branch(hyphen_matches[-1])

    spaced_matches = re.findall(
        r"\b\d{1,2}\s+\d{1,2}\s+\d{1,2}\s+\d{1,3}\b",
        source_text,
    )

    if spaced_matches:
        return normalise_branch(spaced_matches[-1])

    return ""


def parse_subdivision_page(
    page_text: str,
    page_number: int,
) -> List[Dict[str, Any]]:
    """
    Parse selectable text from a subdivision page.

    The source PDF table format is common across stations:
      N | Test Abs. | Inter singularity | KP | Code | Remarks | Name | ...

    Parsing does not depend on station number, section number or branch.
    """
    metadata = extract_page_metadata(page_text)

    lines = [
        normalise_spaces(line)
        for line in page_text.splitlines()
        if normalise_spaces(line)
    ]

    output: List[Dict[str, Any]] = []

    current_subsection = ""
    current_branch = ""

    row_pattern = re.compile(
        r"^\s*(\d{1,3})\s+"
        r"(?:!\s+)?"
        r"(?:(-?\d{1,7}(?:[.,]\d{1,3})?)\s*m\s+)?"
        r"(-?\d{1,7}(?:[.,]\d{1,3})?)\s+"
        r"(\d+(?:_\d+)?)\s+"
        r"(.*)$",
        flags=re.IGNORECASE,
    )

    subsection_pattern = re.compile(
        r"SUB\s*-?\s*SECTION\s*:\s*"
        r"(\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2})",
        flags=re.IGNORECASE,
    )

    for line_index, line in enumerate(lines):
        subsection_match = subsection_pattern.search(line)

        if subsection_match:
            current_subsection = (
                subsection_match.group(1)
                .replace(" ", "")
            )

        row_match = row_pattern.match(line)

        if not row_match:
            continue

        row_number = safe_text(row_match.group(1))
        inter_singularity = to_float(row_match.group(2))
        abscissa = to_float(row_match.group(3))
        code = safe_text(row_match.group(4))
        remaining_text = normalise_spaces(row_match.group(5))

        if abscissa is None:
            continue

        # Exclude obvious page labels, element numbers and corrupted records.
        if abscissa < -10000 or abscissa > 100000:
            continue

        source_context = " ".join(
            lines[
                max(0, line_index - 1):
                min(len(lines), line_index + 2)
            ]
        )

        combined_text = normalise_spaces(
            f"{line} {source_context}"
        )

        branch = extract_branch_from_source(combined_text)

        if not branch and current_subsection:
            branch = f"{current_subsection}-0"

        sector, section, subsection = branch_parts(branch)

        if not sector:
            sector = metadata["sector"]

        if not section:
            section = metadata["section"]

        if not subsection:
            subsection = current_subsection

        is_subsection_heading = (
            "SUB SECTION" in combined_text.upper()
            and "HEADING" in combined_text.upper()
        )

        if is_subsection_heading:
            continue

        event_name = infer_event_name(combined_text)

        output.append(
            {
                "row_number": row_number,
                "sector": sector,
                "section": section,
                "subsection": subsection,
                "branch": branch,
                "direction": metadata["direction"],
                "abscissa_m": abscissa,
                "inter_singularity_m": inter_singularity,
                "code": code,
                "event_name": event_name,
                "location_name": extract_location_name(combined_text),
                "participate_variant": extract_associated_variant(
                    combined_text
                ),
                "anticipate_rank": extract_anticipate_rank(combined_text),
                "source_page": page_number,
                "source_evidence": combined_text[
                    :MAX_SOURCE_EVIDENCE_LENGTH
                ],
                "remarks": remaining_text[
                    :MAX_SOURCE_EVIDENCE_LENGTH
                ],
            }
        )

    return output


def build_events_from_pages(
    page_texts: List[str],
    progress_bar: Any,
    progress_message: Any,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Build extracted source-event register from cached PDF page text.

    Page text extraction is cached separately. This parsing stage is light.
    """
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []

    total_pages = len(page_texts)

    for page_number, page_text in enumerate(page_texts, start=1):
        progress_message.info(
            f"Analysing page {page_number} of {total_pages}..."
        )

        progress_bar.progress(
            int(page_number / total_pages * 100)
        )

        if not looks_like_subdivision_page(page_text):
            continue

        try:
            rows.extend(
                parse_subdivision_page(
                    page_text=page_text,
                    page_number=page_number,
                )
            )
        except Exception as exc:
            warnings.append(
                f"Page {page_number} was skipped during parsing: {exc}"
            )

    progress_bar.progress(100)

    if not rows:
        return (
            pd.DataFrame(columns=EXTRACTED_EVENT_COLUMNS),
            warnings,
        )

    dataframe = pd.DataFrame(rows)

    for column in EXTRACTED_EVENT_COLUMNS:
        if column not in dataframe.columns:
            dataframe[column] = ""

    dataframe["abscissa_m"] = pd.to_numeric(
        dataframe["abscissa_m"],
        errors="coerce",
    )

    dataframe = dataframe.dropna(
        subset=["abscissa_m"]
    ).copy()

    dataframe = dataframe.drop_duplicates(
        subset=[
            "source_page",
            "row_number",
            "abscissa_m",
            "source_evidence",
        ]
    )

    dataframe = dataframe[
        EXTRACTED_EVENT_COLUMNS
    ].sort_values(
        by=["sector", "section", "branch", "abscissa_m"],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe, warnings


# =============================================================================
# PROCEDURE DOCX EXTRACTION
#
# The uploaded procedure defines test codes, test IDs, names and checklist.
# There is no LS01-LS14 Python dictionary.
# =============================================================================

@st.cache_data(show_spinner=False, max_entries=4)
def extract_procedure_library_cached(
    docx_bytes: bytes,
) -> pd.DataFrame:
    """
    Extract available ATC-TNC-TRA-LSxx tests from a procedure DOCX.

    Test definitions are read from the uploaded procedure.
    """
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'python-docx'. "
            "Add python-docx>=1.1.0 to requirements.txt."
        ) from exc

    document = Document(io.BytesIO(docx_bytes))

    paragraphs = [
        normalise_spaces(paragraph.text)
        for paragraph in document.paragraphs
        if normalise_spaces(paragraph.text)
    ]

    rows: List[Dict[str, Any]] = []

    test_pattern = re.compile(
        r"(ATC\s*[- ]?\s*TNC\s*[- ]?\s*TRA\s*[- ]?"
        r"LS\s*(\d{2}))",
        flags=re.IGNORECASE,
    )

    for index, text in enumerate(paragraphs):
        matches = list(test_pattern.finditer(text))

        if not matches:
            continue

        for match in matches:
            raw_test_id = match.group(1)
            test_code = f"LS{match.group(2)}"

            test_id = re.sub(
                r"\s+",
                "-",
                raw_test_id.upper(),
            )

            test_id = test_id.replace("--", "-")

            test_name = re.sub(
                r"\(?\s*ID\s*:?\s*"
                r"ATC\s*[- ]?\s*TNC\s*[- ]?\s*TRA\s*[- ]?"
                r"LS\s*\d{2}\s*\)?",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip(" -–:")

            if len(test_name) < 4 and index > 0:
                test_name = paragraphs[index - 1]

            rows.append(
                {
                    "test_code": test_code,
                    "test_id": test_id,
                    "test_name": test_name,
                    "short_name": test_name,
                    "needs_abscissa": False,
                    "checklist": "",
                }
            )

    if not rows:
        return pd.DataFrame(columns=TEST_LIBRARY_COLUMNS)

    library = pd.DataFrame(rows)

    library = library.drop_duplicates(
        subset=["test_code"]
    ).sort_values(
        by="test_code"
    ).reset_index(drop=True)

    # Extract procedure text blocks between one test heading and next test heading.
    for row_index in library.index:
        test_code = safe_text(library.at[row_index, "test_code"])

        start_index = None

        for index, text in enumerate(paragraphs):
            if test_code.upper() in text.upper():
                start_index = index
                break

        if start_index is None:
            continue

        block: List[str] = []

        for text in paragraphs[start_index + 1:]:
            other_match = test_pattern.search(text)

            if other_match:
                other_code = f"LS{other_match.group(2)}"

                if other_code != test_code:
                    break

            if len(text) >= 8:
                block.append(text)

            if len(block) >= MAX_CHECKLIST_ITEMS:
                break

        # Stored pipe separated. User may edit this in UI.
        checklist_items = [
            item
            for item in block
            if not item.upper().startswith("PURPOSE")
            and not item.upper().startswith("ACCEPTANCE")
        ]

        library.at[row_index, "checklist"] = "|".join(
            checklist_items[:MAX_CHECKLIST_ITEMS]
        )

    for column in TEST_LIBRARY_COLUMNS:
        if column not in library.columns:
            library[column] = ""

    return library[TEST_LIBRARY_COLUMNS]


def apply_test_library_to_register(
    register: pd.DataFrame,
    test_library: pd.DataFrame,
) -> pd.DataFrame:
    """
    Update Test ID, Name and Abscissa Requirement after user selects test code.
    """
    output = register.copy()

    lookup = (
        test_library
        .fillna("")
        .set_index("test_code")
        .to_dict("index")
    )

    for index in output.index:
        test_code = safe_text(output.at[index, "test_code"])

        if test_code not in lookup:
            output.at[index, "test_id"] = ""
            output.at[index, "test_name"] = ""
            output.at[index, "needs_abscissa"] = False
            continue

        details = lookup[test_code]

        output.at[index, "test_id"] = safe_text(
            details.get("test_id")
        )

        output.at[index, "test_name"] = safe_text(
            details.get("test_name")
        )

        output.at[index, "needs_abscissa"] = bool(
            details.get("needs_abscissa")
        )

    return output


# =============================================================================
# REGISTER CREATION
#
# No automatic test assignment. The engineer selects the test from procedure.
# =============================================================================

def create_draft_register(
    extracted_events: pd.DataFrame,
    start_kp: float,
    end_kp: float,
    selected_branch: str,
) -> pd.DataFrame:
    """
    Create source-event review register for the selected KP range.

    Applicable Test is intentionally blank.
    Engineering user selects applicable test code after review.
    """
    lower_kp = min(start_kp, end_kp)
    upper_kp = max(start_kp, end_kp)

    selected = extracted_events[
        extracted_events["abscissa_m"].between(
            lower_kp,
            upper_kp,
            inclusive="both",
        )
    ].copy()

    if selected_branch != "All branches":
        selected = selected[
            selected["branch"].eq(selected_branch)
        ].copy()

    rows: List[Dict[str, Any]] = []

    for _, event in selected.iterrows():
        rows.append(
            {
                "test_code": "",
                "test_id": "",
                "test_name": "",
                "needs_abscissa": False,
                "sector": safe_text(event.get("sector")),
                "section": safe_text(event.get("section")),
                "subsection": safe_text(event.get("subsection")),
                "branch": safe_text(event.get("branch")),
                "direction": safe_text(event.get("direction")),
                "abscissa_m": event.get("abscissa_m"),
                "participate_variant": safe_text(
                    event.get("participate_variant")
                ),
                "anticipate_variant": "",
                "anticipate_rank": safe_text(
                    event.get("anticipate_rank")
                ),
                "anticipate_distance_m": None,
                "anticipate_abscissa_m": None,
                "event_name": safe_text(event.get("event_name")),
                "location_name": safe_text(
                    event.get("location_name")
                ),
                "source_page": safe_text(event.get("source_page")),
                "source_evidence": safe_text(
                    event.get("source_evidence")
                ),
                "review_status": "To be reviewed",
                "remarks": (
                    "Select applicable test from procedure. Verify branch, "
                    "abscissa, participating variant, anticipated variant "
                    "and project test scope."
                ),
            }
        )

    return pd.DataFrame(rows, columns=REGISTER_COLUMNS)


# =============================================================================
# DOCX TEST SHEET GENERATION
# =============================================================================

def set_cell_text(
    cell: Any,
    text: Any,
    bold: bool = False,
) -> None:
    """Set DOCX table cell text."""
    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(text))
    run.bold = bold


def add_docx_row(
    table: Any,
    values: List[Any],
    bold: bool = False,
) -> None:
    """Add row to DOCX table."""
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(cells[index], value, bold)


def get_checklist_for_test(
    test_code: str,
    test_library: pd.DataFrame,
) -> List[str]:
    """Get checklist text from the user-uploaded procedure DOCX."""
    selected = test_library[
        test_library["test_code"].astype(str).eq(test_code)
    ]

    if selected.empty:
        return []

    checklist_text = safe_text(selected.iloc[0].get("checklist"))

    return split_checklist(checklist_text)


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    test_library: pd.DataFrame,
    sheet_number: str,
) -> bytes:
    """
    Create a generic Appendix-A-style DOCX test sheet.

    Test name and checklist are taken from user-uploaded procedure DOCX.
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency python-docx."
        ) from exc

    test_code = safe_text(row.get("test_code"))
    test_name = safe_text(row.get("test_name"))
    test_id = safe_text(row.get("test_id"))

    checklist = get_checklist_for_test(
        test_code=test_code,
        test_library=test_library,
    )

    if not checklist:
        checklist = [
            "Confirm approved engineering source and project scope.",
            "Confirm participating variant condition.",
            "Record OMAP / wayside / log evidence.",
            "Confirm acceptance criterion in approved procedure.",
        ]

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.40)
    section.right_margin = Inches(0.40)

    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(8)

    header = document.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER

    logo = header.add_run("ALSTOM\n")
    logo.bold = True
    logo.font.size = Pt(16)

    title_heading = header.add_run(
        project_info["procedure_title"]
    )
    title_heading.bold = True
    title_heading.font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{test_name} ({test_id})"
    )
    title_run.bold = True
    title_run.font.size = Pt(12)

    document.add_paragraph()

    info_table = document.add_table(rows=0, cols=6)
    info_table.style = "Table Grid"

    add_docx_row(
        info_table,
        [
            "Procedure Reference:",
            project_info["procedure_reference"],
            "Procedure Title:",
            project_info["procedure_title"],
            "Test Sheet No:",
            sheet_number,
        ],
    )

    add_docx_row(
        info_table,
        [
            "Trackside Version:",
            project_info["trackside_version"],
            "ATP Version:",
            project_info["atp_version"],
            "ATO Version:",
            project_info["ato_version"],
        ],
    )

    document.add_paragraph()

    location_table = document.add_table(rows=0, cols=4)
    location_table.style = "Table Grid"

    add_docx_row(
        location_table,
        [
            "Sector:",
            safe_text(row.get("sector")),
            "Branch Number:",
            safe_text(row.get("branch")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Section / Subsection:",
            (
                f"{safe_text(row.get('section'))} / "
                f"{safe_text(row.get('subsection'))}"
            ),
            "Direction:",
            safe_text(row.get("direction")),
        ],
    )

    needs_abscissa = bool(row.get("needs_abscissa"))

    add_docx_row(
        location_table,
        [
            "Abscissa:",
            (
                format_distance(row.get("abscissa_m"))
                if needs_abscissa
                else "Not required / verify procedure"
            ),
            "Location / Asset:",
            safe_text(row.get("location_name")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Participate Variant:",
            safe_text(row.get("participate_variant")),
            "Anticipate Variant:",
            safe_text(row.get("anticipate_variant")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Anticipate Rank:",
            safe_text(row.get("anticipate_rank")),
            "Anticipate Abscissa:",
            (
                format_distance(row.get("anticipate_abscissa_m"))
                or "To be reviewed"
            ),
        ],
    )

    document.add_paragraph()

    record_table = document.add_table(rows=1, cols=4)
    record_table.style = "Table Grid"

    headers = [
        "Check / Test Action",
        "Expected Requirement",
        "Actual Result / Record",
        "OK?",
    ]

    for index, header_text in enumerate(headers):
        set_cell_text(
            record_table.rows[0].cells[index],
            header_text,
            bold=True,
        )

    for checklist_item in checklist:
        add_docx_row(
            record_table,
            [
                checklist_item,
                "As required by approved procedure/design.",
                "",
                "☐ Y   ☐ N   ☐ N/A",
            ],
        )

    document.add_paragraph()

    notes_heading = document.add_paragraph()
    notes_heading.add_run(
        "Comments / Engineering Remarks"
    ).bold = True

    notes_table = document.add_table(rows=1, cols=1)
    notes_table.style = "Table Grid"

    source_notes = (
        f"Source Page: {safe_text(row.get('source_page'))}\n"
        f"Source Event: {safe_text(row.get('event_name'))}\n"
        f"Source Evidence: {safe_text(row.get('source_evidence'))}\n\n"
        f"Engineering Remarks: {safe_text(row.get('remarks'))}"
    )

    set_cell_text(notes_table.rows[0].cells[0], source_notes)

    document.add_paragraph()

    approval_table = document.add_table(rows=0, cols=3)
    approval_table.style = "Table Grid"

    add_docx_row(
        approval_table,
        [
            "The test covered by this Test Sheet is considered:",
            "☐ Passed",
            "☐ Failed",
        ],
    )

    add_docx_row(approval_table, ["", "Alstom", "MTR"])
    add_docx_row(approval_table, ["Name:", "", ""])
    add_docx_row(approval_table, ["Signature:", "", ""])
    add_docx_row(approval_table, ["Date:", "", ""])

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – "
        "Verify approved project-specific data before use."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(7)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_docx_zip(
    register: pd.DataFrame,
    project_info: Dict[str, str],
    test_library: pd.DataFrame,
    progress_bar: Any,
    progress_message: Any,
) -> bytes:
    """Generate ZIP containing DOCX test sheets."""
    output = io.BytesIO()
    total = len(register)

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for sequence, (_, row) in enumerate(
            register.iterrows(),
            start=1,
        ):
            test_code = safe_text(row.get("test_code"))
            branch = safe_text(row.get("branch")) or "NO_BRANCH"

            progress_message.info(
                f"Generating DOCX test sheet {sequence} of {total}: "
                f"{test_code} / {branch}"
            )

            sheet_number = (
                f"{project_info['sheet_prefix']}-{sequence:03d}"
            )

            filename = clean_filename(
                f"{sheet_number}_{test_code}_{branch}_"
                f"KP_{safe_text(row.get('abscissa_m'))}.docx"
            )

            archive.writestr(
                filename,
                create_docx_test_sheet(
                    row=row,
                    project_info=project_info,
                    test_library=test_library,
                    sheet_number=sheet_number,
                ),
            )

            progress_bar.progress(
                int(sequence / total * 100)
            )

    progress_bar.progress(100)

    progress_message.success(
        f"DOCX generation complete: {total} test sheet(s)."
    )

    return output.getvalue()


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    events: pd.DataFrame,
    test_library: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
    """Create register workbook."""
    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        cover = pd.DataFrame(
            [
                ["Procedure Reference", project_info["procedure_reference"]],
                ["Procedure Title", project_info["procedure_title"]],
                ["Project / Contract", project_info["project"]],
                ["Trackside Version", project_info["trackside_version"]],
                ["ATP Version", project_info["atp_version"]],
                ["ATO Version", project_info["ato_version"]],
                ["Generated Date", str(date.today())],
                ["Generated Register Rows", len(register)],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(writer, sheet_name="Cover", index=False)
        register.to_excel(writer, sheet_name="Test Register", index=False)
        events.to_excel(
            writer,
            sheet_name="Extracted Events",
            index=False,
        )
        test_library.to_excel(
            writer,
            sheet_name="Procedure Test Library",
            index=False,
        )

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                maximum_length = max(
                    len(safe_text(cell.value))
                    for cell in column_cells
                )

                worksheet.column_dimensions[
                    column_cells[0].column_letter
                ].width = min(
                    max(maximum_length + 2, 12),
                    70,
                )

    return output.getvalue()


# =============================================================================
# UI STYLES
# =============================================================================

def render_styles() -> None:
    st.markdown(
        """
        <style>
        .generator-hero {
            background: linear-gradient(135deg, #102A43, #164E72);
            color: white;
            padding: 22px 28px;
            border-radius: 14px;
            margin-bottom: 16px;
        }

        .generator-hero h1 {
            color: white !important;
            margin: 0 !important;
        }

        .generator-hero p {
            color: #EAF4FB !important;
            margin: 8px 0 0 0 !important;
        }

        .generator-warning {
            background: #FFF7E8;
            border-left: 5px solid #E37400;
            border-radius: 8px;
            padding: 14px 16px;
            margin: 10px 0 18px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# MAIN STREAMLIT TOOL
# =============================================================================

def render_test_sheet_generator_app() -> None:
    """
    Called by app.py.

    Do not call st.set_page_config() here.
    """
    render_styles()

    st.markdown(
        """
        <div class="generator-hero">
            <h1>ATC Low-Speed Dynamic Test Sheet Generator</h1>
            <p>
                Upload a station SACEM Subdivision PDF and the applicable
                Low-Speed Test Procedure DOCX. Review source data, select
                applicable tests and generate DOCX test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="generator-warning">
            <b>Engineering control:</b><br>
            This tool extracts draft information only. Test applicability,
            abscissa, branch, participating variant, anticipated variant,
            anticipated distance and acceptance criteria must be reviewed
            against approved project documents before issue.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # Project information
    # -------------------------------------------------------------------------
    with st.expander("Project Information", expanded=True):
        project_col1, project_col2, project_col3 = st.columns(3)

        with project_col1:
            project = st.text_input(
                "Project / Contract",
                key="generator_project",
            )

            trackside_version = st.text_input(
                "Trackside Version",
                key="generator_trackside_version",
            )

        with project_col2:
            atp_version = st.text_input(
                "ATP Version",
                key="generator_atp_version",
            )

            ato_version = st.text_input(
                "ATO Version",
                key="generator_ato_version",
            )

        with project_col3:
            procedure_reference = st.text_input(
                "Procedure Reference",
                value=PROCEDURE_REFERENCE_DEFAULT,
                key="generator_procedure_reference",
            )

            sheet_prefix = st.text_input(
                "Test Sheet Prefix",
                value=PROCEDURE_REFERENCE_DEFAULT,
                key="generator_sheet_prefix",
            )

    project_info = {
        "project": project,
        "trackside_version": trackside_version,
        "atp_version": atp_version,
        "ato_version": ato_version,
        "procedure_reference": procedure_reference,
        "procedure_title": PROCEDURE_TITLE_DEFAULT,
        "sheet_prefix": sheet_prefix or PROCEDURE_REFERENCE_DEFAULT,
    }

    # -------------------------------------------------------------------------
    # Uploads
    # -------------------------------------------------------------------------
    st.markdown("## 1. Upload Source Documents")

    upload_col1, upload_col2 = st.columns(2)

    with upload_col1:
        subdivision_pdf = st.file_uploader(
            "Station SACEM Subdivision PDF",
            type=["pdf"],
            key="generator_subdivision_pdf",
            help=(
                "Upload the station-specific SACEM subdivision / "
                "variant-map / anticipation PDF."
            ),
        )

    with upload_col2:
        procedure_docx = st.file_uploader(
            "Low-Speed Dynamic Test Procedure DOCX",
            type=["docx"],
            key="generator_procedure_docx",
            help=(
                "Upload the applicable C1252-TAC-0274-style procedure. "
                "Test IDs, names and checklist text are extracted from it."
            ),
        )

    if subdivision_pdf is None or procedure_docx is None:
        st.info(
            "Upload both the station Subdivision PDF and the applicable "
            "Low-Speed Dynamic Test Procedure DOCX."
        )
        return

    pdf_bytes = subdivision_pdf.getvalue()
    docx_bytes = procedure_docx.getvalue()

    pdf_size_mb = len(pdf_bytes) / (1024 * 1024)

    if pdf_size_mb > MAX_PDF_SIZE_MB:
        st.error(
            f"PDF file is {pdf_size_mb:.1f} MB. Maximum supported size "
            f"for this tool is {MAX_PDF_SIZE_MB} MB. "
            "Use an optimised/selectable-text PDF."
        )
        return

    # -------------------------------------------------------------------------
    # Procedure library
    # -------------------------------------------------------------------------
    try:
        test_library = extract_procedure_library_cached(docx_bytes)
    except Exception as exc:
        st.error(
            f"Unable to read procedure DOCX: "
            f"{type(exc).__name__}: {exc}"
        )
        st.exception(exc)
        return

    if test_library.empty:
        st.error(
            "No ATC-TNC-TRA-LSxx test definitions were found in the "
            "uploaded procedure DOCX."
        )
        return

    st.markdown("## 2. Review Procedure Test Library")

    st.caption(
        "Test codes and names are extracted from the uploaded procedure. "
        "Set 'Needs Abscissa' and edit the checklist as required. "
        "Use | to separate checklist actions."
    )

    edited_test_library = st.data_editor(
        test_library,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="generator_test_library_editor",
        column_config={
            "needs_abscissa": st.column_config.CheckboxColumn(
                "Needs Abscissa"
            ),
            "checklist": st.column_config.TextColumn(
                "Checklist - separate actions using |",
                width="large",
            ),
        },
    )

    test_library = edited_test_library

    # -------------------------------------------------------------------------
    # PDF extraction
    # -------------------------------------------------------------------------
    st.markdown("## 3. Extract Subdivision Events")

    current_pdf_hash = file_hash(pdf_bytes)

    existing_pdf_hash = st.session_state.get(
        "generator_pdf_hash"
    )

    pdf_changed = existing_pdf_hash != current_pdf_hash

    if pdf_changed:
        st.session_state.pop("generator_events", None)
        st.session_state.pop("generator_event_warnings", None)
        st.session_state.pop("generator_register", None)
        st.session_state.pop("generator_docx_zip", None)

    if st.button(
        "🔎 Extract Events From PDF",
        type="primary",
        use_container_width=True,
        key="generator_extract_events_button",
    ):
        progress_message = st.empty()
        progress_bar = st.progress(0)

        try:
            progress_message.info(
                "Reading selectable text from PDF. "
                "This is cached after the first extraction."
            )

            page_texts = extract_pdf_pages_cached(pdf_bytes)

            progress_message.info(
                f"PDF text loaded from {len(page_texts)} page(s). "
                "Parsing subdivision event rows..."
            )

            events, warnings = build_events_from_pages(
                page_texts=page_texts,
                progress_bar=progress_bar,
                progress_message=progress_message,
            )

            st.session_state["generator_pdf_hash"] = current_pdf_hash
            st.session_state["generator_events"] = events
            st.session_state["generator_event_warnings"] = warnings
            st.session_state["generator_page_count"] = len(page_texts)
            st.session_state.pop("generator_register", None)
            st.session_state.pop("generator_docx_zip", None)

            if events.empty:
                progress_message.warning(
                    "PDF extraction completed but no usable subdivision "
                    "event rows were found."
                )
            else:
                progress_message.success(
                    f"Extraction completed: {len(events)} event row(s) "
                    f"found from {len(page_texts)} page(s)."
                )

        except Exception as exc:
            progress_bar.empty()
            progress_message.error(
                f"PDF extraction failed: {type(exc).__name__}: {exc}"
            )
            st.exception(exc)
            return

    events = st.session_state.get(
        "generator_events",
        pd.DataFrame(columns=EXTRACTED_EVENT_COLUMNS),
    )

    if events.empty:
        st.warning(
            "Click 'Extract Events From PDF' to create the source event "
            "register."
        )
        return

    warnings = st.session_state.get(
        "generator_event_warnings",
        [],
    )

    if warnings:
        with st.expander(
            f"Extraction Warnings ({len(warnings)})",
            expanded=False,
        ):
            st.warning(
                "Some pages could not be fully interpreted. "
                "Review extracted source events before creating sheets."
            )

            for warning in warnings:
                st.write(f"• {warning}")

    with st.expander("Review Extracted Source Events", expanded=False):
        st.dataframe(
            events,
            use_container_width=True,
            hide_index=True,
        )

    # -------------------------------------------------------------------------
    # Range selection
    # -------------------------------------------------------------------------
    st.markdown("## 4. Select Start and End Point")

    available_branches = sorted(
        branch
        for branch in events["branch"].dropna().unique()
        if safe_text(branch)
    )

    selected_branch = st.selectbox(
        "Branch Filter",
        options=["All branches"] + available_branches,
        key="generator_branch_filter",
    )

    range_events = events.copy()

    if selected_branch != "All branches":
        range_events = range_events[
            range_events["branch"].eq(selected_branch)
        ].copy()

    range_events = range_events.dropna(
        subset=["abscissa_m"]
    ).copy()

    if range_events.empty:
        st.error(
            "No valid abscissa rows were found for the selected branch."
        )
        return

    minimum_kp = float(range_events["abscissa_m"].min())
    maximum_kp = float(range_events["abscissa_m"].max())

    range_col1, range_col2 = st.columns(2)

    with range_col1:
        start_kp = st.number_input(
            "Start KP / Abscissa",
            min_value=minimum_kp,
            max_value=maximum_kp,
            value=minimum_kp,
            step=0.1,
            format="%.3f",
            key="generator_start_kp",
        )

    with range_col2:
        end_kp = st.number_input(
            "End KP / Abscissa",
            min_value=minimum_kp,
            max_value=maximum_kp,
            value=maximum_kp,
            step=0.1,
            format="%.3f",
            key="generator_end_kp",
        )

    if st.button(
        "Create Draft Test Register",
        type="primary",
        use_container_width=True,
        key="generator_create_register_button",
    ):
        st.session_state["generator_register"] = create_draft_register(
            extracted_events=events,
            start_kp=float(start_kp),
            end_kp=float(end_kp),
            selected_branch=selected_branch,
        )

        st.session_state.pop("generator_docx_zip", None)

    register = st.session_state.get(
        "generator_register",
        pd.DataFrame(columns=REGISTER_COLUMNS),
    )

    if register.empty:
        st.warning(
            "Create a draft register after selecting the start and end KP."
        )
        return

    # -------------------------------------------------------------------------
    # Register review
    # -------------------------------------------------------------------------
    st.markdown("## 5. Review and Assign Applicable Tests")

    st.caption(
        "Select the applicable test from the procedure library for each "
        "source event. Delete non-applicable events. Verify all data."
    )

    available_test_codes = [""] + sorted(
        test_library["test_code"]
        .dropna()
        .astype(str)
        .tolist()
    )

    reviewed_register = st.data_editor(
        register,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="generator_register_editor",
        column_config={
            "test_code": st.column_config.SelectboxColumn(
                "Applicable Test",
                options=available_test_codes,
            ),
            "needs_abscissa": st.column_config.CheckboxColumn(
                "Needs Abscissa"
            ),
            "abscissa_m": st.column_config.NumberColumn(
                "Abscissa (m)",
                format="%.3f",
            ),
            "anticipate_distance_m": st.column_config.NumberColumn(
                "Anticipation Distance (m)",
                format="%.3f",
            ),
            "anticipate_abscissa_m": st.column_config.NumberColumn(
                "Anticipated Abscissa (m)",
                format="%.3f",
            ),
            "review_status": st.column_config.SelectboxColumn(
                "Review Status",
                options=[
                    "To be reviewed",
                    "Reviewed",
                    "Approved for generation",
                    "Engineering review required",
                    "Not applicable",
                ],
            ),
            "source_evidence": st.column_config.TextColumn(
                "Source Evidence",
                width="large",
            ),
            "remarks": st.column_config.TextColumn(
                "Remarks",
                width="large",
            ),
        },
    )

    reviewed_register = apply_test_library_to_register(
        register=reviewed_register,
        test_library=test_library,
    )

    st.session_state["generator_register"] = reviewed_register

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)

    metric_col1.metric(
        "Register Rows",
        len(reviewed_register),
    )

    metric_col2.metric(
        "Assigned Tests",
        int(
            reviewed_register["test_code"]
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ),
    )

    metric_col3.metric(
        "Approved Rows",
        int(
            reviewed_register["review_status"]
            .eq("Approved for generation")
            .sum()
        ),
    )

    metric_col4.metric(
        "Rows With Abscissa",
        int(
            reviewed_register["abscissa_m"]
            .notna()
            .sum()
        ),
    )

    # -------------------------------------------------------------------------
    # Download output
    # -------------------------------------------------------------------------
    st.markdown("## 6. Generate Downloads")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        events=events,
        test_library=test_library,
        project_info=project_info,
    )

    approved_register = reviewed_register[
        reviewed_register["test_code"]
        .astype(str)
        .str.strip()
        .ne("")
        & reviewed_register["review_status"]
        .eq("Approved for generation")
    ].copy()

    download_col1, download_col2, download_col3 = st.columns(3)

    with download_col1:
        st.download_button(
            "⬇️ Download Excel Register",
            data=excel_bytes,
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
        )

    with download_col2:
        st.download_button(
            "⬇️ Download CSV Register",
            data=reviewed_register.to_csv(
                index=False
            ).encode("utf-8"),
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_Test_Register.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with download_col3:
        if approved_register.empty:
            st.button(
                "📦 Generate DOCX Test Sheets",
                disabled=True,
                use_container_width=True,
            )

            st.caption(
                "Assign a test code and set Review Status to "
                "'Approved for generation'."
            )

        else:
            if st.button(
                "📦 Generate DOCX Test Sheets",
                type="primary",
                use_container_width=True,
                key="generator_create_docx_button",
            ):
                docx_progress_message = st.empty()
                docx_progress_bar = st.progress(0)

                try:
                    docx_zip = create_docx_zip(
                        register=approved_register,
                        project_info=project_info,
                        test_library=test_library,
                        progress_bar=docx_progress_bar,
                        progress_message=docx_progress_message,
                    )

                    st.session_state["generator_docx_zip"] = docx_zip

                except Exception as exc:
                    docx_progress_bar.empty()
                    docx_progress_message.error(
                        f"DOCX generation failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    st.exception(exc)

            docx_zip = st.session_state.get(
                "generator_docx_zip"
            )

            if docx_zip:
                st.download_button(
                    "⬇️ Download DOCX Test Sheets",
                    data=docx_zip,
                    file_name=clean_filename(
                        f"{project_info['sheet_prefix']}_"
                        f"ATC_Low_Speed_Test_Sheets.zip"
                    ),
                    mime="application/zip",
                    use_container_width=True,
                    key="generator_download_docx_button",
                )
