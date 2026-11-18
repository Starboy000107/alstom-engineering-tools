from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st
import os
import tempfile
import threading
from pathlib import Path
# =============================================================================
# CONFIGURATION
# =============================================================================

PROCEDURE_REFERENCE_DEFAULT = "C1252-TAC-0274"
PROCEDURE_TITLE_DEFAULT = "Trackside ATC Low-Speed Dynamic Test Procedure"

MAX_PDF_SIZE_MB = 100
MAX_EVIDENCE_LENGTH = 3000

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

TRANSMISSION_CALCULATION_COLUMNS = [
    "pdf_page",
    "sector",
    "section",
    "route_number",
    "route_name",
    "direction",
    "route_branches",
    "anticipated_branches",
    "entry_speed_kph",
    "initialisation_distance_m",
    "running_time_s",
    "dwell_time_s",
    "total_running_time_s",
    "loop_count",
    "message_elements",
    "transmission_time_s",
    "minimum_visibility_invariant_m",
    "minimum_visibility_variant_m",
    "braking_distance_invariant_m",
    "braking_distance_variant_m",
    "total_anticipation_invariant_m",
    "total_anticipation_variant_m",
    "downstream_request_invariant_m",
    "downstream_request_variant_m",
    "current_computed_invariant_m",
    "current_computed_variant_m",
    "comments",
    "source_evidence",
]
# =============================================================================
# MARKER-PDF CONFIGURATION
# =============================================================================

# Marker-PDF is the only extraction engine used by this application.
USE_MARKER_PDF = True
MARKER_REQUIRED = True

# Optional values: cpu, cuda, mps.
MARKER_DEVICE = os.getenv("MARKER_DEVICE", "").strip()

# Process each page independently to preserve original page references.
MARKER_PAGE_BY_PAGE = True

# Cached converter/model instances must not be used concurrently.
MARKER_CONVERTER_LOCK = threading.RLock()


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
# MARKER-PDF EXTRACTION BACKEND
# =============================================================================

def marker_is_installed() -> bool:
    """
    Return True when marker-pdf is installed and importable.

    Importing lazily is important so the whole Streamlit app can still start
    and show a helpful message if Marker is not installed correctly.
    """
    try:
        from marker.converters.pdf import PdfConverter  # noqa: F401
        from marker.models import create_model_dict  # noqa: F401
        return True
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def get_marker_converter() -> Any:
    """
    Load Marker models once per Streamlit server process.

    Marker can use GPU automatically where available. The optional
    MARKER_DEVICE environment variable may be used by deployment environments,
    for example:

        MARKER_DEVICE=cpu
        MARKER_DEVICE=cuda
        MARKER_DEVICE=mps
    """
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
    except Exception as exc:
        raise RuntimeError(
            "marker-pdf is not installed correctly. "
            "Install it with: pip install marker-pdf"
        ) from exc

    config: Dict[str, Any] = {}

    # Marker versions differ slightly. These values are optional and safely
    # ignored by versions that do not use them.
    if MARKER_DEVICE:
        config["device"] = MARKER_DEVICE

    try:
        converter = PdfConverter(
            artifact_dict=create_model_dict(),
            config=config,
        )
    except TypeError:
        # Compatibility with Marker versions where config is not accepted.
        converter = PdfConverter(
            artifact_dict=create_model_dict(),
        )

    return converter


def marker_rendered_to_markdown(rendered: Any) -> str:
    """
    Extract Markdown/text from multiple marker-pdf API versions.

    Different Marker releases return slightly different result objects. This
    compatibility function supports the common variants without tying the
    Streamlit app to only one Marker release.
    """
    if rendered is None:
        return ""

    if isinstance(rendered, str):
        return rendered.strip()

    for attribute_name in [
        "markdown",
        "text",
        "html",
        "output",
    ]:
        value = getattr(rendered, attribute_name, None)

        if isinstance(value, str) and value.strip():
            return value.strip()

    # Marker commonly exposes text_from_rendered().
    try:
        from marker.output import text_from_rendered

        output = text_from_rendered(rendered)

        if isinstance(output, str):
            return output.strip()

        if isinstance(output, tuple) and output:
            first_value = output[0]

            if isinstance(first_value, str):
                return first_value.strip()

    except Exception:
        pass

    return safe_text(rendered)


def marker_text_to_parser_text(markdown_text: str) -> str:
    """
    Convert Marker Markdown into parser-friendly engineering schedule text.

    The existing singularity parser is line-oriented. Marker often produces
    Markdown tables such as:

        | 1 | 0.0 m | 1177.5 | 1 | Sub Section: 9-1-0 |

    This function intentionally keeps each table row on its own line while
    removing Markdown-only symbols. Therefore your existing row parser can
    read the data as:

        1 0.0 m 1177.5 1 Sub Section: 9-1-0

    It does not remove hyphens because they are required for subsection and
    branch identifiers such as 09-01-0-0.
    """
    output_lines: List[str] = []

    for raw_line in safe_text(markdown_text).splitlines():
        line = safe_text(raw_line)

        if not line:
            continue

        # Remove Markdown headings only.
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)

        # Remove common Markdown emphasis marks.
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("`", "")

        # Convert Markdown tables into whitespace-separated schedule rows.
        line = re.sub(r"\s*\|\s*", " ", line)

        # Ignore Markdown separator rows such as: --- | --- | ---
        compact_line = re.sub(r"[\s:|_-]", "", line)

        if compact_line == "":
            continue

        # Ignore image links but retain their alt text when useful.
        line = re.sub(
            r"!\[([^\]]*)\]\([^)]+\)",
            r"\1",
            line,
        )

        # Convert ordinary Markdown links to visible link labels.
        line = re.sub(
            r"\[([^\]]+)\]\([^)]+\)",
            r"\1",
            line,
        )

        line = normalise_spaces(line)

        if line:
            output_lines.append(line)

    parser_text = "\n".join(output_lines)

    return repair_ocr_page_text(parser_text)


def create_single_page_pdf_bytes(
    source_document: fitz.Document,
    page_index: int,
) -> bytes:
    """
    Make a one-page PDF for Marker.

    Processing page-by-page has two important benefits for this application:
    1. Every extracted text block keeps its original PDF page number.
    2. Schedule pages, Variant Composition pages and calculation pages are
       not merged into one large Markdown document.
    """
    output_document = fitz.open()

    try:
        output_document.insert_pdf(
            source_document,
            from_page=page_index,
            to_page=page_index,
        )

        return output_document.tobytes(
            garbage=4,
            deflate=True,
        )

    finally:
        output_document.close()


def extract_one_page_with_marker(
    single_page_pdf_bytes: bytes,
    original_page_number: int,
) -> Dict[str, str]:
    """
    Extract one PDF page through Marker.

    A temporary file is used because Marker PdfConverter normally expects a
    file path rather than raw uploaded bytes.
    """
    converter = get_marker_converter()

    temporary_path = ""

    try:
        with tempfile.NamedTemporaryFile(
            suffix=f"_page_{original_page_number}.pdf",
            delete=False,
        ) as temporary_file:
            temporary_file.write(single_page_pdf_bytes)
            temporary_path = temporary_file.name

        with MARKER_CONVERTER_LOCK:
            rendered = converter(temporary_path)

        marker_markdown = marker_rendered_to_markdown(rendered)

        parser_text = marker_text_to_parser_text(marker_markdown)

        audit_text = "\n".join(
            [
                "=" * 100,
                f"MARKER-PDF OUTPUT - ORIGINAL PDF PAGE {original_page_number}",
                "=" * 100,
                "",
                "----- RAW MARKER MARKDOWN / TEXT -----",
                marker_markdown or "[NO MARKER TEXT EXTRACTED]",
                "",
                "----- PARSER-NORMALISED MARKER TEXT -----",
                parser_text or "[NO PARSER TEXT EXTRACTED]",
            ]
        )

        return {
            "parser_text": parser_text,
            "audit_text": audit_text,
        }

    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except Exception:
                pass


@st.cache_data(show_spinner=False, max_entries=4)
def extract_pdf_text_pages_with_marker(
    pdf_bytes: bytes,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Extract every PDF page using Marker-PDF only.

    No PyMuPDF native text extraction, Tesseract OCR, OpenCV preprocessing,
    image thresholding, or alternate OCR fallback is used.
    """
    source_document = fitz.open(
        stream=pdf_bytes,
        filetype="pdf",
    )

    pages: List[str] = []
    extracted_pages: List[Dict[str, Any]] = []

    try:
        for page_index in range(source_document.page_count):
            page_number = page_index + 1

            single_page_pdf = create_single_page_pdf_bytes(
                source_document=source_document,
                page_index=page_index,
            )

            marker_result = extract_one_page_with_marker(
                single_page_pdf_bytes=single_page_pdf,
                original_page_number=page_number,
            )

            parser_text = safe_text(
                marker_result.get("parser_text", "")
            )

            audit_text = safe_text(
                marker_result.get("audit_text", "")
            )

            pages.append(parser_text)

            extracted_pages.append(
                {
                    "page_number": page_number,
                    "extraction_method": "Marker-PDF Only",
                    "text": parser_text,
                    "audit_text": audit_text,
                }
            )

    finally:
        source_document.close()

    return pages, extracted_pages


@st.cache_data(show_spinner=False, max_entries=4)
def extract_pdf_text_pages(
    pdf_bytes: bytes,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Marker-PDF-only extraction entry point.

    Marker is mandatory. No Tesseract, OpenCV, PIL image OCR, or native PDF
    text extraction fallback is permitted.
    """
    if not marker_is_installed():
        raise RuntimeError(
            "marker-pdf is required but is not installed. "
            "Install it with: pip install marker-pdf"
        )

    try:
        return extract_pdf_text_pages_with_marker(pdf_bytes)

    except Exception as exc:
        raise RuntimeError(
            "Marker-PDF extraction failed. No fallback extraction engine is "
            "configured because this application is Marker-PDF only. "
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc

# =============================================================================
# SOURCE PAGE IDENTIFICATION
# =============================================================================

# =============================================================================
# PAGE TYPE IDENTIFICATION
# =============================================================================
def compact_ocr_text(value: Any) -> str:
    """
    Create compact uppercase text for robust heading matching.

    The name is retained for compatibility with existing parsers, although
    Marker-PDF is now the sole extraction engine.
    """
    return re.sub(
        r"[^A-Z0-9]+",
        "",
        safe_text(value).upper(),
    )


def make_spaced_word_pattern(word: str) -> str:
    clean_word = re.sub(r"[^A-Z0-9]", "", word.upper())

    if not clean_word:
        return r"$^"

    return (
        r"(?<![A-Z0-9])"
        + r"[\s._|/-]*".join(
            re.escape(character)
            for character in clean_word
        )
        + r"(?![A-Z0-9])"
    )


def repair_known_ocr_headings(text: str) -> str:
    """
    Conservative heading repair retained for imperfect Marker extraction.
    """
    repaired = safe_text(text)

    repairs = {
        "SINGULARITIES": "SINGULARITIES",
        "SINGULARITY": "SINGULARITY",
        "SAFETYVARIANTS": "SAFETY VARIANTS",
        "SAFETYVARIANTSMESSAGE": "SAFETY VARIANTS MESSAGE",
        "SAFETYVARIANTSMESSAGES": "SAFETY VARIANTS MESSAGES",
        "VARIANTSCOMPOSITION": "VARIANTS COMPOSITION",
        "VARIANTCOMPOSITION": "VARIANT COMPOSITION",
        "TRANSMISSIONTIME": "TRANSMISSION TIME",
        "ANTICIPATIONDISTANCE": "ANTICIPATION DISTANCE",
        "SUBSECTION": "SUB SECTION",
        "SUBSECT": "SUB-SECT.",
        "INTERSINGUL": "INTER SINGUL.",
        "TESTABSC": "TEST ABSC.",
        "NORMALDIRECTION": "NORMAL DIRECTION",
        "UPTRACK": "UP TRACK",
        "DOWNTRACK": "DOWN TRACK",
        "DNTRACK": "DN TRACK",
        "SPACINGSTOPPINGPOINT": "SPACING STOPPING POINT",
        "SPECIALSTOPPINGPOINT": "SPECIAL STOPPING POINT",
        "COMPLEXSTOPPINGPOINT": "COMPLEX STOPPING POINT",
        "MOVINGTRAININITIALISATION": "MOVING TRAIN INITIALISATION",
        "MOVINGTRAININITIALIZATION": "MOVING TRAIN INITIALIZATION",
        "STATIONARYTRAININITIALISATION": "STATIONARY TRAIN INITIALISATION",
        "STATIONARYTRAININITIALIZATION": "STATIONARY TRAIN INITIALIZATION",
        "EMERGENCYZONE": "EMERGENCY ZONE",
        "RETURNCHANNEL": "RETURN CHANNEL",
        "EXITSACEMROUTE": "EXIT SACEM ROUTE",
        "SUBSECTIONEND": "SUB SECTION END",
        "ASSOCIATEDVARIANT": "ASSOCIATED VARIANT",
        "ASSOCIATEDVARIANTS": "ASSOCIATED VARIANTS",
        "FIXEDCHARACTERISTICS": "FIXED CHARACTERISTICS",
        "STATIONCHARACTERISTICS": "STATION CHARACTERISTICS",
        "CURRENTCOMPUTED": "CURRENT COMPUTED",
    }

    for compact_word, replacement in repairs.items():
        repaired = re.sub(
            make_spaced_word_pattern(compact_word),
            replacement,
            repaired,
            flags=re.IGNORECASE,
        )

    return repaired


def repair_ocr_page_text(text: str) -> str:
    """
    Compatibility name retained for downstream parsing.

    Applies conservative cleanup to Marker-PDF output.
    """
    repaired_lines: List[str] = []

    for raw_line in safe_text(text).splitlines():
        line = normalise_spaces(raw_line)

        if not line:
            continue

        line = repair_known_ocr_headings(line)
        line = re.sub(r"\s+:", ":", line)
        line = re.sub(r"\(\s+", "(", line)
        line = re.sub(r"\s+\)", ")", line)

        repaired_lines.append(line)

    return "\n".join(repaired_lines)

def is_singularity_schedule_page(page_text: str) -> bool:
    """
    Detect Singularities schedule pages, including pages where the word
    SINGULARITIES was lost or badly OCR'd.

    The source PDF pages 1-7 are identified by schedule content, not only by
    one heading.
    """
    upper = normalise_spaces(page_text).upper()
    compact = compact_ocr_text(page_text)

    has_line_lar = "LINELAR" in compact

    has_sector_section = (
        "SECTOR" in compact
        and "SECTION" in compact
    )

    has_schedule_heading = any(
        marker in compact
        for marker in [
            "SINGULARITIES",
            "SINGULARITY",
            "SUBSECTIONHEADING",
            "SUBSECTION",
            "FIRSTVARIANTRANK",
        ]
    )

    has_schedule_content = any(
        marker in compact
        for marker in [
            "ASSOCIATEDVARIANT",
            "ASSOCIATEDVARIANTS",
            "BEACONTYPE",
            "FIXEDCHARACTERISTICS",
            "COMPENSATEDGRADIENT",
            "GRADIENTVARIATION",
            "SPACINGSTOPPINGPOINT",
            "SPECIALSTOPPINGPOINT",
            "PERMANENTSPEEDRESTRICTION",
            "MOVINGTRAININITIALISATION",
            "MOVINGTRAININITIALIZATION",
            "ESATEMPPOINT",
            "SUBSECTIONEND",
            "EXITSACEMROUTE",
            "REVERSESUBSECTION",
            "RETURNCHANNEL",
        ]
    )

    has_chainage = bool(
        re.search(
            r"\b-?\d{1,4}(?:,\d{3})?\.\d+\b",
            upper,
        )
    )

    is_transmission = (
        "TRANSMISSIONTIME" in compact
        and "ANTICIPATIONDISTANCE" in compact
    )

    is_variant = (
        "SAFETYVARIANTS" in compact
        or "VARIANTSCOMPOSITION" in compact
    )

    is_invariant = any(
        marker in compact
        for marker in [
            "INVARIANTSEMISSION",
            "EMISSIONONLOOP",
            "INVARIANTMESSAGES",
        ]
    )

    return (
        has_line_lar
        and has_sector_section
        and has_chainage
        and (has_schedule_heading or has_schedule_content)
        and not is_transmission
        and not is_variant
        and not is_invariant
    )


def is_transmission_calculation_page(page_text: str) -> bool:
    """
    Identify pages such as Appendix J pages 22 to 27:

        Transmission time and anticipation distance calculation sheet

    These pages contain route branches, anticipated branches, speed,
    transmission time and calculated anticipation distances.
    """
    upper = normalise_spaces(page_text).upper()

    required_markers = [
        "TRANSMISSION TIME",
        "ANTICIPATION DISTANCE",
    ]

    route_markers = [
        "FIRST ROUTE",
        "SECOND ROUTE",
        "THIRD ROUTE",
        "ANTICIPATED BRANCHES",
    ]

    return (
        all(marker in upper for marker in required_markers)
        and any(marker in upper for marker in route_markers)
    )

def is_variant_composition_page(page_text: str) -> bool:
    """
    Detect Variant Composition / Safety Variants Messages pages.

    Do not require the exact word RANK because OCR commonly loses it.
    """
    compact = compact_ocr_text(page_text)

    has_variant_heading = any(
        marker in compact
        for marker in [
            "SAFETYVARIANTS",
            "SAFETYVARIANTSMESSAGE",
            "SAFETYVARIANTSMESSAGES",
            "VARIANTSCOMPOSITION",
            "VARIANTCOMPOSITION",
        ]
    )

    has_subsection = bool(
        re.search(
            r"\b\d{1,2}\s*[- ]\s*\d{1,2}\s*[- ]\s*\d{1,2}\b",
            page_text,
        )
    )

    has_variant_content = any(
        marker in compact
        for marker in [
            "TUC",
            "THW",
            "TCE",
            "PEP",
            "PSD",
            "STATION",
            "POINT",
            "EMERGENCY",
            "TC",
            "RP",
            "NP",
            "SIG",
        ]
    )

    is_transmission = (
        "TRANSMISSIONTIME" in compact
        and "ANTICIPATIONDISTANCE" in compact
    )

    is_singularity = (
        "LINELAR" in compact
        and (
            "SINGULARITIES" in compact
            or "SUBSECTIONHEADING" in compact
        )
    )

    is_invariant = any(
        marker in compact
        for marker in [
            "INVARIANTSEMISSION",
            "EMISSIONONLOOP",
            "INVARIANTMESSAGES",
        ]
    )

    return (
        has_variant_heading
        and has_subsection
        and has_variant_content
        and not is_transmission
        and not is_singularity
        and not is_invariant
    )


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

# =============================================================================
# SUB-SECTION AND BRANCH IDENTIFIERS
# =============================================================================

def normalise_subsection_parts(
    sector: str,
    section: str,
    subsection: str,
) -> str:
    """
    Standard format:

        09-01-0
        09-03-1
    """
    return (
        f"{int(sector):02d}-"
        f"{int(section):02d}-"
        f"{int(subsection)}"
    )


def normalise_branch_parts(
    sector: str,
    section: str,
    subsection: str,
    branch: str,
) -> str:
    """
    Standard full branch format:

        09-01-0-0
        09-03-0-2
        14-04-1-4
    """
    return (
        f"{int(sector):02d}-"
        f"{int(section):02d}-"
        f"{int(subsection)}-"
        f"{int(branch)}"
    )


def extract_explicit_subsection_reference(text: str) -> str:
    """
    Extract a subsection only when explicitly prefixed by 'Sub Section'.

    Valid source examples:
        Sub Section : 9-1-0
        Sub Section: 09-01-1
        Sub-section 09-01-2 / 65 kHz
        Sub Section : 09-01-1-0

    Returned values:
        09-01-0
        09-01-1
        09-01-2

    The fourth component is deliberately ignored because it is a branch
    reference, not the subsection identifier.
    """
    text = normalise_spaces(text)

    match = re.search(
        r"\bSUB\s*-?\s*SECTION"
        r"(?:\s+(?:NUMBER|NO\.?))?"
        r"\s*[:=]?\s*"
        r"(?P<sector>\d{1,2})\s*[- ]\s*"
        r"(?P<section>\d{1,2})\s*[- ]\s*"
        r"(?P<subsection>\d{1,2})"
        r"(?:\s*[- ]\s*\d{1,2})?",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    try:
        return normalise_subsection_parts(
            sector=match.group("sector"),
            section=match.group("section"),
            subsection=match.group("subsection"),
        )
    except ValueError:
        return ""


def extract_branch_references(text: str) -> List[str]:
    """
    Extract four-part branch identifiers.

    Source examples:
        09-01-0-0, 09-01-1-0, 09-01-2-0
        09-03-0-0, 09-03-0-2, 09-03-0-3
        14-04-1-4

    Returned:
        [
            '09-01-0-0',
            '09-01-1-0',
            '09-01-2-0',
        ]
    """
    text = normalise_spaces(text)

    matches = re.finditer(
        r"\b"
        r"(?P<sector>\d{1,2})\s*[- ]\s*"
        r"(?P<section>\d{1,2})\s*[- ]\s*"
        r"(?P<subsection>\d{1,2})\s*[- ]\s*"
        r"(?P<branch>\d{1,2})"
        r"\b",
        text,
        flags=re.IGNORECASE,
    )

    results: List[str] = []

    for match in matches:
        try:
            branch_reference = normalise_branch_parts(
                sector=match.group("sector"),
                section=match.group("section"),
                subsection=match.group("subsection"),
                branch=match.group("branch"),
            )
        except ValueError:
            continue

        if branch_reference not in results:
            results.append(branch_reference)

    return results


def extract_subsection_reference(text: str) -> str:
    """
    Compatibility wrapper.

    Existing code may still call extract_subsection_reference(). It now only
    returns explicitly labelled Sub Section values and will not accidentally
    interpret K.P., rank, document number or OCR noise as a subsection.
    """
    return extract_explicit_subsection_reference(text)


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
    Parse a SACEM Singularities schedule row.

    Supports OCR text and Marker table extraction.

    Typical source row:
        13 1 14.0 m 1460.0 15.1 Beacon type...

    Typical Marker Markdown table row:
        | 13 | 1 | 14.0 m | 1460.0 | 15.1 | Beacon type... |
    """
    text = safe_text(line)

    # Marker commonly returns Markdown table separators.
    text = text.replace("**", "")
    text = text.replace("`", "")
    text = re.sub(r"\s*\|\s*", " ", text)
    text = normalise_spaces(text)

    # Reject Markdown separator rows.
    if not text or re.fullmatch(r"[-:\s]+", text):
        return None

    # Must begin with a schedule row number.
    row_match = re.match(r"^\s*(\d{1,3})\b", text)

    if not row_match:
        return None

    row_number = row_match.group(1)
    remainder = text[row_match.end():].strip()

    numeric_matches = list(
        re.finditer(
            r"(?<![A-Z0-9])"
            r"-?\d[\d,]*(?:\.\d+)?"
            r"(?![A-Z0-9])",
            remainder,
            flags=re.IGNORECASE,
        )
    )

    if len(numeric_matches) < 3:
        return None

    numeric_values: List[Tuple[float, str, int, int]] = []

    for match in numeric_matches:
        value = to_float(match.group(0))

        if value is None:
            continue

        numeric_values.append(
            (
                value,
                match.group(0),
                match.start(),
                match.end(),
            )
        )

    if len(numeric_values) < 3:
        return None

    kp_index: Optional[int] = None

    for index, (value, raw, start, end) in enumerate(numeric_values):
        if abs(value) >= 20 and index + 1 < len(numeric_values):
            next_value = numeric_values[index + 1][0]

            # SACEM singularity codes are normally 1, 2, 9, 10, 11,
            # 15.1, 15.7, etc.
            if -1 <= next_value <= 30:
                kp_index = index
                break

    if kp_index is None:
        return None

    absolute_abscissa = numeric_values[kp_index][0]
    singularity_code = numeric_values[kp_index + 1][1]

    inter_singularity: Optional[float] = None

    if kp_index >= 1:
        inter_candidate = numeric_values[kp_index - 1][0]

        if -1000 <= inter_candidate <= 1000:
            inter_singularity = inter_candidate

    code_end_position = numeric_values[kp_index + 1][3]
    remaining = remainder[code_end_position:].strip(" ,;:-|")

    return {
        "row_number": row_number,
        "inter_singularity_m": inter_singularity,
        "abscissa_m": absolute_abscissa,
        "singularity_code": singularity_code,
        "remaining": normalise_spaces(remaining),
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

# =============================================================================
# TRANSMISSION / ANTICIPATION CALCULATION HELPERS
# =============================================================================

def extract_number_after_label(
    text: str,
    label_pattern: str,
) -> Optional[float]:
    """
    Extract one numerical value following a label.

    Example:
        Entry speed : Ve = 135 km/h
        Initialisation distance at 22 km/h = 160 m
    """
    match = re.search(
        label_pattern
        + r"\s*[:=]?\s*"
        + r"(-?\d+(?:[,.]\d+)?)",
        normalise_spaces(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return to_float(match.group(1))


def extract_two_numbers_after_label(
    text: str,
    label_pattern: str,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Extract invariant / variant value pairs.

    Example:
        Minimum visibility distance 1,047 m 188 m

    Returns:
        (1047.0, 188.0)
    """
    match = re.search(
        label_pattern
        + r"\s*[:=]?\s*"
        + r"(-?\d+(?:[,.]\d+)?)\s*m?"
        + r"\s+"
        + r"(-?\d+(?:[,.]\d+)?)\s*m?",
        normalise_spaces(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return None, None

    return (
        to_float(match.group(1)),
        to_float(match.group(2)),
    )


def extract_singularities_table(pages: List[str]) -> pd.DataFrame:
    """
    Extract all singularity schedule rows.

    Local Abscissa:
        Absolute K.P. - Sub-section Start K.P.

    A subsection start is accepted only when the actual schedule row contains
    the explicit text 'Sub Section'. Context lines are not used to establish
    a new subsection because they can cause incorrect OCR matches.
    """
    records: List[Dict[str, Any]] = []

    for page_number, page_text in enumerate(pages, start=1):
        if not is_singularity_schedule_page(page_text):
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

            if parsed_row is None:
                continue

            context_start = max(0, line_index - 2)
            context_end = min(len(lines), line_index + 4)

            context = normalise_spaces(
                " ".join(lines[context_start:context_end])
            )

            # Only use the actual schedule row to establish a new subsection.
            explicit_subsection = extract_explicit_subsection_reference(line)

            if explicit_subsection:
                current_subsection = explicit_subsection

                # The K.P. of a 'Sub Section' schedule row is local zero.
                current_subsection_start_abscissa = parsed_row["abscissa_m"]

            detected_route = extract_route_reference(
                f"{line} {context}"
            )

            if detected_route:
                current_route = detected_route

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

            singularity_name = parsed_row["remaining"]

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
                    "singularity_name": singularity_name,
                    "associated_variant": extract_associated_variant(
                        f"{line} {context}"
                    ),
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
            "pdf_page",
            "absolute_abscissa_m",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe[SINGULARITY_COLUMNS]

def parse_variant_visual_row(
    line: str,
) -> Optional[Dict[str, str]]:
    """
    Parse OCR or Marker-extracted rows from Variant Composition tables.

    Examples:
        0 09-01-0 TUC KF T/C
        | 0 | 09-01-0 | TUC KF T/C |
        14 09-03-0 TUC DN TCL PEP(1) ...
    """
    text = safe_text(line)

    # Normalise Marker Markdown table rows.
    text = text.replace("**", "")
    text = text.replace("`", "")
    text = re.sub(r"\s*\|\s*", " ", text)
    text = normalise_spaces(text)

    if not text or re.fullmatch(r"[-:\s]+", text):
        return None

    match = re.match(
        r"^(?P<rank>\d{1,2})\s+"
        r"(?:(?P<subsection>\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2})\s+)?"
        r"(?P<variant>.+)$",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    rank = safe_text(match.group("rank"))
    subsection_raw = safe_text(match.group("subsection"))
    variant = normalise_spaces(match.group("variant"))

    variant_upper = variant.upper()

    if not variant or variant_upper in {
        "VARIANTS COMPOSITION",
        "VARIANT COMPOSITION",
        "ANTIC DIST",
        "ANTIC. DIST",
        "SUB SECT",
        "SUB-SECT",
        "RANK",
        "P",
        "A",
    }:
        return None

    has_variant_signal = any(
        marker in variant_upper
        for marker in [
            "TUC",
            "THW",
            "TCE",
            "STATION",
            "PEP",
            "PSD",
            "POINT",
            "ESA",
            "SP",
            "T/C",
            "RP",
            "NP",
            "REPEAT",
            "EMERGENCY",
            "SIG",
        ]
    )

    if not has_variant_signal:
        return None

    subsection = ""

    if subsection_raw:
        try:
            parts = re.split(r"\s*-\s*", subsection_raw)

            subsection = normalise_subsection_parts(
                parts[0],
                parts[1],
                parts[2],
            )
        except (ValueError, IndexError):
            subsection = ""

    return {
        "rank": rank,
        "subsection": subsection,
        "variant": variant,
    }


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
            visual_row = parse_variant_visual_row(line)

            if visual_row:
                if visual_row["subsection"]:
                    current_subsection = visual_row["subsection"]

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
                        "rank": visual_row["rank"],
                        "participating_rank": "",
                        "anticipated_rank": "",
                        "participating_variants": "",
                        "anticipated_variants": "",
                        "all_variants": visual_row["variant"],
                        "source_evidence": context[:MAX_EVIDENCE_LENGTH],
                    }
                )

                # Important: do not process this same row again
                # using the paragraph/label-based parser below.
                continue
                
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
            "all_variants",
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
    transmission_calculations: pd.DataFrame,
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

    lines.extend(
        [
            "",
            "=" * 120,
            "TABLE 3 - TRANSMISSION / ANTICIPATION CALCULATIONS",
            "=" * 120,
        ]
    )

    if transmission_calculations.empty:
        lines.append("[No transmission calculation records extracted]")
    else:
        display_columns = [
            "pdf_page",
            "sector",
            "section",
            "route_number",
            "route_name",
            "direction",
            "route_branches",
            "anticipated_branches",
            "entry_speed_kph",
            "initialisation_distance_m",
            "message_elements",
            "transmission_time_s",
            "current_computed_invariant_m",
            "current_computed_variant_m",
        ]

        available_columns = [
            column
            for column in display_columns
            if column in transmission_calculations.columns
        ]

        lines.append(
            transmission_calculations[available_columns].to_string(
                index=False,
                max_colwidth=100,
            )
        )

    return "\n".join(lines)

def extract_transmission_calculation_table(
    pages: List[str],
) -> pd.DataFrame:
    """
    Extract information from transmission-time and anticipation-distance
    calculation pages.

    Typical Appendix J page text:

        1 - First route, to Up TCL :
        09-01-0-0, 09-01-1-0, ...

        - Anticipated branches to be run :
        09-03-0-0, 09-03-0-2, ...

        Entry speed : Ve = 135 km/h
        Initialisation distance at 22 km/h = 160 m
    """
    records: List[Dict[str, Any]] = []

    for page_number, page_text in enumerate(pages, start=1):
        if not is_transmission_calculation_page(page_text):
            continue

        text = normalise_spaces(page_text)
        metadata = page_metadata(text)

        sector = metadata["sector"]
        section = metadata["section"]

        sector_section_match = re.search(
            r"\bSECTOR\s*(\d{1,2})\s*,?\s*SECTION\s*(\d{1,2})",
            text,
            flags=re.IGNORECASE,
        )

        if sector_section_match:
            sector = f"{int(sector_section_match.group(1)):02d}"
            section = f"{int(sector_section_match.group(2)):02d}"

        route_blocks = re.split(
            r"(?=\b\d+\s*-\s*"
            r"(?:FIRST|SECOND|THIRD)\s+ROUTE\b)",
            text,
            flags=re.IGNORECASE,
        )

        for route_block in route_blocks:
            route_match = re.search(
                r"\b(?P<route_number>\d+)\s*-\s*"
                r"(?P<route_name>FIRST|SECOND|THIRD)\s+ROUTE"
                r"(?:\s*,?\s*TO\s*(?P<direction>UP|DN|DOWN)\s+TCL)?"
                r"\s*[:=]?\s*"
                r"(?P<route_branches>.*?)"
                r"(?="
                r"\s*-\s*ANTICIPATED\s+BRANCHES|"
                r"\s*ENTRY\s+SPEED|"
                r"$"
                r")",
                route_block,
                flags=re.IGNORECASE,
            )

            if not route_match:
                continue

            raw_direction = safe_text(
                route_match.group("direction")
            ).upper()

            if raw_direction == "UP":
                direction = "Up"
            elif raw_direction in {"DN", "DOWN"}:
                direction = "Down"
            else:
                direction = ""

            route_branches = ", ".join(
                extract_branch_references(
                    route_match.group("route_branches")
                )
            )

            anticipated_match = re.search(
                r"ANTICIPATED\s+BRANCHES\s+TO\s+BE\s+RUN"
                r"\s*[:=]?\s*"
                r"(?P<anticipated_branches>.*?)"
                r"(?="
                r"\s*ENTRY\s+SPEED|"
                r"\s*\d+\s*-\s*"
                r"(?:FIRST|SECOND|THIRD)\s+ROUTE|"
                r"$"
                r")",
                route_block,
                flags=re.IGNORECASE,
            )

            anticipated_branches = ""

            if anticipated_match:
                anticipated_branches = ", ".join(
                    extract_branch_references(
                        anticipated_match.group(
                            "anticipated_branches"
                        )
                    )
                )

            entry_speed_kph = extract_number_after_label(
                route_block,
                r"ENTRY\s+SPEED.*?V\s*[Ee]\s*=",
            )

            initialisation_distance_m = extract_number_after_label(
                route_block,
                r"INITIALI[ZS]ATION\s+DISTANCE.*?22\s*KM\s*/?\s*H\s*=",
            )

            running_time_s = extract_number_after_label(
                route_block,
                r"\bRUNNING\s+TIME",
            )

            dwell_time_s = extract_number_after_label(
                route_block,
                r"\bDWELL\s+TIME",
            )

            loop_count = extract_number_after_label(
                route_block,
                r"LOOPS?\s+(?:NBER|NUMBER|NO\.?)\s*:\s*L[NH]",
            )

            message_elements = extract_number_after_label(
                route_block,
                r"ELTS?\s+(?:NBER|NUMBER|NO\.?)\s*:\s*N",
            )

            transmission_time_s = extract_number_after_label(
                route_block,
                r"\bTR[I1]\b",
            )

            visibility_invariant, visibility_variant = (
                extract_two_numbers_after_label(
                    route_block,
                    r"MINIMUM\s+VISIBILITY\s+DISTANCE",
                )
            )

            braking_invariant, braking_variant = (
                extract_two_numbers_after_label(
                    route_block,
                    r"SECTION\s+BRAKING\s+DISTANCE",
                )
            )

            downstream_invariant, downstream_variant = (
                extract_two_numbers_after_label(
                    route_block,
                    r"DOWNSTREAM\s+REQUEST",
                )
            )

            computed_invariant, computed_variant = (
                extract_two_numbers_after_label(
                    route_block,
                    r"CURRENT\s+COMPUTED",
                )
            )

            # Total running time. Restrict to the first Total before
            # "Anticipation distance for section".
            total_running_time_match = re.search(
                r"\bTOTAL\s+(-?\d+(?:[,.]\d+)?)\s*S",
                route_block,
                flags=re.IGNORECASE,
            )

            total_running_time_s = None

            if total_running_time_match:
                total_running_time_s = to_float(
                    total_running_time_match.group(1)
                )

            # Anticipation total appears after braking distance.
            total_anticipation_match = re.search(
                r"SECTION\s+BRAKING\s+DISTANCE.*?"
                r"\bTOTAL\s+"
                r"(-?\d+(?:[,.]\d+)?)\s*M?\s+"
                r"(-?\d+(?:[,.]\d+)?)\s*M?",
                route_block,
                flags=re.IGNORECASE,
            )

            total_anticipation_invariant_m = None
            total_anticipation_variant_m = None

            if total_anticipation_match:
                total_anticipation_invariant_m = to_float(
                    total_anticipation_match.group(1)
                )

                total_anticipation_variant_m = to_float(
                    total_anticipation_match.group(2)
                )

            comments_match = re.search(
                r"COMMENTS?\s*:\s*(.+?)"
                r"(?="
                r"\s*\d+\s*-\s*"
                r"(?:FIRST|SECOND|THIRD)\s+ROUTE|"
                r"$"
                r")",
                route_block,
                flags=re.IGNORECASE,
            )

            comments = ""

            if comments_match:
                comments = normalise_spaces(
                    comments_match.group(1)
                )

            records.append(
                {
                    "pdf_page": page_number,
                    "sector": sector,
                    "section": section,
                    "route_number": safe_text(
                        route_match.group("route_number")
                    ),
                    "route_name": safe_text(
                        route_match.group("route_name")
                    ).title(),
                    "direction": direction,
                    "route_branches": route_branches,
                    "anticipated_branches": anticipated_branches,
                    "entry_speed_kph": entry_speed_kph,
                    "initialisation_distance_m": (
                        initialisation_distance_m
                    ),
                    "running_time_s": running_time_s,
                    "dwell_time_s": dwell_time_s,
                    "total_running_time_s": total_running_time_s,
                    "loop_count": loop_count,
                    "message_elements": message_elements,
                    "transmission_time_s": transmission_time_s,
                    "minimum_visibility_invariant_m": (
                        visibility_invariant
                    ),
                    "minimum_visibility_variant_m": (
                        visibility_variant
                    ),
                    "braking_distance_invariant_m": braking_invariant,
                    "braking_distance_variant_m": braking_variant,
                    "total_anticipation_invariant_m": (
                        total_anticipation_invariant_m
                    ),
                    "total_anticipation_variant_m": (
                        total_anticipation_variant_m
                    ),
                    "downstream_request_invariant_m": (
                        downstream_invariant
                    ),
                    "downstream_request_variant_m": (
                        downstream_variant
                    ),
                    "current_computed_invariant_m": (
                        computed_invariant
                    ),
                    "current_computed_variant_m": (
                        computed_variant
                    ),
                    "comments": comments,
                    "source_evidence": route_block[:MAX_EVIDENCE_LENGTH],
                }
            )

    if not records:
        return pd.DataFrame(
            columns=TRANSMISSION_CALCULATION_COLUMNS
        )

    dataframe = pd.DataFrame(records)

    dataframe = dataframe.drop_duplicates(
        subset=[
            "pdf_page",
            "route_number",
            "route_name",
            "route_branches",
        ]
    )

    dataframe = dataframe.sort_values(
        by=[
            "sector",
            "section",
            "pdf_page",
            "route_number",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe[TRANSMISSION_CALCULATION_COLUMNS]

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

        if not is_singularity_schedule_page(page_text):
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

            # A subsection start is valid only where the actual singularity row
            # explicitly contains 'Sub Section'. Do not use nearby OCR context for this,
            # otherwise ranks, K.P.s and unrelated values can become false subsections.
            detected_subsection = extract_explicit_subsection_reference(line)

            if detected_subsection and parsed_row is not None:
                current_subsection = detected_subsection
                current_subsection_start_abscissa = parsed_row["abscissa_m"]

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
        f"Marker-PDF Source Evidence:\n"
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
    transmission_calculations: pd.DataFrame,
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
            "Transmission_Anticipation_Calculations.csv",
            transmission_calculations.to_csv(index=False),
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
    transmission_calculations: Optional[pd.DataFrame] = None,
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
                ["Extraction Method", "Marker-PDF only"],
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

        if transmission_calculations is not None:
            transmission_calculations.to_excel(
                writer,
                sheet_name="Transmission Calculation",
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

        "test_sheet_transmission_calculations": pd.DataFrame(
            columns=TRANSMISSION_CALCULATION_COLUMNS
        ),

        "test_sheet_organised_source_log": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# =============================================================================
# MARKER-PDF AUDIT LOG / SUMMARY
# =============================================================================

def create_pdf_text_log(
    extracted_pages: List[Dict[str, Any]],
) -> str:
    """
    Create a complete Marker-PDF extraction audit log.
    """
    lines = [
        "=" * 110,
        "MARKER-PDF EXTRACTION AUDIT LOG",
        "=" * 110,
        "",
        "Extraction engine: Marker-PDF only.",
        "No Tesseract, OpenCV preprocessing, native-text fallback, or "
        "alternative OCR engine is used.",
        "",
    ]

    for page_data in extracted_pages:
        page_number = page_data["page_number"]
        extraction_method = page_data["extraction_method"]
        parser_text = safe_text(page_data.get("text", ""))
        audit_text = safe_text(page_data.get("audit_text", parser_text))

        lines.extend(
            [
                "=" * 110,
                f"PDF PAGE {page_number}",
                "=" * 110,
                f"Extraction Method: {extraction_method}",
                f"Parser Text Character Count: {len(parser_text)}",
                f"Parser Text Word Count: {len(parser_text.split())}",
                "",
                "PARSER-NORMALISED MARKER TEXT",
                "-" * 110,
                parser_text or "[NO TEXT EXTRACTED]",
                "",
                "MARKER-PDF RAW / AUDIT OUTPUT",
                "-" * 110,
                audit_text or "[NO AUDIT TEXT EXTRACTED]",
                "",
            ]
        )

    return "\n".join(lines)


def create_pdf_extraction_summary(
    extracted_pages: List[Dict[str, Any]],
) -> pd.DataFrame:
    """
    Create a concise per-page Marker-PDF extraction summary.
    """
    records: List[Dict[str, Any]] = []

    for page_data in extracted_pages:
        parser_text = safe_text(page_data.get("text", ""))
        audit_text = safe_text(page_data.get("audit_text", parser_text))

        records.append(
            {
                "pdf_page": page_data["page_number"],
                "extraction_method": page_data["extraction_method"],
                "character_count": len(parser_text),
                "word_count": len(parser_text.split()),
                "audit_character_count": len(audit_text),
                "text_preview": parser_text[:700],
            }
        )

    return pd.DataFrame(records)
    
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
        "This application uses Marker-PDF only for layout-aware extraction of "
        "native-text and scanned/image-only Subdivision schedule pages."
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
        st.session_state[
            "test_sheet_transmission_calculations"
        ] = pd.DataFrame(
            columns=TRANSMISSION_CALCULATION_COLUMNS
        )
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
                "Reading PDF pages with Marker-PDF..."
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
            progress_text.info(
                "Extracting transmission and anticipation calculation records..."
            )

            transmission_calculations = (
                extract_transmission_calculation_table(pages)
            )

            st.session_state[
                "test_sheet_transmission_calculations"
            ] = transmission_calculations

            # -----------------------------------------------------------------
            # 4. Create readable text table / audit log.
            # -----------------------------------------------------------------
            st.session_state["test_sheet_organised_source_log"] = (
                create_organised_source_log(
                    singularities=singularities,
                    variant_composition=variant_composition,
                    transmission_calculations=transmission_calculations,
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
                f"Transmission route records: {len(transmission_calculations)} | "
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
    transmission_calculations = st.session_state.get(
        "test_sheet_transmission_calculations",
        pd.DataFrame(columns=TRANSMISSION_CALCULATION_COLUMNS),
    )

    organised_source_log = st.session_state.get(
        "test_sheet_organised_source_log",
        "",
    )

    if pdf_text_log:
        st.divider()
        st.markdown("### Extracted Marker-PDF Text Log")

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

        tab_1, tab_2, tab_3, tab_4 = st.tabs(
            [
                "Singularities",
                "Variant Composition",
                "Transmission / Anticipation",
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
            st.caption(
                "Transmission-time and anticipation-distance calculation "
                "records, including route branches and anticipated branches."
            )

            st.dataframe(
                transmission_calculations,
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                "Download Transmission / Anticipation CSV",
                data=transmission_calculations.to_csv(
                    index=False
                ).encode("utf-8"),
                file_name=clean_filename(
                    f"{project_info['sheet_prefix']}_"
                    f"Transmission_Anticipation.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )

        with tab_4:
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
            "Download Full Marker-PDF Extraction Log",
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
        transmission_calculations=transmission_calculations,
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
                        singularities=singularities,
                        variant_composition=variant_composition,
                        transmission_calculations=transmission_calculations,
                        organised_source_log=organised_source_log,
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
