from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz  # PyMuPDF
import numpy as np
import pandas as pd
import pytesseract
import streamlit as st
from PIL import Image
# =============================================================================
# CONFIGURATION
# =============================================================================

PROCEDURE_REFERENCE_DEFAULT = "C1252-TAC-0274"
PROCEDURE_TITLE_DEFAULT = "Trackside ATC Low-Speed Dynamic Test Procedure"

MAX_PDF_SIZE_MB = 100
MAX_EVIDENCE_LENGTH = 3000

OCR_DPI = 300
OCR_MIN_TEXT_CHARS = 80
OCR_CONFIDENCE_MIN = 25


# =============================================================================
# TEST DEFINITIONS
#
# These are generic procedure-level rules.
# No station-specific chainage, branch, section, location or variant is fixed.
# =============================================================================

TEST_RULES: Dict[str, Dict[str, Any]] = {
    "LS01": {
        "id": "ATC-TNC-TRA-LS01",
        "name": "MTIB Initialization Test",
        "short_name": "MTIB Initialization",
        "patterns": [
            r"\bMTIB\b",
            r"MOVING\s+TRAIN\s+INITIALISATION",
            r"MOVING\s+TRAIN\s+INITIALIZATION",
        ],
        "checks": [
            ("Selected Mode", "RM", "Y / N"),
            ("Localisation Status", "Out Loc", "Y / N"),
            ("Cross MTIB", "Localisation becomes Wait Loc", "Y / N"),
            ("Beacon Codes", "Record and compare with approved design", "Record"),
            ("Calibration", "Record A-car number and calibration", "mm"),
            ("Localisation Status", "Total Loc", "Y / N"),
        ],
    },
    "LS02": {
        "id": "ATC-TNC-TRA-LS02",
        "name": "STIB Initialization Test",
        "short_name": "STIB Initialization",
        "patterns": [
            r"\bSTIB\b",
            r"STATIONARY\s+TRAIN\s+INITIALISATION",
            r"STATIONARY\s+TRAIN\s+INITIALIZATION",
        ],
        "checks": [
            ("Selected Mode", "RM", "Y / N"),
            ("Localisation Status", "Out Loc", "Y / N"),
            ("Stop on STIB", "Localisation becomes Wait Inv", "Y / N"),
            ("Beacon Codes", "Record and compare with approved design", "Record"),
            ("Localisation Status", "Total Loc", "Y / N"),
            ("Mode Available", "CM/AM available as applicable", "Y / N"),
            ("First RB / MTIB", "No Emergency Brake", "Y / N"),
        ],
    },
    "LS03": {
        "id": "ATC-TNC-TRA-LS03",
        "name": "Spacing Stopping Point – Track Circuit",
        "short_name": "Spacing Stopping Point",
        "patterns": [
            r"SPACING\s+STOPPING\s+POINT",
            r"\bSPACING\b.*\bSTOPPING\b",

            # Generic SACEM track-circuit wording:
            # TUC KM T/C
            # TUC KF T/C
            # TUC KN T/C
            # THW SP T/C
            r"\b[A-Z]{2,5}\s+K[A-Z0-9]*\s+T\s*/?\s*C\b",
            r"\b[A-Z]{2,5}\s+S[A-Z0-9]*\s+T\s*/?\s*C\b",
        ],
        "checks": [
            ("Track Circuit Occupied", "Associated variant restrictive", "Y / N"),
            ("Available Mode", "CM/AUTO", "Y / N"),
            ("Localisation Loc Max", "Record", "m"),
            ("Target Distance", "Record", "m"),
            ("Abscissa", "Record", "m"),
            (
                "Localisation Check",
                "Loc Max + Target Distance = Abscissa ± approved tolerance",
                "Y / N",
            ),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Speedometer Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Associated Variant", "Restrictive", "Y / N"),
            ("Track Circuit Free", "Associated variant permissive", "Y / N"),
            ("Target Distance", ">= restrictive condition where applicable", "Y / N"),
            ("OMAP Target Speed", "Record", "kph"),
            ("Speedometer Target Speed", "> 0 where applicable", "kph"),
            ("Associated Variant", "Permissive", "Y / N"),
        ],
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "name": "Special Stopping Point – Signal Without Overlap",
        "short_name": "Special Stopping Point",
        "patterns": [
            r"SPECIAL\s+STOPPING\s+POINT",
            r"\bSSP\b",
            r"SSP\s+DISTANCE",
        ],
        "checks": [
            ("Restrictive Signal", "Associated variant restrictive", "Y / N"),
            ("Available Mode", "CM/AUTO", "Y / N"),
            ("Localisation Loc Max", "Record", "m"),
            ("Target Distance", "Record", "m"),
            ("Abscissa", "Record", "m"),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Permissive Signal", "Associated variant permissive", "Y / N"),
            ("Target Comparison", "Permissive target >= restrictive", "Y / N"),
        ],
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "name": "Complex Stopping Point – Signal With Overlap",
        "short_name": "Complex Stopping Point",
        "patterns": [
            r"COMPLEX\s+STOPPING\s+POINT",
            r"SIGNAL.*OVERLAP",
            r"\bPEP\b.*\bPSD\b",
        ],
        "checks": [
            ("Signal Red / Overlap Occupied", "Variants restrictive", "Y / N"),
            ("Signal Red / Overlap Free", "Record stopping behaviour", "Record"),
            ("Signal Blue / Overlap Free", "Variants permissive", "Y / N"),
            ("Target Comparison", "Permissive target >= restrictive", "Y / N"),
        ],
    },
    "LS06": {
        "id": "ATC-TNC-TRA-LS06",
        "name": "Stopping Point – Point / Turnout",
        "short_name": "Convergence / Divergence",
        "patterns": [
            r"\bCONVERGENCE\b",
            r"\bDIVERGENCE\b",
            r"\bTURNOUT\b",
            r"POINT\s+(NORMAL|REVERSE|CONTROLLED|UNCONTROLLED)",
            r"POINT\s+(NORMAL|REVERSE)\s+POSITION",
        ],
        "checks": [
            ("Point Uncontrolled", "Associated variant restrictive", "Y / N"),
            ("Available Mode", "CM/AM", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record", "m"),
            ("Point Controlled", "Associated variant permissive", "Y / N"),
            ("Target Comparison", "Controlled >= uncontrolled", "Y / N"),
        ],
    },
    "LS07": {
        "id": "ATC-TNC-TRA-LS07",
        "name": "Stopping Point – End of SACEM",
        "short_name": "End of SACEM",
        "patterns": [
            r"END\s+OF\s+SACEM",
            r"EXIT\s+SACEM",
            r"EXIT\s+SACEM\s+ROUTE",
        ],
        "checks": [
            ("Available Mode Before End of SACEM", "CM/AUTO", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record", "m"),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Beyond End of SACEM", "CM/AM unavailable", "Y / N"),
        ],
    },
    "LS08": {
        "id": "ATC-TNC-TRA-LS08",
        "name": "Station Stopping Point and Door Side Verification",
        "short_name": "Station Stop / Door Side",
        "patterns": [
            r"STATION\s+CHARACTERISTICS",
            r"STATION\s+STOPPING",
            r"PLATFORM\s+STOPPING",
            r"STATION.*PSD",
        ],
        "checks": [
            ("Localisation Status", "Total Loc", "Y / N"),
            ("Available Mode", "CM/AM", "Y / N"),
            ("Selected Mode", "CM", "Y / N"),
            ("Precise Stop", "Record Loc Max and measured distance", "m"),
            ("Door Side", "Correct side enabled", "A / B"),
            ("Stop Short / Overrun", "Within approved tolerance", "Y / N"),
        ],
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "name": "PSD Operation Pre-Test",
        "short_name": "PSD Opening Pre-Test",
        "patterns": [
            r"\bPSD\b",
            r"ALL\s+DOOR\s+CLOSED",
        ],
        "checks": [
            ("Correct Platform Side", "Door side enabled", "Y / N"),
            ("Open Wrong-Side Doors", "PSD / relay not activated", "Y / N"),
            ("Open Correct-Side Doors", "PSD / relay activated", "Y / N"),
            ("All Doors Closed Lost", "Variants restrictive; RM only", "Y / N"),
            ("PIOS / Override Active", "CM/AM and timeout checked", "Y / N"),
            ("Close Correct-Side Doors", "PSD closed and variants permissive", "Y / N"),
        ],
    },
    "LS10": {
        "id": "ATC-TNC-TRA-LS10",
        "name": "Emergency Stop Zone Test",
        "short_name": "Emergency Stop Zone",
        "patterns": [
            r"EMERGENCY\s+ZONE",
            r"EMERGENCY\s+STOP\s+ZONE",
            r"\bESZ\b",
            r"ESA\s+TEMP\s+POINT",
            r"REPEAT\s+ENTRANCE\s+EMERGENCY",
        ],
        "checks": [
            ("ESZ Condition Active", "Associated variant restrictive", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record", "m"),
            ("ESZ Condition Resumed", "Associated variant permissive", "Y / N"),
            ("Inside Restrictive Zone", "CM/AM unavailable", "Y / N"),
            ("End of ESZ", "CM becomes available", "Y / N"),
        ],
    },
    "LS11": {
        "id": "ATC-TNC-TRA-LS11",
        "name": "Change of End Test",
        "short_name": "Change of End",
        "patterns": [
            r"CHANGE\s+OF\s+END",
            r"CHANGE\s+ENDS",
            r"REVERSE\s+SUB-?SECTION",
            r"REVERSE\s+BRANCH",
        ],
        "checks": [
            ("Cab 1 Arrival", "Total Loc and CM/AM available", "Y / N"),
            ("Cab 1 Loc Max / Loc Min", "Record", "m"),
            ("Cab 2 Before Change of End", "Shutdown and distance checked", "Y / N"),
            ("Cab 2 After Change of End", "Total Loc and CM/AM available", "Y / N"),
            ("First RB / MTIB", "No delocalisation / emergency brake", "Y / N"),
        ],
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "name": "Temporary Speed Restriction Test",
        "short_name": "Temporary Speed Restriction",
        "patterns": [
            r"TEMPORARY\s+SPEED\s+RESTRICTION",
            r"\bTSR\b",
        ],
        "checks": [
            ("Track Circuit", "Record selected track circuit", "Record"),
            ("Section", "Record selected section", "Record"),
            ("TSR Value", "Record TSR value", "kph"),
            ("Train Behaviour", "Target speed follows TSR", "Y / N"),
        ],
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "short_name": "Return Channel",
        "patterns": [
            r"RETURN\s+CHANNEL",
        ],
        "checks": [
            ("Return Channel Location", "Record location", "Record"),
            ("Return Channel Loop", "Record loop/equipment reference", "Record"),
            ("Trackside Reception", "Train information received", "Y / N"),
        ],
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "name": "End of Shunt Test",
        "short_name": "End of Shunt",
        "patterns": [
            r"END\s+OF\s+SHUNT",
            r"SHUNT\s+ROUTE",
            r"SHUNT\s+TUNNEL",
        ],
        "checks": [
            ("Shunt Route Name", "Record", "Record"),
            ("Step 1 – Begin", "Record branch, Loc Max and target distance", "Record"),
            ("Step 2", "Train in RM", "Y / N"),
            ("Step 3 – End", "Record CM-resume branch and Loc Max", "Record"),
            ("Acceptance", "CM/AM resumes at approved location", "Y / N"),
        ],
    },
}


REGISTER_COLUMNS = [
    "test_code",
    "test_id",
    "test_name",
    "sector",
    "section",
    "subsection",
    "branch",
    "direction",
    "abscissa_m",
    "absolute_abscissa_m",
    "subsection_start_abscissa_m",
    "inter_singularity_m",
    "row_number",
    "singularity_code",
    "event_name",
    "associated_variant",
    "location_name",
    "source_page",
    "source_evidence",
    "calculation_log",
    "review_status",
    "remarks",
]

SINGULARITY_COLUMNS = [
    "pdf_page",
    "sector",
    "section",
    "direction",
    "subsection",
    "route",
    "row_number",
    "inter_singularity_m",
    "absolute_abscissa_m",
    "subsection_start_abscissa_m",
    "calculated_local_abscissa_m",
    "singularity_code",
    "singularity_name",
    "associated_variant",
    "calculation_log",
    "source_evidence",
]

VARIANT_COMPOSITION_COLUMNS = [
    "pdf_page",
    "sector",
    "section",
    "direction",
    "subsection",
    "route",
    "rank",
    "participating_rank",
    "anticipated_rank",
    "participating_variants",
    "anticipated_variants",
    "all_variants",
    "source_evidence",
]

# =============================================================================
# BASIC HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def normalise_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", safe_text(value)).strip()


def clean_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", safe_text(value))
    value = re.sub(r"\s+", "_", value)
    return value[:160] or "ATC_Test_Sheet"


def get_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_float(value: Any) -> Optional[float]:
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


def format_distance(value: Any) -> str:
    numeric = to_float(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


def normalise_subsection(value: str) -> str:
    match = re.search(
        r"(\d{1,2})\s*[- ]\s*(\d{1,2})\s*[- ]\s*(\d{1,2})",
        safe_text(value),
    )

    if not match:
        return safe_text(value)

    sector, section, subsection = match.groups()

    return f"{int(sector):02d}-{int(section):02d}-{int(subsection)}"


# =============================================================================
# OCR AND PDF EXTRACTION
# =============================================================================

def page_has_meaningful_text(page_text: str) -> bool:
    compact = re.sub(r"\s+", "", safe_text(page_text))
    return len(compact) >= OCR_MIN_TEXT_CHARS


def preprocess_for_ocr(pixmap: fitz.Pixmap) -> Image.Image:
    mode = "RGB" if pixmap.n >= 3 else "L"

    image = Image.frombytes(
        mode,
        [pixmap.width, pixmap.height],
        pixmap.samples,
    )

    image_array = np.array(image)

    if image_array.ndim == 3:
        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = image_array

    gray = cv2.resize(
        gray,
        None,
        fx=1.4,
        fy=1.4,
        interpolation=cv2.INTER_CUBIC,
    )

    cleaned = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )[1]

    return Image.fromarray(cleaned)


def group_ocr_words_into_visual_lines(
    ocr_data: Dict[str, List[Any]],
    y_tolerance: int = 18,
) -> List[str]:
    """
    OCR word order is rebuilt using physical x/y positions.

    This is essential for table schedules because normal OCR text output can
    interleave the table columns.
    """
    words: List[Dict[str, Any]] = []

    for index, raw_word in enumerate(ocr_data["text"]):
        word = normalise_spaces(raw_word)

        if not word:
            continue

        try:
            confidence = float(ocr_data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0

        if confidence < OCR_CONFIDENCE_MIN:
            continue

        words.append(
            {
                "text": word,
                "left": int(ocr_data["left"][index]),
                "top": int(ocr_data["top"][index]),
            }
        )

    words.sort(key=lambda item: (item["top"], item["left"]))

    line_groups: List[List[Dict[str, Any]]] = []

    for word in words:
        if not line_groups:
            line_groups.append([word])
            continue

        previous_line = line_groups[-1]
        average_y = sum(item["top"] for item in previous_line) / len(previous_line)

        if abs(word["top"] - average_y) <= y_tolerance:
            previous_line.append(word)
        else:
            line_groups.append([word])

    lines: List[str] = []

    for line_words in line_groups:
        line_words.sort(key=lambda item: item["left"])
        lines.append(
            normalise_spaces(" ".join(item["text"] for item in line_words))
        )

    return lines


def ocr_pdf_page(page: fitz.Page) -> str:
    scale = OCR_DPI / 72
    matrix = fitz.Matrix(scale, scale)

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False,
        colorspace=fitz.csRGB,
    )

    image = preprocess_for_ocr(pixmap)

    ocr_data = pytesseract.image_to_data(
        image,
        lang="eng",
        config="--oem 3 --psm 6",
        output_type=pytesseract.Output.DICT,
    )

    return "\n".join(group_ocr_words_into_visual_lines(ocr_data))

def create_pdf_text_log(
    extracted_pages: List[Dict[str, Any]],
) -> str:
    """
    Create a complete plain-text audit log of all text extracted from the PDF.

    The log includes:
    - PDF page number
    - Native PDF text or OCR extraction method
    - Character and word count
    - Full text extracted from that page
    """
    lines = [
        "=" * 110,
        "PDF TEXT EXTRACTION / OCR AUDIT LOG",
        "=" * 110,
        "",
        "This file contains all text extracted from the uploaded PDF.",
        "Pages are marked as either:",
        "  - Native PDF Text: selectable text obtained directly from the PDF",
        "  - OCR: text recognised from a scanned/image-only PDF page",
        "",
    ]

    for page_data in extracted_pages:
        page_number = page_data["page_number"]
        extraction_method = page_data["extraction_method"]
        page_text = safe_text(page_data["text"])

        lines.extend(
            [
                "=" * 110,
                f"PDF PAGE {page_number}",
                "=" * 110,
                f"Extraction Method: {extraction_method}",
                f"Character Count: {len(page_text)}",
                f"Word Count: {len(page_text.split())}",
                "",
                "EXTRACTED TEXT",
                "-" * 110,
                page_text if page_text else "[NO TEXT EXTRACTED]",
                "",
            ]
        )

    return "\n".join(lines)


def create_pdf_extraction_summary(
    extracted_pages: List[Dict[str, Any]],
) -> pd.DataFrame:
    """Create a concise per-page extraction summary for Streamlit/Excel."""
    records: List[Dict[str, Any]] = []

    for page_data in extracted_pages:
        page_text = safe_text(page_data["text"])

        records.append(
            {
                "pdf_page": page_data["page_number"],
                "extraction_method": page_data["extraction_method"],
                "character_count": len(page_text),
                "word_count": len(page_text.split()),
                "text_preview": page_text[:500],
            }
        )

    return pd.DataFrame(records)

@st.cache_data(show_spinner=False, max_entries=4)
def extract_pdf_text_pages(
    pdf_bytes: bytes,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Extract text from every PDF page.

    Native/selectable text is used where sufficient text exists.
    OCR is used for image-only/scanned pages.

    Returns:
        pages:
            List of text strings used by the test-point extraction logic.

        extracted_pages:
            Detailed audit data for each PDF page, including extraction method.
    """
    document = fitz.open(stream=pdf_bytes, filetype="pdf")

    pages: List[str] = []
    extracted_pages: List[Dict[str, Any]] = []

    try:
        for page_number, page in enumerate(document, start=1):
            native_text = page.get_text("text") or ""

            if page_has_meaningful_text(native_text):
                extracted_text = native_text
                extraction_method = "Native PDF Text"

            else:
                extracted_text = ocr_pdf_page(page)
                extraction_method = "OCR (image/scanned page)"

            pages.append(extracted_text)

            extracted_pages.append(
                {
                    "page_number": page_number,
                    "extraction_method": extraction_method,
                    "text": extracted_text,
                }
            )

    finally:
        document.close()

    return pages, extracted_pages


# =============================================================================
# SOURCE PAGE IDENTIFICATION
# =============================================================================

def is_subdivision_page(page_text: str) -> bool:
    upper = page_text.upper()

    markers = [
        "SINGULARITIES",
        "SINGULARITY",
        "INTER SINGUL",
        "TEST ABSC",
        "SUB SECTION",
        "SUB-SECTION",
        "UP TRACK NORMAL DIRECTION",
        "DN TRACK NORMAL DIRECTION",
        "DOWN TRACK NORMAL DIRECTION",
        "K.P.",
    ]

    return any(marker in upper for marker in markers)


def page_metadata(page_text: str) -> Dict[str, str]:
    text = normalise_spaces(page_text)

    sector_match = re.search(
        r"\bSECTOR\s*[: ,]*\s*(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )

    section_match = re.search(
        r"\bSECTION\s*[: ,]*\s*(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )

    direction = ""

    if re.search(r"\bUP\s+TRACK\b", text, flags=re.IGNORECASE):
        direction = "Up"
    elif re.search(r"\b(?:DN|DOWN)\s+TRACK\b", text, flags=re.IGNORECASE):
        direction = "Down"

    return {
        "sector": f"{int(sector_match.group(1)):02d}" if sector_match else "",
        "section": f"{int(section_match.group(1)):02d}" if section_match else "",
        "direction": direction,
    }


# =============================================================================
# EVENT EXTRACTION
# =============================================================================

def extract_subsection_reference(text: str) -> str:
    """
    Extract generic subsection references.

    Examples:
        9-1-1
        09-01-1
        09-01-1-0
        Sub Section 09-01-1
    """
    match = re.search(
        r"(?:SUB\s*-?\s*SECTION(?:\s*(?:NUMBER|NO\.?))?\s*[:=]?\s*)?"
        r"\b(\d{1,2}\s*[- ]\s*\d{1,2}\s*[- ]\s*\d{1,2})"
        r"(?:\s*[- ]\s*\d{1,2})?\b",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    return normalise_subsection(match.group(1))


def extract_associated_variant(text: str) -> str:
    match = re.search(
        r"ASSOCIATED\s+VARIANTS?"
        r"(?:\s+NUMBER)?\s*[:=]?\s*"
        r"(.+?)"
        r"(?="
        r"$|"
        r"RANK\s+OF\s+FIRST|"
        r"NUMBER\s+OF\s+ANTICIPATED|"
        r"SUB\s*-?\s*SECTION|"
        r"BEACON\s+TYPE|"
        r"VALUE\s+OF|"
        r"SSP\s+DISTANCE|"
        r"FIXED\s+CHARACTERISTICS"
        r")",
        normalise_spaces(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    value = normalise_spaces(match.group(1)).strip("() ")

    return value[:500]


def extract_location_name(text: str) -> str:
    patterns = [
        r"RETURN\s+CHANNEL\s*(?:NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"(?:SIGNAL|SIG)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"(?:POINT|TURNOUT)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"(?:TRACK\s*CIRCUIT|T/C)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"ESA\s+TEMP\s+POINT\s*\d+",
        r"EMERGENCY\s+ZONE\s+ENDS",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            return normalise_spaces(match.group(0))

    return ""

def extract_route_reference(text: str) -> str:
    """
    Extract route / route name from Variant Composition or schedule pages.

    Examples supported:
        Route: ABC
        Route Name = ABC
        Route No. 12
        Shunt Route XYZ
    """
    patterns = [
        r"\bROUTE\s+NAME\s*[:=]?\s*([A-Z0-9/_ -]+)",
        r"\bROUTE\s+NO\.?\s*[:=]?\s*([A-Z0-9/_ -]+)",
        r"\bROUTE\s*[:=]\s*([A-Z0-9/_ -]+)",
        r"\bSHUNT\s+ROUTE\s*[:=]?\s*([A-Z0-9/_ -]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, normalise_spaces(text), flags=re.IGNORECASE)

        if match:
            return normalise_spaces(match.group(1)).strip(" -:")

    return ""


def extract_rank_value(text: str, rank_type: str = "") -> str:
    """
    Extract a rank value from Variant Composition pages.

    Supports wording such as:
        Rank: 4
        Rank of First Participating Variant: 4
        Rank of First Anticipated Variant: 7
        Number of Anticipated Variants: 3
    """
    type_expression = ""

    if rank_type:
        type_expression = rf"(?:{rank_type}\s+)?"

    patterns = [
        rf"\bRANK\s+(?:OF\s+)?(?:FIRST\s+)?{type_expression}"
        rf"(?:VARIANT\s+)?(?:NUMBER\s*)?[:=]?\s*(\d+)",

        rf"\b{type_expression}RANK\s*[:=]?\s*(\d+)",

        rf"\bNUMBER\s+OF\s+{type_expression}VARIANTS?\s*[:=]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, normalise_spaces(text), flags=re.IGNORECASE)

        if match:
            return safe_text(match.group(1))

    return ""


def extract_variants_by_type(text: str, variant_type: str) -> str:
    """
    Extract variant references such as:

        Participating Variants: TUC KM T/C, TUC KF T/C
        Anticipated Variants = 15.4 / 15.5
    """
    pattern = (
        rf"\b{variant_type}\s+VARIANTS?"
        rf"(?:\s+(?:NUMBER|NO\.?))?"
        rf"\s*[:=]?\s*"
        rf"(.+?)"
        rf"(?="
        rf"$|"
        rf"RANK\b|"
        rf"NUMBER\s+OF\b|"
        rf"SUB\s*-?\s*SECTION\b|"
        rf"ROUTE\b|"
        rf"FIXED\s+CHARACTERISTICS\b|"
        rf"ASSOCIATED\s+VARIANTS?\b"
        rf")"
    )

    match = re.search(
        pattern,
        normalise_spaces(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    return normalise_spaces(match.group(1)).strip("() ,;:-")[:500]


def is_variant_composition_page(page_text: str) -> bool:
    upper = safe_text(page_text).upper()

    markers = [
        "VARIANT COMPOSITION",
        "VARIANTS COMPOSITION",
        "COMPOSITION OF VARIANTS",
        "PARTICIPATING VARIANT",
        "ANTICIPATED VARIANT",
        "RANK OF FIRST PARTICIPATING",
        "RANK OF FIRST ANTICIPATED",
    ]

    return any(marker in upper for marker in markers)


def infer_event_name(text: str) -> str:
    upper = text.upper()

    labels = [
        ("SPACING STOPPING POINT", "Spacing Stopping Point"),
        ("SPECIAL STOPPING POINT", "Special Stopping Point"),
        ("COMPLEX STOPPING POINT", "Complex Stopping Point"),
        ("EMERGENCY ZONE", "Emergency Stop Zone"),
        ("ESA TEMP POINT", "Emergency Stop Zone"),
        ("RETURN CHANNEL", "Return Channel"),
        ("MOVING TRAIN INITIALISATION", "MTIB"),
        ("MOVING TRAIN INITIALIZATION", "MTIB"),
        ("STATIONARY TRAIN INITIALISATION", "STIB"),
        ("STATIONARY TRAIN INITIALIZATION", "STIB"),
        ("CHANGE OF END", "Change of End"),
        ("REVERSE SUBSECTION", "Change of End"),
        ("END OF SACEM", "End of SACEM"),
        ("EXIT SACEM", "End of SACEM"),
        ("END OF SHUNT", "End of Shunt"),
        ("CONVERGENCE", "Convergence"),
        ("DIVERGENCE", "Divergence"),
        ("TURNOUT", "Turnout"),
        ("STATION CHARACTERISTICS", "Station Stopping Point"),
        ("TEMPORARY SPEED RESTRICTION", "Temporary Speed Restriction"),
    ]

    for phrase, label in labels:
        if phrase in upper:
            return label

    track_circuit_match = re.search(
        r"\b([A-Z]{2,5}\s+[KS][A-Z0-9]*\s+T\s*/?\s*C)\b",
        upper,
    )

    if track_circuit_match:
        return track_circuit_match.group(1)

    return "Subdivision Event"


def identify_tests(source_text: str) -> List[str]:
    detected: List[str] = []

    for test_code, test_rule in TEST_RULES.items():
        if any(
            re.search(pattern, source_text, flags=re.IGNORECASE)
            for pattern in test_rule["patterns"]
        ):
            detected.append(test_code)

    return list(dict.fromkeys(detected))


def parse_singularity_row(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a singularity-schedule row.

    Expected source structure:

        row | inter singularity | absolute k.p. | code | remarks

    Examples OCR may produce:

        20 8.4 m 1,611.4 15.7 Sub section end...
        26 3.0 m 1,788.0 15.4 Associated variant : (TUC KM T/C)
    """
    text = normalise_spaces(line)

    pattern = re.compile(
        r"^\s*"
        r"(?P<row>\d{1,3})\s+"
        r"(?:!\s*)?"
        r"(?:(?P<inter>-?\d+(?:[,.]\d+)?)\s*m?\s+)?"
        r"(?P<abscissa>-?\d+(?:[,.]\d+)?)\s+"
        r"(?P<code>\d+(?:[._-]\d+)?)\s+"
        r"(?P<remaining>.+)$",
        flags=re.IGNORECASE,
    )

    match = pattern.match(text)

    if not match:
        return None

    abscissa = to_float(match.group("abscissa"))

    if abscissa is None:
        return None

    if abscissa < -10000 or abscissa > 100000:
        return None

    return {
        "row_number": safe_text(match.group("row")),
        "inter_singularity_m": to_float(match.group("inter")),
        "abscissa_m": abscissa,
        "singularity_code": safe_text(match.group("code")),
        "remaining": normalise_spaces(match.group("remaining")),
    }


def calculate_local_abscissa(
    absolute_abscissa_m: Optional[float],
    subsection_start_abscissa_m: Optional[float],
    subsection: str,
) -> Tuple[Optional[float], str]:
    """
    Calculate local test-sheet abscissa.

    Example:
        Event schedule chainage:          1788.0 m
        Sub-section start chainage:       1611.4 m
        Local abscissa:                    176.6 m

        1788.0 - 1611.4 = 176.6 m
    """
    if absolute_abscissa_m is None:
        return None, "Calculation unavailable: event absolute chainage is missing."

    if subsection_start_abscissa_m is None:
        return (
            None,
            "Calculation unavailable: current sub-section start chainage "
            "was not detected before this event.",
        )

    local_abscissa = round(
        absolute_abscissa_m - subsection_start_abscissa_m,
        3,
    )

    log = (
        f"Sub-section {subsection or 'not stated'} local abscissa calculation: "
        f"{absolute_abscissa_m:.3f} m - "
        f"{subsection_start_abscissa_m:.3f} m = "
        f"{local_abscissa:.3f} m."
    )

    if local_abscissa < -0.001:
        log += (
            " WARNING: negative result. Check OCR order, direction, "
            "branch context and sub-section boundary."
        )

    return local_abscissa, log

def extract_singularities_table(pages: List[str]) -> pd.DataFrame:
    """
    Extract every parsed singularity schedule row.

    Important:
    - Absolute K.P. remains preserved in absolute_abscissa_m.
    - The sub-section start K.P. is used as local zero.
    - Calculated local abscissa =
          absolute singularity K.P. - sub-section start K.P.
    """
    records: List[Dict[str, Any]] = []

    for page_number, page_text in enumerate(pages, start=1):
        if not is_subdivision_page(page_text):
            continue

        metadata = page_metadata(page_text)

        lines = [
            normalise_spaces(line)
            for line in page_text.splitlines()
            if normalise_spaces(line)
        ]

        current_subsection = ""
        current_route = ""
        current_subsection_start_abscissa: Optional[float] = None

        for line_index, line in enumerate(lines):
            parsed_row = parse_singularity_row(line)

            # Use nearby text only for supporting information.
            context_start = max(0, line_index - 2)
            context_end = min(len(lines), line_index + 4)

            context = normalise_spaces(
                " ".join(lines[context_start:context_end])
            )

            explicit_subsection = ""

            if re.search(
                r"\bSUB\s*-?\s*SECTION\b",
                line,
                flags=re.IGNORECASE,
            ):
                explicit_subsection = extract_subsection_reference(line)

            if explicit_subsection:
                current_subsection = explicit_subsection

            detected_route = extract_route_reference(f"{line} {context}")

            if detected_route:
                current_route = detected_route

            if parsed_row is None:
                continue

            # A schedule row explicitly containing "Sub Section" establishes
            # the local zero K.P. for the following singularity rows.
            if explicit_subsection:
                current_subsection_start_abscissa = parsed_row["abscissa_m"]

            local_abscissa, calculation_log = calculate_local_abscissa(
                absolute_abscissa_m=parsed_row["abscissa_m"],
                subsection_start_abscissa_m=current_subsection_start_abscissa,
                subsection=current_subsection,
            )

            sector = metadata["sector"]
            section = metadata["section"]

            if current_subsection:
                subsection_parts = current_subsection.split("-")

                if len(subsection_parts) == 3:
                    sector = subsection_parts[0]
                    section = subsection_parts[1]

            records.append(
                {
                    "pdf_page": page_number,
                    "sector": sector,
                    "section": section,
                    "direction": metadata["direction"],
                    "subsection": current_subsection,
                    "route": current_route,
                    "row_number": parsed_row["row_number"],
                    "inter_singularity_m": parsed_row[
                        "inter_singularity_m"
                    ],
                    "absolute_abscissa_m": parsed_row["abscissa_m"],
                    "subsection_start_abscissa_m": (
                        current_subsection_start_abscissa
                    ),
                    "calculated_local_abscissa_m": local_abscissa,
                    "singularity_code": parsed_row["singularity_code"],
                    "singularity_name": parsed_row["remaining"],
                    "associated_variant": extract_associated_variant(context),
                    "calculation_log": calculation_log,
                    "source_evidence": context[:MAX_EVIDENCE_LENGTH],
                }
            )

    if not records:
        return pd.DataFrame(columns=SINGULARITY_COLUMNS)

    dataframe = pd.DataFrame(records)

    dataframe = dataframe.drop_duplicates(
        subset=[
            "pdf_page",
            "row_number",
            "absolute_abscissa_m",
            "singularity_code",
        ]
    )

    dataframe = dataframe.sort_values(
        by=[
            "sector",
            "section",
            "subsection",
            "absolute_abscissa_m",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe[SINGULARITY_COLUMNS]


def extract_variant_composition_table(pages: List[str]) -> pd.DataFrame:
    """
    Extract structured information from Variant Composition pages.

    Expected information:
    - Sub-section
    - Route
    - Rank
    - Participating rank / variants
    - Anticipated rank / variants
    """
    records: List[Dict[str, Any]] = []

    for page_number, page_text in enumerate(pages, start=1):
        if not is_variant_composition_page(page_text):
            continue

        metadata = page_metadata(page_text)

        lines = [
            normalise_spaces(line)
            for line in page_text.splitlines()
            if normalise_spaces(line)
        ]

        current_subsection = ""
        current_route = ""

        for line_index, line in enumerate(lines):
            detected_subsection = extract_subsection_reference(line)

            if detected_subsection:
                current_subsection = detected_subsection

            detected_route = extract_route_reference(line)

            if detected_route:
                current_route = detected_route

            context_start = max(0, line_index - 3)
            context_end = min(len(lines), line_index + 5)

            context = normalise_spaces(
                " ".join(lines[context_start:context_end])
            )

            context_upper = context.upper()

            relevant_markers = [
                "RANK",
                "PARTICIPATING",
                "ANTICIPATED",
                "ASSOCIATED VARIANT",
                "VARIANT COMPOSITION",
                "VARIANTS COMPOSITION",
            ]

            if not any(marker in context_upper for marker in relevant_markers):
                continue

            rank = extract_rank_value(context)

            participating_rank = extract_rank_value(
                context,
                rank_type="PARTICIPATING",
            )

            anticipated_rank = extract_rank_value(
                context,
                rank_type="ANTICIPATED",
            )

            participating_variants = extract_variants_by_type(
                context,
                "PARTICIPATING",
            )

            anticipated_variants = extract_variants_by_type(
                context,
                "ANTICIPATED",
            )

            all_variants = extract_associated_variant(context)

            # Do not add meaningless heading-only rows.
            if not any(
                [
                    rank,
                    participating_rank,
                    anticipated_rank,
                    participating_variants,
                    anticipated_variants,
                    all_variants,
                ]
            ):
                continue

            sector = metadata["sector"]
            section = metadata["section"]

            if current_subsection:
                subsection_parts = current_subsection.split("-")

                if len(subsection_parts) == 3:
                    sector = subsection_parts[0]
                    section = subsection_parts[1]

            records.append(
                {
                    "pdf_page": page_number,
                    "sector": sector,
                    "section": section,
                    "direction": metadata["direction"],
                    "subsection": current_subsection,
                    "route": current_route,
                    "rank": rank,
                    "participating_rank": participating_rank,
                    "anticipated_rank": anticipated_rank,
                    "participating_variants": participating_variants,
                    "anticipated_variants": anticipated_variants,
                    "all_variants": all_variants,
                    "source_evidence": context[:MAX_EVIDENCE_LENGTH],
                }
            )

    if not records:
        return pd.DataFrame(columns=VARIANT_COMPOSITION_COLUMNS)

    dataframe = pd.DataFrame(records)

    dataframe = dataframe.drop_duplicates(
        subset=[
            "pdf_page",
            "subsection",
            "route",
            "rank",
            "participating_rank",
            "anticipated_rank",
            "participating_variants",
            "anticipated_variants",
        ]
    )

    dataframe = dataframe.sort_values(
        by=[
            "sector",
            "section",
            "subsection",
            "route",
            "pdf_page",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe[VARIANT_COMPOSITION_COLUMNS]


def create_organised_source_log(
    singularities: pd.DataFrame,
    variant_composition: pd.DataFrame,
) -> str:
    lines = [
        "=" * 120,
        "ORGANISED SUBDIVISION / VARIANT COMPOSITION EXTRACTION LOG",
        "=" * 120,
        "",
        "LOCAL ABSCISSA CALCULATION",
        "Local Abscissa = Absolute Singularity K.P. - Sub-section Start K.P.",
        "",
        "=" * 120,
        "TABLE 1 - SINGULARITIES",
        "=" * 120,
    ]

    if singularities.empty:
        lines.append("[No singularity rows extracted]")
    else:
        display_columns = [
            "pdf_page",
            "subsection",
            "route",
            "row_number",
            "absolute_abscissa_m",
            "subsection_start_abscissa_m",
            "calculated_local_abscissa_m",
            "singularity_code",
            "singularity_name",
            "associated_variant",
        ]

        lines.append(
            singularities[display_columns].to_string(
                index=False,
                max_colwidth=80,
            )
        )

    lines.extend(
        [
            "",
            "=" * 120,
            "TABLE 2 - VARIANT COMPOSITION",
            "=" * 120,
        ]
    )

    if variant_composition.empty:
        lines.append("[No Variant Composition rows extracted]")
    else:
        display_columns = [
            "pdf_page",
            "subsection",
            "route",
            "rank",
            "participating_rank",
            "anticipated_rank",
            "participating_variants",
            "anticipated_variants",
            "all_variants",
        ]

        lines.append(
            variant_composition[display_columns].to_string(
                index=False,
                max_colwidth=80,
            )
        )

    return "\n".join(lines)
# =============================================================================
# MAIN REGISTER EXTRACTION
# =============================================================================

def extract_test_register_from_pdf(
    pages: List[str],
    progress_bar: Any,
    progress_text: Any,
) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    total_pages = len(pages)

    for page_number, page_text in enumerate(pages, start=1):
        progress_text.info(
            f"Reading and analysing page {page_number} of {total_pages}..."
        )

        progress_bar.progress(int(page_number / total_pages * 100))

        if not is_subdivision_page(page_text):
            continue

        metadata = page_metadata(page_text)

        lines = [
            normalise_spaces(line)
            for line in page_text.splitlines()
            if normalise_spaces(line)
        ]

        current_subsection = ""
        current_subsection_start_abscissa: Optional[float] = None

        for line_index, line in enumerate(lines):
            parsed_row = parse_singularity_row(line)

            context_start = max(0, line_index - 3)
            context_end = min(len(lines), line_index + 5)

            context = normalise_spaces(
                " ".join(lines[context_start:context_end])
            )

            detected_subsection = extract_subsection_reference(
                f"{line} {context}"
            )

            # A sub-section start is normally a schedule row containing:
            # "Sub Section 09-01-1 ..."
            # The associated absolute k.p. becomes the local zero point.
            if detected_subsection and parsed_row is not None:
                line_upper = line.upper()
                context_upper = context.upper()

                if (
                    "SUB SECTION" in line_upper
                    or "SUB-SECTION" in line_upper
                    or "SUB SECTION" in context_upper
                    or "SUB-SECTION" in context_upper
                ):
                    current_subsection = detected_subsection
                    current_subsection_start_abscissa = parsed_row["abscissa_m"]

            elif detected_subsection and not current_subsection:
                current_subsection = detected_subsection

            if parsed_row is None:
                continue

            matched_tests = identify_tests(context)

            if not matched_tests:
                continue

            sector = metadata["sector"]
            section = metadata["section"]

            if current_subsection:
                subsection_parts = current_subsection.split("-")

                if len(subsection_parts) == 3:
                    sector = subsection_parts[0]
                    section = subsection_parts[1]

            local_abscissa, calculation_log = calculate_local_abscissa(
                absolute_abscissa_m=parsed_row["abscissa_m"],
                subsection_start_abscissa_m=current_subsection_start_abscissa,
                subsection=current_subsection,
            )

            for test_code in matched_tests:
                test_rule = TEST_RULES[test_code]

                records.append(
                    {
                        "test_code": test_code,
                        "test_id": test_rule["id"],
                        "test_name": test_rule["name"],
                        "sector": sector,
                        "section": section,
                        "subsection": current_subsection,
                        "branch": current_subsection,
                        "direction": metadata["direction"],

                        # Test-sheet-local calculated abscissa.
                        "abscissa_m": local_abscissa,

                        # Original schedule/source absolute chainage.
                        "absolute_abscissa_m": parsed_row["abscissa_m"],

                        # Chainage of the active section start.
                        "subsection_start_abscissa_m": (
                            current_subsection_start_abscissa
                        ),

                        "inter_singularity_m": parsed_row[
                            "inter_singularity_m"
                        ],
                        "row_number": parsed_row["row_number"],
                        "singularity_code": parsed_row[
                            "singularity_code"
                        ],
                        "event_name": infer_event_name(context),
                        "associated_variant": extract_associated_variant(
                            context
                        ),
                        "location_name": extract_location_name(context),
                        "source_page": page_number,
                        "source_evidence": context[:MAX_EVIDENCE_LENGTH],
                        "calculation_log": calculation_log,
                        "review_status": "To be reviewed",
                        "remarks": (
                            "Generated from Subdivision PDF/OCR. "
                            "Engineering verification is mandatory before use."
                        ),
                    }
                )

    progress_bar.progress(100)

    if not records:
        return pd.DataFrame(columns=REGISTER_COLUMNS)

    dataframe = pd.DataFrame(records)

    dataframe = dataframe.drop_duplicates(
        subset=[
            "test_code",
            "source_page",
            "row_number",
            "absolute_abscissa_m",
            "subsection",
        ]
    )

    dataframe = dataframe.sort_values(
        by=[
            "sector",
            "section",
            "subsection",
            "absolute_abscissa_m",
            "test_code",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe[REGISTER_COLUMNS]


def apply_test_details(register: pd.DataFrame) -> pd.DataFrame:
    output = register.copy()

    for index in output.index:
        test_code = safe_text(output.at[index, "test_code"])

        if test_code in TEST_RULES:
            output.at[index, "test_id"] = TEST_RULES[test_code]["id"]
            output.at[index, "test_name"] = TEST_RULES[test_code]["name"]
        else:
            output.at[index, "test_id"] = ""
            output.at[index, "test_name"] = ""

    return output


# =============================================================================
# TEXT EXPORT
# =============================================================================

def create_text_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> str:
    test_code = safe_text(row.get("test_code"))
    test_rule = TEST_RULES.get(test_code)

    if test_rule is None:
        raise ValueError(f"Unknown test code: {test_code}")

    lines = [
        "=" * 90,
        "ALSTOM",
        project_info["procedure_title"],
        "",
        f"{test_rule['name']} ({test_rule['id']})",
        "=" * 90,
        "",
        f"Project / Contract: {project_info['project']}",
        f"Procedure Reference: {project_info['procedure_reference']}",
        f"Test Sheet No.: {sheet_number}",
        f"Review Status: {safe_text(row.get('review_status'))}",
        "",
        "-" * 90,
        "LOCATION / SOURCE DATA",
        "-" * 90,
        f"Test Code: {test_code}",
        f"Sector: {safe_text(row.get('sector'))}",
        f"Section: {safe_text(row.get('section'))}",
        f"Sub-section: {safe_text(row.get('subsection'))}",
        f"Branch Reference: {safe_text(row.get('branch'))}",
        f"Direction: {safe_text(row.get('direction'))}",
        f"Event: {safe_text(row.get('event_name'))}",
        f"Location / Asset: {safe_text(row.get('location_name'))}",
        f"Associated Variant: {safe_text(row.get('associated_variant'))}",
        f"Source PDF Page: {safe_text(row.get('source_page'))}",
        f"Singularity Row: {safe_text(row.get('row_number'))}",
        f"Singularity Code: {safe_text(row.get('singularity_code'))}",
        "",
        "-" * 90,
        "ABSCISSA CALCULATION",
        "-" * 90,
        f"Absolute Schedule Chainage: {format_distance(row.get('absolute_abscissa_m'))}",
        f"Sub-section Start Chainage: {format_distance(row.get('subsection_start_abscissa_m'))}",
        f"Calculated Local Abscissa: {format_distance(row.get('abscissa_m'))}",
        f"Calculation Log: {safe_text(row.get('calculation_log'))}",
        "",
        "-" * 90,
        "TEST CHECKS",
        "-" * 90,
    ]

    for sequence, (action, expected, unit) in enumerate(
        test_rule["checks"],
        start=1,
    ):
        lines.extend(
            [
                f"{sequence}. Check / Test Action: {action}",
                f"   Expected Requirement: {expected}",
                f"   Actual Result / Record ({unit}): ______________________________",
                "   Result: [ ] Y   [ ] N   [ ] N/A",
                "",
            ]
        )

    lines.extend(
        [
            "-" * 90,
            "SOURCE EVIDENCE",
            "-" * 90,
            safe_text(row.get("source_evidence")),
            "",
            "-" * 90,
            "ENGINEERING REMARKS",
            "-" * 90,
            safe_text(row.get("remarks")),
            "",
            "-" * 90,
            "APPROVAL",
            "-" * 90,
            "Test Result: [ ] Passed   [ ] Failed",
            "",
            "Alstom Name: __________________________",
            "Alstom Signature: _____________________",
            "Alstom Date: __________________________",
            "",
            "MTR Name: _____________________________",
            "MTR Signature: ________________________",
            "MTR Date: _____________________________",
            "",
            "UNCONTROLLED WHEN PRINTED – Verify approved project-specific data before use.",
        ]
    )

    return "\n".join(lines)


def create_register_text(
    register: pd.DataFrame,
    project_info: Dict[str, str],
) -> str:
    lines = [
        "=" * 110,
        "ATC LOW-SPEED DYNAMIC TEST REGISTER",
        "=" * 110,
        f"Project / Contract: {project_info['project']}",
        f"Procedure Reference: {project_info['procedure_reference']}",
        f"Procedure Title: {project_info['procedure_title']}",
        f"Generated Date: {date.today()}",
        f"Number of Records: {len(register)}",
        "",
    ]

    for sequence, (_, row) in enumerate(register.iterrows(), start=1):
        lines.extend(
            [
                "=" * 110,
                f"RECORD {sequence}",
                "=" * 110,
                f"Test: {safe_text(row.get('test_name'))}",
                f"Test Code: {safe_text(row.get('test_code'))}",
                f"Test ID: {safe_text(row.get('test_id'))}",
                f"Review Status: {safe_text(row.get('review_status'))}",
                f"Sector / Section / Sub-section: "
                f"{safe_text(row.get('sector'))} / "
                f"{safe_text(row.get('section'))} / "
                f"{safe_text(row.get('subsection'))}",
                f"Direction: {safe_text(row.get('direction'))}",
                f"Event: {safe_text(row.get('event_name'))}",
                f"Associated Variant: {safe_text(row.get('associated_variant'))}",
                f"Source Page / Row: "
                f"{safe_text(row.get('source_page'))} / "
                f"{safe_text(row.get('row_number'))}",
                f"Absolute Chainage: {format_distance(row.get('absolute_abscissa_m'))}",
                f"Sub-section Start Chainage: "
                f"{format_distance(row.get('subsection_start_abscissa_m'))}",
                f"Calculated Local Abscissa: {format_distance(row.get('abscissa_m'))}",
                f"Calculation: {safe_text(row.get('calculation_log'))}",
                "",
                "Source Evidence:",
                safe_text(row.get("source_evidence")),
                "",
            ]
        )

    return "\n".join(lines)


# =============================================================================
# DOCX EXPORT
# =============================================================================

def set_cell_text(cell: Any, value: Any, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(value))
    run.bold = bold


def add_docx_row(table: Any, values: List[Any], bold: bool = False) -> None:
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(cells[index], value, bold=bold)


def set_table_font_size(table: Any, point_size: int = 8) -> None:
    from docx.shared import Pt

    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(point_size)


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> bytes:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing python-docx. Install python-docx>=1.1.2."
        ) from exc

    test_code = safe_text(row.get("test_code"))
    test_rule = TEST_RULES.get(test_code)

    if test_rule is None:
        raise ValueError(f"Unknown test code: {test_code}")

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.35)
    section.right_margin = Inches(0.35)

    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(8)

    header = document.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = header.add_run("ALSTOM\n")
    run.bold = True
    run.font.size = Pt(16)

    run = header.add_run(project_info["procedure_title"])
    run.bold = True
    run.font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = title.add_run(f"{test_rule['name']} ({test_rule['id']})")
    run.bold = True
    run.font.size = Pt(12)

    information_table = document.add_table(rows=0, cols=6)
    information_table.style = "Table Grid"

    add_docx_row(
        information_table,
        [
            "Project / Contract:",
            project_info["project"],
            "Procedure Ref:",
            project_info["procedure_reference"],
            "Sheet No.:",
            sheet_number,
        ],
    )

    add_docx_row(
        information_table,
        [
            "Test Code:",
            test_code,
            "Test ID:",
            test_rule["id"],
            "Review Status:",
            safe_text(row.get("review_status")),
        ],
    )

    set_table_font_size(information_table)

    document.add_paragraph()

    location_table = document.add_table(rows=0, cols=4)
    location_table.style = "Table Grid"

    location_rows = [
        ["Sector:", row.get("sector"), "Direction:", row.get("direction")],
        ["Section:", row.get("section"), "Sub-section:", row.get("subsection")],
        ["Branch:", row.get("branch"), "Event:", row.get("event_name")],
        ["Location / Asset:", row.get("location_name"), "Variant:", row.get("associated_variant")],
        ["Absolute Chainage:", format_distance(row.get("absolute_abscissa_m")), "Sub-section Start:", format_distance(row.get("subsection_start_abscissa_m"))],
        ["Calculated Abscissa:", format_distance(row.get("abscissa_m")), "Source Page:", row.get("source_page")],
    ]

    for values in location_rows:
        add_docx_row(location_table, values)

    set_table_font_size(location_table)

    document.add_paragraph()

    calculation_title = document.add_paragraph()
    calculation_title.add_run("Abscissa Calculation / Audit Log").bold = True

    calculation_table = document.add_table(rows=1, cols=1)
    calculation_table.style = "Table Grid"

    set_cell_text(
        calculation_table.rows[0].cells[0],
        safe_text(row.get("calculation_log")),
    )

    set_table_font_size(calculation_table)

    document.add_paragraph()

    test_table = document.add_table(rows=1, cols=4)
    test_table.style = "Table Grid"

    headers = [
        "Check / Test Action",
        "Expected Requirement",
        "Actual Result / Record",
        "OK?",
    ]

    for index, header_text in enumerate(headers):
        set_cell_text(test_table.rows[0].cells[index], header_text, bold=True)

    for action, expected, unit in test_rule["checks"]:
        add_docx_row(
            test_table,
            [
                action,
                expected,
                f"{unit}: ",
                "☐ Y   ☐ N   ☐ N/A",
            ],
        )

    set_table_font_size(test_table)

    document.add_paragraph()

    evidence_title = document.add_paragraph()
    evidence_title.add_run("Source Evidence / Engineering Remarks").bold = True

    evidence_table = document.add_table(rows=1, cols=1)
    evidence_table.style = "Table Grid"

    evidence = (
        f"Source PDF Page: {safe_text(row.get('source_page'))}\n"
        f"Singularity Row: {safe_text(row.get('row_number'))}\n"
        f"Singularity Code: {safe_text(row.get('singularity_code'))}\n"
        f"Calculation: {safe_text(row.get('calculation_log'))}\n\n"
        f"PDF/OCR Source Evidence:\n"
        f"{safe_text(row.get('source_evidence'))}\n\n"
        f"Engineering Remarks:\n"
        f"{safe_text(row.get('remarks'))}"
    )

    set_cell_text(evidence_table.rows[0].cells[0], evidence)
    set_table_font_size(evidence_table)

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

    set_table_font_size(approval_table)

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – Verify approved project-specific data before use."
    )
    run.italic = True
    run.font.size = Pt(7)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_export_zip(
    register: pd.DataFrame,
    project_info: Dict[str, str],
    pdf_text_log: str,
    singularities: pd.DataFrame,
    variant_composition: pd.DataFrame,
    organised_source_log: str,
    progress_bar: Any,
    progress_text: Any,
) -> bytes:
    """
    ZIP contains:
    - DOCX test sheets
    - equivalent TXT test sheets
    - full register TXT export
    - CSV register
    """
    output = io.BytesIO()
    total = len(register)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "PDF_Text_Extraction_Log.txt",
            safe_text(pdf_text_log),
        )
        archive.writestr(
            "Organised_Source_Extraction_Log.txt",
            safe_text(organised_source_log),
        )

        archive.writestr(
            "Singularities.csv",
            singularities.to_csv(index=False),
        )

        archive.writestr(
            "Variant_Composition.csv",
            variant_composition.to_csv(index=False),
        )
        archive.writestr(
            "ATC_Low_Speed_Test_Register.txt",
            create_register_text(register, project_info),
        )

        archive.writestr(
            "ATC_Low_Speed_Test_Register.csv",
            register.to_csv(index=False),
        )

        for sequence, (_, row) in enumerate(register.iterrows(), start=1):
            test_code = safe_text(row.get("test_code"))
            subsection = safe_text(row.get("subsection")) or "NO_SUBSECTION"

            sheet_number = f"{project_info['sheet_prefix']}-{sequence:03d}"

            progress_text.info(
                f"Generating export {sequence} of {total}: "
                f"{test_code} / {subsection}"
            )

            basename = clean_filename(
                f"{sheet_number}_{test_code}_{subsection}_"
                f"KP_{safe_text(row.get('abscissa_m'))}"
            )

            text_sheet = create_text_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            archive.writestr(f"{basename}.txt", text_sheet)

            document_bytes = create_docx_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            archive.writestr(f"{basename}.docx", document_bytes)

            progress_bar.progress(int(sequence / total * 100))

    progress_bar.progress(100)
    progress_text.success(
        f"Export completed: {total} DOCX and TXT test sheet(s)."
    )

    return output.getvalue()


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    project_info: Dict[str, str],
    singularities: Optional[pd.DataFrame] = None,
    variant_composition: Optional[pd.DataFrame] = None,
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        cover = pd.DataFrame(
            [
                ["Project / Contract", project_info["project"]],
                ["Procedure Reference", project_info["procedure_reference"]],
                ["Procedure Title", project_info["procedure_title"]],
                ["Generated Date", str(date.today())],
                ["Test Register Rows", len(register)],
                ["Extraction Method", "Native PDF text + OCR fallback"],
                [
                    "Abscissa Calculation",
                    "Local Abscissa = Absolute Chainage - Sub-section Start Chainage",
                ],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(writer, sheet_name="Cover", index=False)
        register.to_excel(writer, sheet_name="Test Register", index=False)

        calculation_columns = [
            "test_code",
            "test_name",
            "subsection",
            "source_page",
            "row_number",
            "absolute_abscissa_m",
            "subsection_start_abscissa_m",
            "abscissa_m",
            "calculation_log",
            "source_evidence",
        ]

        available_columns = [
            column
            for column in calculation_columns
            if column in register.columns
        ]

        register[available_columns].to_excel(
            writer,
            sheet_name="Calculation Log",
            index=False,
        )

        if singularities is not None:
            singularities.to_excel(
                writer,
                sheet_name="Singularities",
                index=False,
            )

        if variant_composition is not None:
            variant_composition.to_excel(
                writer,
                sheet_name="Variant Composition",
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
                ].width = min(max(maximum_length + 2, 12), 70)

    return output.getvalue()


# =============================================================================
# STREAMLIT APPLICATION
# =============================================================================

def initialise_test_sheet_state() -> None:
    defaults = {
        "test_sheet_pdf_hash": "",
        "test_sheet_register": pd.DataFrame(columns=REGISTER_COLUMNS),
        "test_sheet_page_count": 0,
        "test_sheet_export_zip": None,
        "test_sheet_pdf_text_log": "",

        "test_sheet_pdf_extraction_summary": pd.DataFrame(
            columns=[
                "pdf_page",
                "extraction_method",
                "character_count",
                "word_count",
                "text_preview",
            ]
        ),

        "test_sheet_singularities": pd.DataFrame(
            columns=SINGULARITY_COLUMNS
        ),
        "test_sheet_variant_composition": pd.DataFrame(
            columns=VARIANT_COMPOSITION_COLUMNS
        ),
        "test_sheet_organised_source_log": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_test_sheet_generator_app() -> None:
    initialise_test_sheet_state()

    st.title("ATC Low-Speed Dynamic Test Sheet Generator")

    st.warning(
        "Engineering control: this tool produces draft test sheets. "
        "All OCR output, calculated chainages, locations, variants, "
        "test applicability and approved project information must be "
        "engineering-reviewed before use."
    )

    st.info(
        "The extractor supports selectable PDF text and scanned/image-only "
        "Subdivision schedule pages. Image-only schedule pages are OCR processed."
    )

    with st.expander("Project Information", expanded=True):
        column_1, column_2 = st.columns(2)

        with column_1:
            project = st.text_input(
                "Project / Contract",
                key="test_sheet_project",
            )

            procedure_reference = st.text_input(
                "Procedure Reference",
                value=PROCEDURE_REFERENCE_DEFAULT,
                key="test_sheet_procedure_reference",
            )

        with column_2:
            procedure_title = st.text_input(
                "Procedure Title",
                value=PROCEDURE_TITLE_DEFAULT,
                key="test_sheet_procedure_title",
            )

            sheet_prefix = st.text_input(
                "Test Sheet Prefix",
                value=PROCEDURE_REFERENCE_DEFAULT,
                key="test_sheet_prefix",
            )

    project_info = {
        "project": project,
        "procedure_reference": procedure_reference,
        "procedure_title": procedure_title,
        "sheet_prefix": sheet_prefix or PROCEDURE_REFERENCE_DEFAULT,
    }

    st.markdown("## 1. Upload Subdivision PDF")

    uploaded_pdf = st.file_uploader(
        "Upload Appendix J / SACEM Subdivision PDF",
        type=["pdf"],
        accept_multiple_files=False,
        key="test_sheet_subdivision_pdf",
    )

    if uploaded_pdf is None:
        st.info(
            "Upload a SACEM Subdivision PDF containing singularity schedules."
        )
        return

    pdf_bytes = uploaded_pdf.getvalue()
    pdf_size_mb = len(pdf_bytes) / (1024 * 1024)

    if pdf_size_mb > MAX_PDF_SIZE_MB:
        st.error(
            f"PDF size is {pdf_size_mb:.1f} MB. "
            f"Maximum supported size is {MAX_PDF_SIZE_MB} MB."
        )
        return

    current_pdf_hash = get_file_hash(pdf_bytes)

    # Reset extraction output if the user uploads a different PDF.
    if current_pdf_hash != st.session_state["test_sheet_pdf_hash"]:
        st.session_state["test_sheet_pdf_hash"] = current_pdf_hash

        st.session_state["test_sheet_register"] = pd.DataFrame(
            columns=REGISTER_COLUMNS
        )
        st.session_state["test_sheet_page_count"] = 0
        st.session_state["test_sheet_export_zip"] = None
        st.session_state["test_sheet_pdf_text_log"] = ""
        st.session_state["test_sheet_singularities"] = pd.DataFrame(
            columns=SINGULARITY_COLUMNS
        )

        st.session_state["test_sheet_variant_composition"] = pd.DataFrame(
            columns=VARIANT_COMPOSITION_COLUMNS
        )

        st.session_state["test_sheet_organised_source_log"] = ""
        st.session_state["test_sheet_pdf_extraction_summary"] = pd.DataFrame(
            columns=[
                "pdf_page",
                "extraction_method",
                "character_count",
                "word_count",
                "text_preview",
            ]
        )

    # -------------------------------------------------------------------------
    # EXTRACTION BUTTON
    # -------------------------------------------------------------------------
    if st.button(
        "Extract Low-Speed Test Points",
        type="primary",
        use_container_width=True,
        key="test_sheet_extract_button",
    ):
        progress_text = st.empty()
        progress_bar = st.progress(0)

        try:
            progress_text.info(
                "Reading PDF pages. Scanned/image-only pages will be OCR processed..."
            )

            # -----------------------------------------------------------------
            # 1. Extract native PDF text or OCR text from every PDF page.
            # -----------------------------------------------------------------
            pages, extracted_pages = extract_pdf_text_pages(pdf_bytes)

            st.session_state["test_sheet_page_count"] = len(pages)

            # Complete raw native/OCR extraction audit log.
            st.session_state["test_sheet_pdf_text_log"] = (
                create_pdf_text_log(extracted_pages)
            )

            # Short per-page audit summary.
            st.session_state["test_sheet_pdf_extraction_summary"] = (
                create_pdf_extraction_summary(extracted_pages)
            )

            # -----------------------------------------------------------------
            # 2. Extract all Singularities schedule rows.
            #
            # This table retains:
            # - Sub-section reference
            # - Absolute K.P.
            # - Sub-section-start K.P.
            # - Calculated local abscissa
            # - Singularity code/name
            # - Associated variant/source evidence
            # -----------------------------------------------------------------
            progress_text.info(
                "Extracting Singularities schedule rows and calculating "
                "local abscissae..."
            )

            singularities = extract_singularities_table(pages)

            st.session_state["test_sheet_singularities"] = singularities

            # -----------------------------------------------------------------
            # 3. Extract Variant Composition records.
            #
            # This table retains:
            # - Sub-section
            # - Route
            # - Rank
            # - Participating rank / variants
            # - Anticipated rank / variants
            # -----------------------------------------------------------------
            progress_text.info(
                "Extracting Variant Composition records, routes and ranks..."
            )

            variant_composition = extract_variant_composition_table(pages)

            st.session_state["test_sheet_variant_composition"] = (
                variant_composition
            )

            # -----------------------------------------------------------------
            # 4. Create readable text table / audit log.
            # -----------------------------------------------------------------
            st.session_state["test_sheet_organised_source_log"] = (
                create_organised_source_log(
                    singularities=singularities,
                    variant_composition=variant_composition,
                )
            )

            # -----------------------------------------------------------------
            # 5. Extract applicable low-speed dynamic test points.
            # -----------------------------------------------------------------
            progress_text.info(
                f"PDF loaded: {len(pages)} page(s). "
                "Identifying low-speed test points..."
            )

            register = extract_test_register_from_pdf(
                pages=pages,
                progress_bar=progress_bar,
                progress_text=progress_text,
            )

            st.session_state["test_sheet_register"] = register
            st.session_state["test_sheet_export_zip"] = None

            progress_bar.progress(100)

            # -----------------------------------------------------------------
            # 6. Final status message.
            # -----------------------------------------------------------------
            summary_message = (
                f"PDF pages: {len(pages)} | "
                f"Singularity rows: {len(singularities)} | "
                f"Variant Composition records: {len(variant_composition)} | "
                f"Low-Speed test records: {len(register)}"
            )

            if register.empty:
                progress_text.warning(
                    "Extraction completed, but no low-speed test points were "
                    "identified. Review the raw OCR text and organised source "
                    f"tables. {summary_message}"
                )
            else:
                progress_text.success(
                    f"Extraction completed successfully. {summary_message}"
                )

        except pytesseract.TesseractNotFoundError:
            progress_bar.empty()

            progress_text.error(
                "Tesseract OCR is not installed or is not available in PATH. "
                "Install tesseract-ocr in the deployment environment and "
                "restart the application."
            )

        except fitz.FileDataError:
            progress_bar.empty()

            progress_text.error(
                "The uploaded file cannot be opened as a valid PDF. "
                "Please upload a readable, non-corrupted PDF."
            )

        except Exception as exc:
            progress_bar.empty()

            progress_text.error(
                f"PDF extraction failed: {type(exc).__name__}: {exc}"
            )

            st.exception(exc)

    # -------------------------------------------------------------------------
    # EXTRACTED TEXT LOG
    # This is intentionally positioned directly BELOW the extraction button.
    # It is shown whether or not test points were found.
    # -------------------------------------------------------------------------
    pdf_text_log = st.session_state.get(
        "test_sheet_pdf_text_log",
        "",
    )

    pdf_extraction_summary = st.session_state.get(
        "test_sheet_pdf_extraction_summary",
        pd.DataFrame(),
    )

    # Always retrieve source tables from session state here.
    # They are needed later for Excel export, even if the raw OCR log is empty.
    singularities = st.session_state.get(
        "test_sheet_singularities",
        pd.DataFrame(columns=SINGULARITY_COLUMNS),
    )

    variant_composition = st.session_state.get(
        "test_sheet_variant_composition",
        pd.DataFrame(columns=VARIANT_COMPOSITION_COLUMNS),
    )

    organised_source_log = st.session_state.get(
        "test_sheet_organised_source_log",
        "",
    )

    if pdf_text_log:
        st.divider()
        st.markdown("### Extracted PDF Text / OCR Log")

        st.caption(
            "This section shows the complete text extracted from the uploaded "
            "PDF. Each page is identified as native PDF text extraction or OCR "
            "extraction. Review this output if expected test points are missing."
        )

        if not pdf_extraction_summary.empty:
            st.markdown("#### Per-Page Extraction Summary")

            st.dataframe(
                pdf_extraction_summary,
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("### Organised Source Tables")

        tab_1, tab_2, tab_3 = st.tabs(
            [
                "Singularities",
                "Variant Composition",
                "Organised TXT Log",
            ]
        )

        with tab_1:
            st.caption(
                "Each singularity is shown with original absolute K.P., "
                "detected sub-section start K.P. and calculated local abscissa."
            )

            st.dataframe(
                singularities,
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                "Download Singularities CSV",
                data=singularities.to_csv(index=False).encode("utf-8"),
                file_name=clean_filename(
                    f"{project_info['sheet_prefix']}_Singularities.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )

        with tab_2:
            st.caption(
                "Variant Composition records are grouped using the detected "
                "sub-section, route, rank, participating and anticipated variant data."
            )

            st.dataframe(
                variant_composition,
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                "Download Variant Composition CSV",
                data=variant_composition.to_csv(index=False).encode("utf-8"),
                file_name=clean_filename(
                    f"{project_info['sheet_prefix']}_Variant_Composition.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )

        with tab_3:
            st.text_area(
                "Organised source extraction log",
                value=organised_source_log,
                height=600,
                disabled=True,
            )

            st.download_button(
                "Download Organised Source Log",
                data=organised_source_log.encode("utf-8"),
                file_name=clean_filename(
                    f"{project_info['sheet_prefix']}_Organised_Source_Log.txt"
                ),
                mime="text/plain",
                use_container_width=True,
            )

        st.download_button(
            "Download Full Extracted PDF Text / OCR Log",
            data=pdf_text_log.encode("utf-8"),
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_PDF_Text_Extraction_Log.txt"
            ),
            mime="text/plain",
            use_container_width=True,
            key="test_sheet_download_pdf_text_log",
        )

        with st.expander(
            "Show Full Extracted PDF Text",
            expanded=True,
        ):
            st.text_area(
                "Full extracted PDF text",
                value=pdf_text_log,
                height=650,
                disabled=True,
                key="test_sheet_pdf_text_preview",
            )

        st.divider()

    # -------------------------------------------------------------------------
    # TEST REGISTER
    # -------------------------------------------------------------------------
    register = st.session_state["test_sheet_register"]

    if register.empty:
        st.warning(
            "Click 'Extract Low-Speed Test Points' to create the draft register. "
            "The extracted PDF/OCR text log above can still be reviewed and "
            "downloaded even if no test points were detected."
        )
        return

    metric_1, metric_2, metric_3 = st.columns(3)

    metric_1.metric("Draft Test Sheets", len(register))
    metric_2.metric("PDF Pages", st.session_state["test_sheet_page_count"])
    metric_3.metric("Test Types Detected", register["test_code"].nunique())

    st.markdown("## 2. Review and Approve Test Register")

    reviewed_register = st.data_editor(
        register,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="test_sheet_register_editor",
        column_config={
            "test_code": st.column_config.SelectboxColumn(
                "Test Code",
                options=list(TEST_RULES.keys()),
                required=True,
            ),
            "abscissa_m": st.column_config.NumberColumn(
                "Calculated Local Abscissa (m)",
                format="%.3f",
            ),
            "absolute_abscissa_m": st.column_config.NumberColumn(
                "Absolute Schedule Chainage (m)",
                format="%.3f",
            ),
            "subsection_start_abscissa_m": st.column_config.NumberColumn(
                "Sub-section Start Chainage (m)",
                format="%.3f",
            ),
            "inter_singularity_m": st.column_config.NumberColumn(
                "Inter-Singularity (m)",
                format="%.3f",
            ),
            "review_status": st.column_config.SelectboxColumn(
                "Review Status",
                options=[
                    "To be reviewed",
                    "Engineering review required",
                    "Approved for generation",
                    "Not applicable",
                ],
            ),
            "source_evidence": st.column_config.TextColumn(
                "Source Evidence",
                width="large",
            ),
            "calculation_log": st.column_config.TextColumn(
                "Calculation Log",
                width="large",
            ),
            "remarks": st.column_config.TextColumn(
                "Engineering Remarks",
                width="large",
            ),
        },
    )

    reviewed_register = apply_test_details(reviewed_register)
    st.session_state["test_sheet_register"] = reviewed_register

    st.markdown("### Abscissa Calculation and OCR Audit Log")

    calculation_columns = [
        "test_code",
        "test_name",
        "subsection",
        "source_page",
        "row_number",
        "absolute_abscissa_m",
        "subsection_start_abscissa_m",
        "abscissa_m",
        "calculation_log",
        "source_evidence",
    ]

    available_calculation_columns = [
        column
        for column in calculation_columns
        if column in reviewed_register.columns
    ]

    st.dataframe(
        reviewed_register[available_calculation_columns],
        use_container_width=True,
        hide_index=True,
    )

    approved_register = reviewed_register[
        reviewed_register["review_status"]
        .eq("Approved for generation")
        & reviewed_register["test_code"].isin(TEST_RULES.keys())
    ].copy()

    st.markdown("## 3. Download Register, Text Logs and Test Sheets")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        project_info=project_info,
        singularities=singularities,
        variant_composition=variant_composition,
    )

    full_register_text = create_register_text(
        register=reviewed_register,
        project_info=project_info,
    )

    column_1, column_2, column_3, column_4 = st.columns(4)

    with column_1:
        st.download_button(
            "Download Excel Register",
            data=excel_bytes,
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_Subdivision_Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
        )

    with column_2:
        st.download_button(
            "Download CSV Register",
            data=reviewed_register.to_csv(index=False).encode("utf-8"),
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_Subdivision_Test_Register.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with column_3:
        st.download_button(
            "Download Full TXT Register / Audit Log",
            data=full_register_text.encode("utf-8"),
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_"
                f"Subdivision_Test_Register_and_Audit_Log.txt"
            ),
            mime="text/plain",
            use_container_width=True,
        )

    with column_4:
        if approved_register.empty:
            st.button(
                "Generate DOCX + TXT ZIP",
                disabled=True,
                use_container_width=True,
            )
        else:
            if st.button(
                f"Generate {len(approved_register)} DOCX + TXT Test Sheet(s)",
                type="primary",
                use_container_width=True,
            ):
                progress_text = st.empty()
                progress_bar = st.progress(0)

                try:
                    export_zip = create_export_zip(
                        register=approved_register,
                        project_info=project_info,
                        pdf_text_log=st.session_state.get(
                            "test_sheet_pdf_text_log",
                            "",
                        ),
                        progress_bar=progress_bar,
                        progress_text=progress_text,
                    )

                    st.session_state["test_sheet_export_zip"] = export_zip

                except Exception as exc:
                    progress_text.error(
                        f"Export generation failed: {type(exc).__name__}: {exc}"
                    )
                    st.exception(exc)

    export_zip = st.session_state.get("test_sheet_export_zip")

    if export_zip:
        st.download_button(
            "Download DOCX + TXT Test Sheets ZIP",
            data=export_zip,
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_ATC_Low_Speed_Test_Sheets.zip"
            ),
            mime="application/zip",
            use_container_width=True,
        )
