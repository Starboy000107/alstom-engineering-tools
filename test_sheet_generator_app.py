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
# FIXED PROCEDURE CONFIGURATION
# =============================================================================

PROCEDURE_REFERENCE = "C1252-TAC-0274"
PROCEDURE_TITLE = "Trackside ATC Low-Speed Dynamic Test Procedure"

MAX_PDF_SIZE_MB = 80
MAX_SOURCE_EVIDENCE_LENGTH = 1800


# =============================================================================
# TEST DEFINITIONS
#
# These are fixed rules from C1252-TAC-0274.
#
# Station-specific data is NEVER hard-coded:
# - sector
# - section
# - branch
# - subsection
# - abscissa
# - participating variant
# - anticipated variant
# - anticipated distance
# =============================================================================

TEST_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "LS01": {
        "id": "ATC-TNC-TRA-LS01",
        "name": "MTIB Initialization Test",
        "short_name": "MTIB",
        "needs_abscissa": False,
        "requires_variant_link": False,
        "patterns": [
            r"MOVING\s+TRAIN\s+INITIALISATION",
            r"MOVING\s+TRAIN\s+INITIALIZATION",
            r"\bMTIB\b",
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
        "short_name": "STIB",
        "needs_abscissa": False,
        "requires_variant_link": False,
        "patterns": [
            r"STATIONARY\s+TRAIN\s+INITIALISATION",
            r"STATIONARY\s+TRAIN\s+INITIALIZATION",
            r"\bSTIB\b",
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
        "name": "Spacing Stopping Point Test – Track Circuit",
        "short_name": "Spacing Stopping Point",
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"SPACING\s+STOPPING\s+POINT",
        ],
        "checks": [
            ("Track Circuit Occupied", "Associated variant restrictive", "Y / N"),
            ("Available Mode", "CM/AUTO", "Y / N"),
            ("Localisation", "Record Loc Max", "m"),
            ("Target Distance", "Record", "m"),
            ("Singularity Abscissa", "Record", "m"),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Track Circuit Free", "Associated variant permissive", "Y / N"),
            ("Target Comparison", "Free target >= occupied target", "Y / N"),
        ],
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "name": "Special Stopping Point Test – Signal Without Overlap",
        "short_name": "Special Stopping Point",
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"SPECIAL\s+STOPPING\s+POINT",
        ],
        "checks": [
            ("Restrictive Signal", "Associated variant restrictive", "Y / N"),
            ("Available Mode", "CM/AUTO", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record values", "m"),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Permissive Signal", "Associated variant permissive", "Y / N"),
            ("Target Comparison", "Permissive target >= restrictive", "Y / N"),
        ],
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "name": "Complex Stopping Point Test – Signal With Overlap",
        "short_name": "Complex Stopping Point",
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"COMPLEX\s+STOPPING\s+POINT",
            r"SIGNAL.*OVERLAP",
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
        "name": "Stopping Point Test – Point",
        "short_name": "Convergence / Divergence",
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"\bCONVERGENCE\b",
            r"\bDIVERGENCE\b",
            r"\bTURNOUT\b",
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
        "name": "Stopping Point Test – End of SACEM",
        "short_name": "End of SACEM",
        "needs_abscissa": True,
        "requires_variant_link": False,
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
        "short_name": "Station Stopping Point / Door Side",
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"STATION\s+CHARACTERISTICS",
            r"STATION\s+STOPPING",
            r"PLATFORM\s+STOPPING",
        ],
        "checks": [
            ("Localisation Status", "Total Loc", "Y / N"),
            ("Available Mode", "CM/AM", "Y / N"),
            ("Selected Mode", "CM", "Y / N"),
            ("Precise Stop", "Record Loc Max and measured distance", "m"),
            ("Door Side", "Correct side enabled", "A / B"),
            ("Stop Short / Overrun", "Door enabled within ±0.35 m", "Y / N"),
        ],
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "name": "PSD Operation Pre-Test",
        "short_name": "PSD Opening Pre-Test",
        "needs_abscissa": False,
        "requires_variant_link": True,
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
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"EMERGENCY\s+ZONE",
            r"EMERGENCY\s+STOP\s+ZONE",
            r"\bESZ\b",
            r"REPEAT\s+ENTRANCE\s+EMERGENCY\s+AREA",
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
        "short_name": "Change of End Zone",
        "needs_abscissa": True,
        "requires_variant_link": True,
        "patterns": [
            r"CHANGE\s+OF\s+END",
            r"CHANGE\s+ENDS",
            r"REVERSE\s+SUB-?SECTION",
        ],
        "checks": [
            ("Cab 1 Arrival", "Total Loc and CM/AM available", "Y / N"),
            ("Cab 1 Loc Max / Loc Min", "Record", "m"),
            ("Cab 2 Before CoE", "Shutdown and distance to signal < 12m", "Y / N"),
            ("Cab 2 After CoE", "Total Loc and CM/AM available", "Y / N"),
            ("First RB / MTIB", "No delocalisation / no EB", "Y / N"),
        ],
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "name": "Temporary Speed Restriction Test",
        "short_name": "Temporary Speed Restriction",
        "needs_abscissa": False,
        "requires_variant_link": False,
        "patterns": [
            r"TEMPORARY\s+SPEED\s+RESTRICTION",
            r"\bTSR\b",
        ],
        "checks": [
            ("Track Circuit", "Record selected TC", "Record"),
            ("Section", "Record selected section", "Record"),
            ("TSR Value", "Record TSR value", "kph"),
            ("Train Behaviour", "Target speed follows TSR; no EB", "Y / N"),
        ],
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "short_name": "Return Channel Test",
        "needs_abscissa": False,
        "requires_variant_link": False,
        "patterns": [
            r"RETURN\s+CHANNEL",
        ],
        "checks": [
            ("Return Channel Location", "Record location", "Record"),
            ("Return Channel Loop", "Record loop / equipment reference", "Record"),
            ("Trackside Reception", "Train information received", "Y / N"),
        ],
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "name": "End of Shunt Test",
        "short_name": "End of Shunt Test",
        "needs_abscissa": False,
        "requires_variant_link": False,
        "patterns": [
            r"END\s+OF\s+SHUNT",
            r"SHUNT\s+ROUTE",
        ],
        "checks": [
            ("Shunt Route Name", "Record", "Record"),
            ("Step 1 – Begin", "Record Branch, Loc Max and Target Distance", "Record"),
            ("Step 2", "Train in RM", "Y / N"),
            ("Step 3 – End", "Record CM resume Branch and Loc Max", "Record"),
            ("Acceptance", "CM/AM resumes at approved location", "Y / N"),
        ],
    },
}


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
    "rank",
    "event_name",
    "location_name",
    "participate_variant",
    "anticipate_rank",
    "source_page",
    "source_evidence",
    "remarks",
]

VARIANT_MAP_COLUMNS = [
    "source_page",
    "sector",
    "section",
    "variant_rank_p",
    "variant_rank_a",
    "variant_subsection",
    "variant_composition",
    "anticipation_distance_m",
    "source_evidence",
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
    "variant_map_composition",
    "variant_map_rank_p",
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
    """Safely return text."""
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def normalise_spaces(value: Any) -> str:
    """Collapse multiple spaces/newlines."""
    return re.sub(r"\s+", " ", safe_text(value)).strip()


def clean_filename(value: str) -> str:
    """Make a safe filename."""
    value = re.sub(r'[\\/:*?"<>|]', "_", safe_text(value))
    value = re.sub(r"\s+", "_", value)

    return value[:150] or "ATC_Test_Sheet"


def get_file_hash(data: bytes) -> str:
    """Create hash used to identify changed uploads."""
    return hashlib.sha256(data).hexdigest()


def to_float(value: Any) -> Optional[float]:
    """Convert text such as 2,191.0 m to float."""
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


def normalise_branch(value: Any) -> str:
    """
    Normalise a branch number.

    Examples:
        09-01-2-0 -> 09-01-2-0
        9-1-2-0   -> 09-01-2-0
        09 01 2 0 -> 09-01-2-0
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
    """Return sector, section and subsection from a branch."""
    parts = safe_text(branch).split("-")

    sector = parts[0] if len(parts) >= 1 else ""
    section = parts[1] if len(parts) >= 2 else ""
    subsection = "-".join(parts[:3]) if len(parts) >= 3 else ""

    return sector, section, subsection


def format_distance(value: Any) -> str:
    """Format number as metres."""
    numeric = to_float(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


# =============================================================================
# PDF TEXT EXTRACTION
# =============================================================================

@st.cache_data(show_spinner=False, max_entries=3)
def extract_pdf_text_pages(pdf_bytes: bytes) -> List[str]:
    """
    Extract selectable text from PDF pages.

    The result is cached by Streamlit, reducing CPU and memory consumption
    after the initial upload/extraction.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency pypdf. "
            "Add pypdf>=4.0.0 to requirements.txt."
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(
                "The uploaded PDF is encrypted."
            ) from exc

    return [
        page.extract_text() or ""
        for page in reader.pages
    ]


# =============================================================================
# PAGE CLASSIFICATION / METADATA
# =============================================================================

def page_metadata(page_text: str) -> Dict[str, str]:
    """Extract sector, section and direction from a page."""
    text = normalise_spaces(page_text)

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


def variant_map_metadata(page_text: str) -> Dict[str, str]:
    """
    Extract sector and section from Safety Variants Message pages.

    Supports:
        SECTION 09 - 01
        SECTION 14 - 04
    """
    text = normalise_spaces(page_text)

    section_match = re.search(
        r"\bSECTION\s+(\d{1,2})\s*-\s*(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )

    if section_match:
        return {
            "sector": f"{int(section_match.group(1)):02d}",
            "section": f"{int(section_match.group(2)):02d}",
        }

    generic = page_metadata(page_text)

    return {
        "sector": generic["sector"],
        "section": generic["section"],
    }


def is_subdivision_page(page_text: str) -> bool:
    """Identify Appendix J singularities pages."""
    upper = page_text.upper()

    return any(
        marker in upper
        for marker in [
            "SINGULARITIES",
            "TEST ABSC",
            "INTER SINGUL",
            "UP TRACK NORMAL DIRECTION",
            "DN TRACK NORMAL DIRECTION",
            "DOWN TRACK NORMAL DIRECTION",
        ]
    )


def is_variant_map_page(page_text: str) -> bool:
    """Identify Safety Variants Message pages."""
    upper = page_text.upper()

    return (
        "VARIANTS COMPOSITION" in upper
        and "ANTIC" in upper
        and (
            "SAFETY VARIANTS" in upper
            or "SAFETY VARIANTS MESSAGE" in upper
        )
    )


# =============================================================================
# SOURCE EXTRACTION HELPERS
# =============================================================================

def extract_branch_from_text(text: str) -> str:
    """Extract last branch-like value from source text."""
    hyphen_matches = re.findall(
        r"\b\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,3}\b",
        text,
    )

    if hyphen_matches:
        return normalise_branch(hyphen_matches[-1])

    spaced_matches = re.findall(
        r"\b\d{1,2}\s+\d{1,2}\s+\d{1,2}\s+\d{1,3}\b",
        text,
    )

    if spaced_matches:
        return normalise_branch(spaced_matches[-1])

    return ""


def extract_associated_variant(text: str) -> str:
    """
    Extract variant from source wording such as:

        Associated variant : (TUC KF T/C)
        Associated variants : (THW 222 RP) & (THW 222 NP)
    """
    match = re.search(
        r"ASSOCIATED\s+VARIANTS?"
        r"(?:\s+NUMBER)?\s*[:=]?\s*"
        r"(.+?)"
        r"(?=$|/\s*NORMAL\s+BRANCH|"
        r"/\s*RANK\s+OF|"
        r";\s*RANK\s+OF|"
        r"\s+SPACING\s+STOPPING|"
        r"\s+SPECIAL\s+STOPPING|"
        r"\s+CONVERGENCE|"
        r"\s+DIVERGENCE)",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    value = normalise_spaces(match.group(1))

    return value.strip("() ")


def extract_anticipate_rank(text: str) -> str:
    """Extract rank of first anticipated variant."""
    match = re.search(
        r"RANK\s+OF\s+FIRST\s+ANTICIPATED\s+VARIANT\s*:\s*(\d+)",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    return match.group(1) if match else ""


def extract_location_name(text: str) -> str:
    """Extract useful location/equipment label."""
    patterns = [
        r"RETURN\s+CHANNEL\s*(?:NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:SIGNAL|SIG)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:TRACK\s*CIRCUIT|T/C)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:POINT|TURNOUT)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            safe_text(text),
            flags=re.IGNORECASE,
        )

        if match:
            return normalise_spaces(match.group(0))

    return ""


def infer_event_name(text: str) -> str:
    """Create a useful event-name label from source text."""
    upper = safe_text(text).upper()

    labels = [
        ("SPACING STOPPING POINT", "Spacing Stopping Point"),
        ("SPECIAL STOPPING POINT", "Special Stopping Point"),
        ("COMPLEX STOPPING POINT", "Complex Stopping Point"),
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
        ("END OF SACEM", "End of SACEM"),
        ("EXIT SACEM", "End of SACEM"),
        ("CHAINED END", "Chained End"),
        ("SIMPLE END", "Simple End"),
        ("BEACON TYPE", "Beacon"),
        ("BEACON", "Beacon"),
    ]

    for phrase, label in labels:
        if phrase in upper:
            return label

    return "Subdivision Event"


# =============================================================================
# VARIANT COMPARISON
# =============================================================================

def normalise_variant_name(value: Any) -> str:
    """
    Create comparison-safe variant text.

    Examples:
        TUC KF T/C                    -> TUCKFTC
        TUC-KF-T/C                    -> TUCKFTC
        THW 20 SIG & THW SU T/C       -> THW20SIGTHWSUTC
    """
    text = safe_text(value).upper()

    text = text.replace("(", " ")
    text = text.replace(")", " ")
    text = text.replace("&", " ")
    text = text.replace("/", " ")
    text = text.replace("-", " ")

    return re.sub(r"[^A-Z0-9]+", "", text)


def split_variant_composition(value: Any) -> List[str]:
    """
    Split multi-part variant compositions.

    Example:
        THW 20 SIG & THW SU T/C
        -> THW 20 SIG
        -> THW SU T/C
    """
    text = safe_text(value)

    if not text:
        return []

    parts = re.split(
        r"\s*(?:&|\+|/|\band\b)\s*",
        text,
        flags=re.IGNORECASE,
    )

    return [
        normalise_spaces(part)
        for part in parts
        if normalise_spaces(part)
    ]


def variant_composition_matches(
    subdivision_variant: str,
    variant_composition: str,
) -> bool:
    """
    Compare an associated variant in a singularity row against a Variant Map
    composition.

    Supports exact and multi-component matching.
    """
    event_value = normalise_variant_name(subdivision_variant)
    map_value = normalise_variant_name(variant_composition)

    if not event_value or not map_value:
        return False

    if event_value == map_value:
        return True

    if event_value in map_value or map_value in event_value:
        return True

    event_parts = split_variant_composition(subdivision_variant)
    map_parts = split_variant_composition(variant_composition)

    for event_part in event_parts:
        event_key = normalise_variant_name(event_part)

        if not event_key:
            continue

        for map_part in map_parts:
            map_key = normalise_variant_name(map_part)

            if not map_key:
                continue

            if event_key == map_key:
                return True

            if event_key in map_key or map_key in event_key:
                return True

    return False


# =============================================================================
# SUBDIVISION EVENT EXTRACTION
# =============================================================================

def parse_subdivision_page(
    page_text: str,
    page_number: int,
) -> List[Dict[str, Any]]:
    """
    Parse a standard Appendix J singularities page.

    Typical row format after selectable text extraction:

        45 ! 4.8 m 2,191.0 11 Associated variant : (TUC KF T/C)
        Spacing Stopping Point ... 09 01 2 0
    """
    metadata = page_metadata(page_text)

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
        remaining = normalise_spaces(row_match.group(5))

        if abscissa is None:
            continue

        if abscissa < -10000 or abscissa > 100000:
            continue

        context = " ".join(
            lines[
                max(0, line_index - 1):
                min(len(lines), line_index + 3)
            ]
        )

        source_text = normalise_spaces(
            f"{line} {context}"
        )

        branch = extract_branch_from_text(source_text)

        if not branch and current_subsection:
            branch = f"{current_subsection}-0"

        if branch:
            current_branch = branch
        else:
            branch = current_branch

        sector, section, subsection = branch_parts(branch)

        if not sector:
            sector = metadata["sector"]

        if not section:
            section = metadata["section"]

        if not subsection:
            subsection = current_subsection

        # Subsection headings are not test events.
        if (
            "SUB SECTION" in source_text.upper()
            and "HEADING" in source_text.upper()
        ):
            continue

        # Rank is normally near branch/reference in extracted table text.
        rank = ""

        rank_match = re.search(
            r"\b(\d{1,2})\s+"
            r"\d{1,2}\s*[- ]\s*"
            r"\d{1,2}\s*[- ]\s*"
            r"\d{1,2}\s*[- ]\s*"
            r"\d{1,3}\b",
            source_text,
        )

        if rank_match:
            rank = rank_match.group(1)

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
                "rank": rank,
                "event_name": infer_event_name(source_text),
                "location_name": extract_location_name(source_text),
                "participate_variant": extract_associated_variant(
                    source_text
                ),
                "anticipate_rank": extract_anticipate_rank(source_text),
                "source_page": page_number,
                "source_evidence": source_text[
                    :MAX_SOURCE_EVIDENCE_LENGTH
                ],
                "remarks": remaining[
                    :MAX_SOURCE_EVIDENCE_LENGTH
                ],
            }
        )

    return output


def extract_subdivision_events(
    pages: List[str],
    progress_bar: Any,
    progress_text: Any,
) -> Tuple[pd.DataFrame, List[str]]:
    """Extract all Appendix J subdivision event rows."""
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []

    total_pages = len(pages)

    for page_number, page_text in enumerate(pages, start=1):
        progress_text.info(
            f"Reading subdivision page {page_number} of {total_pages}..."
        )

        progress_bar.progress(
            int(page_number / total_pages * 100)
        )

        if not is_subdivision_page(page_text):
            continue

        try:
            records.extend(
                parse_subdivision_page(
                    page_text=page_text,
                    page_number=page_number,
                )
            )
        except Exception as exc:
            warnings.append(
                f"Subdivision page {page_number} skipped: {exc}"
            )

    progress_bar.progress(100)

    if not records:
        return (
            pd.DataFrame(columns=EXTRACTED_EVENT_COLUMNS),
            warnings,
        )

    dataframe = pd.DataFrame(records)

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
# SAFETY VARIANT MAP EXTRACTION
# =============================================================================

def parse_variant_map_page(
    page_text: str,
    page_number: int,
) -> List[Dict[str, Any]]:
    """
    Parse Safety Variants Message pages.

    Typical text examples:

        0 14-04-0 THW SL T/C
        1 THW ESA Temp Point 282
        12 4 14-06-0 THW SS T/C 481 m
        13 5 14-06-1 THW ST T/C 720 m

    The parser supports:
      Rank P
      Rank P / Rank A
      optional Subsection
      Variant Composition
      optional Anticipation Distance
    """
    metadata = variant_map_metadata(page_text)

    lines = [
        normalise_spaces(line)
        for line in page_text.splitlines()
        if normalise_spaces(line)
    ]

    records: List[Dict[str, Any]] = []

    current_subsection = ""

    row_pattern = re.compile(
        r"^\s*(\d{1,2})"
        r"(?:\s+(\d{1,2}))?"
        r"(?:\s+(\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}))?"
        r"\s+(.+?)"
        r"(?:\s+(\d{1,4}(?:[.,]\d{1,3})?)\s*m)?\s*$",
        flags=re.IGNORECASE,
    )

    ignore_words = [
        "VARIANTS COMPOSITION",
        "SAFETY VARIANTS",
        "SAFETY VARIANTS MESSAGE",
        "ANTIC. DIST",
        "ANTIC DIST",
        "NOTA",
        "RANK",
        "MTR CONTRACT",
        "APPENDIX",
        "SECTION",
        "SECTOR",
        "POINT NORMAL POSITION",
        "POINT REVERSE POSITION",
    ]

    for line in lines:
        upper = line.upper()

        if any(word in upper for word in ignore_words):
            continue

        if len(line) < 4:
            continue

        match = row_pattern.match(line)

        if not match:
            continue

        rank_p = safe_text(match.group(1))
        rank_a = safe_text(match.group(2))

        subsection = safe_text(match.group(3))
        if subsection:
            subsection = subsection.replace(" ", "")
            current_subsection = subsection

        composition = normalise_spaces(match.group(4))
        anticipation_distance = to_float(match.group(5))

        if not composition:
            continue

        if composition.upper().startswith("SWITCH"):
            continue

        if composition.upper().startswith("SPARE"):
            continue

        if composition.upper().startswith("CHANNEL"):
            continue

        if len(composition) < 3:
            continue

        records.append(
            {
                "source_page": page_number,
                "sector": metadata["sector"],
                "section": metadata["section"],
                "variant_rank_p": rank_p,
                "variant_rank_a": rank_a,
                "variant_subsection": (
                    subsection or current_subsection
                ),
                "variant_composition": composition,
                "anticipation_distance_m": anticipation_distance,
                "source_evidence": line[
                    :MAX_SOURCE_EVIDENCE_LENGTH
                ],
            }
        )

    return records


def extract_variant_maps(
    pages: List[str],
    progress_bar: Any,
    progress_text: Any,
) -> Tuple[pd.DataFrame, List[str]]:
    """Extract all Safety Variant Map records."""
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []

    total_pages = len(pages)

    for page_number, page_text in enumerate(pages, start=1):
        progress_text.info(
            f"Reading variant map page {page_number} of {total_pages}..."
        )

        progress_bar.progress(
            int(page_number / total_pages * 100)
        )

        if not is_variant_map_page(page_text):
            continue

        try:
            records.extend(
                parse_variant_map_page(
                    page_text=page_text,
                    page_number=page_number,
                )
            )
        except Exception as exc:
            warnings.append(
                f"Variant map page {page_number} skipped: {exc}"
            )

    progress_bar.progress(100)

    if not records:
        return (
            pd.DataFrame(columns=VARIANT_MAP_COLUMNS),
            warnings,
        )

    dataframe = pd.DataFrame(records)

    for column in VARIANT_MAP_COLUMNS:
        if column not in dataframe.columns:
            dataframe[column] = ""

    dataframe["anticipation_distance_m"] = pd.to_numeric(
        dataframe["anticipation_distance_m"],
        errors="coerce",
    )

    dataframe = dataframe.drop_duplicates(
        subset=[
            "source_page",
            "variant_rank_p",
            "variant_rank_a",
            "variant_subsection",
            "variant_composition",
        ]
    )

    return dataframe[VARIANT_MAP_COLUMNS].reset_index(drop=True), warnings


# =============================================================================
# TEST IDENTIFICATION
# =============================================================================

def identify_tests(source_text: str) -> List[str]:
    """
    Find low-speed test types from the singularity/event source wording.

    Test type comes from fixed C1252-TAC-0274 procedure logic.
    Station data comes from the PDF.
    """
    matches: List[str] = []

    for test_code, definition in TEST_DEFINITIONS.items():
        for pattern in definition["patterns"]:
            if re.search(
                pattern,
                source_text,
                flags=re.IGNORECASE,
            ):
                matches.append(test_code)
                break

    return list(dict.fromkeys(matches))


def candidate_anticipate_abscissa(
    abscissa_m: Optional[float],
    anticipation_distance_m: Optional[float],
    direction: str,
) -> Optional[float]:
    """
    Calculate draft anticipated KP.

    Up direction:
        event abscissa + anticipation distance

    Down direction:
        event abscissa - anticipation distance

    Must be reviewed because chained/reverse branches can invalidate
    simple arithmetic.
    """
    if abscissa_m is None or anticipation_distance_m is None:
        return None

    if safe_text(direction).lower().startswith("up"):
        return abscissa_m + anticipation_distance_m

    if safe_text(direction).lower().startswith("down"):
        return abscissa_m - anticipation_distance_m

    return None


# =============================================================================
# VARIANT MAP / EVENT LINKING
# =============================================================================

def find_matching_variant_rows(
    event: pd.Series,
    variant_map: pd.DataFrame,
) -> List[pd.Series]:
    """
    Match a singularity event against Variant Map rows.

    Match sequence:

    1. Same sector and section where available.
    2. Compare Associated Variant with Variant Composition.
    3. If variant text extraction is poor, use subsection + rank fallback.

    This creates the required relationship:

        Safety Variant Map
            -> Variant Composition
            -> Subdivision Associated Variant
            -> Singularity Event
            -> Low-Speed Test Sheet
    """
    if variant_map.empty:
        return []

    event_variant = safe_text(event.get("participate_variant"))
    event_sector = safe_text(event.get("sector"))
    event_section = safe_text(event.get("section"))
    event_subsection = safe_text(event.get("subsection"))
    event_rank = safe_text(event.get("rank"))

    candidates = variant_map.copy()

    if event_sector:
        candidates = candidates[
            candidates["sector"].astype(str).eq(event_sector)
        ].copy()

    if event_section:
        candidates = candidates[
            candidates["section"].astype(str).eq(event_section)
        ].copy()

    matches: List[pd.Series] = []

    for _, map_row in candidates.iterrows():
        map_variant = safe_text(
            map_row.get("variant_composition")
        )

        same_subsection = (
            event_subsection
            and event_subsection
            == safe_text(map_row.get("variant_subsection"))
        )

        same_rank = (
            event_rank
            and event_rank
            == safe_text(map_row.get("variant_rank_p"))
        )

        text_match = variant_composition_matches(
            subdivision_variant=event_variant,
            variant_composition=map_variant,
        )

        # Preferred match: exact/partial variant composition match.
        if text_match:
            matches.append(map_row)
            continue

        # Secondary match: subsection and rank match.
        if same_subsection and same_rank:
            matches.append(map_row)

    return matches


def create_test_register(
    events: pd.DataFrame,
    variant_map: pd.DataFrame,
    start_kp: float,
    end_kp: float,
    selected_branch: str,
) -> pd.DataFrame:
    """
    Build draft test register.

    Variant-dependent tests are only created if their associated variant
    links to the Safety Variant Map.

    Non-variant tests such as MTIB, STIB and Return Channel may be generated
    directly from the singularity event.
    """
    lower_kp = min(start_kp, end_kp)
    upper_kp = max(start_kp, end_kp)

    selected_events = events[
        events["abscissa_m"].between(
            lower_kp,
            upper_kp,
            inclusive="both",
        )
    ].copy()

    if selected_branch != "All branches":
        selected_events = selected_events[
            selected_events["branch"].eq(selected_branch)
        ].copy()

    rows: List[Dict[str, Any]] = []

    for _, event in selected_events.iterrows():
        source_text = " ".join(
            [
                safe_text(event.get("event_name")),
                safe_text(event.get("remarks")),
                safe_text(event.get("source_evidence")),
                safe_text(event.get("location_name")),
            ]
        )

        test_codes = identify_tests(source_text)

        if not test_codes:
            continue

        linked_variant_rows = find_matching_variant_rows(
            event=event,
            variant_map=variant_map,
        )

        for test_code in test_codes:
            definition = TEST_DEFINITIONS[test_code]

            # A test which requires variants should normally only be created
            # when variant map matching has been found.
            if (
                definition["requires_variant_link"]
                and not linked_variant_rows
            ):
                continue

            # For direct/non-variant tests, create one default record.
            working_variant_rows: List[pd.Series]

            if linked_variant_rows:
                working_variant_rows = linked_variant_rows
            else:
                working_variant_rows = [
                    pd.Series(
                        {
                            "variant_composition": "",
                            "variant_rank_p": "",
                            "variant_rank_a": "",
                            "anticipation_distance_m": None,
                            "variant_subsection": "",
                        }
                    )
                ]

            for variant_row in working_variant_rows:
                participate_abscissa: Optional[float]

                if definition["needs_abscissa"]:
                    participate_abscissa = to_float(
                        event.get("abscissa_m")
                    )
                else:
                    participate_abscissa = None

                anticipation_distance = to_float(
                    variant_row.get("anticipation_distance_m")
                )

                anticipated_abscissa = candidate_anticipate_abscissa(
                    abscissa_m=participate_abscissa,
                    anticipation_distance_m=anticipation_distance,
                    direction=safe_text(event.get("direction")),
                )

                rows.append(
                    {
                        "test_code": test_code,
                        "test_id": definition["id"],
                        "test_name": definition["name"],
                        "needs_abscissa": definition["needs_abscissa"],
                        "sector": safe_text(event.get("sector")),
                        "section": safe_text(event.get("section")),
                        "subsection": safe_text(event.get("subsection")),
                        "branch": safe_text(event.get("branch")),
                        "direction": safe_text(event.get("direction")),
                        "abscissa_m": participate_abscissa,
                        "participate_variant": safe_text(
                            event.get("participate_variant")
                        ),
                        "variant_map_composition": safe_text(
                            variant_row.get("variant_composition")
                        ),
                        "variant_map_rank_p": safe_text(
                            variant_row.get("variant_rank_p")
                        ),
                        "anticipate_variant": "",
                        "anticipate_rank": safe_text(
                            variant_row.get("variant_rank_a")
                        ),
                        "anticipate_distance_m": anticipation_distance,
                        "anticipate_abscissa_m": anticipated_abscissa,
                        "event_name": safe_text(
                            event.get("event_name")
                        ),
                        "location_name": safe_text(
                            event.get("location_name")
                        ),
                        "source_page": safe_text(
                            event.get("source_page")
                        ),
                        "source_evidence": safe_text(
                            event.get("source_evidence")
                        ),
                        "review_status": "To be reviewed",
                        "remarks": (
                            "Automatically linked between Appendix J "
                            "Subdivision Event and Safety Variant Map. "
                            "Verify participating/anticipated rank, "
                            "anticipated branch, anticipation distance "
                            "and candidate anticipated abscissa."
                        ),
                    }
                )

    if not rows:
        return pd.DataFrame(columns=REGISTER_COLUMNS)

    dataframe = pd.DataFrame(rows)

    dataframe = dataframe.drop_duplicates(
        subset=[
            "test_code",
            "branch",
            "abscissa_m",
            "participate_variant",
            "variant_map_composition",
            "source_page",
        ]
    )

    return dataframe.sort_values(
        by=[
            "branch",
            "abscissa_m",
            "test_code",
        ],
        na_position="last",
    ).reset_index(drop=True)


def apply_test_details(register: pd.DataFrame) -> pd.DataFrame:
    """Update test ID/name/abscissa flag after manual test-code editing."""
    output = register.copy()

    for index in output.index:
        test_code = safe_text(output.at[index, "test_code"])

        if test_code in TEST_DEFINITIONS:
            details = TEST_DEFINITIONS[test_code]

            output.at[index, "test_id"] = details["id"]
            output.at[index, "test_name"] = details["name"]
            output.at[index, "needs_abscissa"] = (
                details["needs_abscissa"]
            )
        else:
            output.at[index, "test_id"] = ""
            output.at[index, "test_name"] = ""
            output.at[index, "needs_abscissa"] = False

    return output


# =============================================================================
# DOCX GENERATION
# =============================================================================

def set_cell_text(
    cell: Any,
    value: Any,
    bold: bool = False,
) -> None:
    """Set a Word table cell value."""
    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(value))
    run.bold = bold


def add_docx_row(
    table: Any,
    values: List[Any],
    bold: bool = False,
) -> None:
    """Add one row to a DOCX table."""
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(
            cells[index],
            value,
            bold=bold,
        )


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> bytes:
    """
    Create a C1252-TAC-0274 Appendix-A-style draft test sheet.
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

    if test_code not in TEST_DEFINITIONS:
        raise ValueError(
            f"Unknown Test Code '{test_code}'."
        )

    definition = TEST_DEFINITIONS[test_code]

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

    alstom = header.add_run("ALSTOM\n")
    alstom.bold = True
    alstom.font.size = Pt(16)

    procedure_title = header.add_run(PROCEDURE_TITLE)
    procedure_title.bold = True
    procedure_title.font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{definition['name']} ({definition['id']})"
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
            PROCEDURE_TITLE,
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

    subtitle = document.add_paragraph()
    subtitle.add_run(
        definition["short_name"]
    ).bold = True

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

    if bool(row.get("needs_abscissa")):
        abscissa = format_distance(row.get("abscissa_m"))
    else:
        abscissa = "Not required for this test"

    add_docx_row(
        location_table,
        [
            "Abscissa:",
            abscissa,
            "Location / Asset:",
            safe_text(row.get("location_name")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Participate Variant:",
            safe_text(row.get("participate_variant")),
            "Variant Map Composition:",
            safe_text(row.get("variant_map_composition")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Variant Map Rank:",
            safe_text(row.get("variant_map_rank_p")),
            "Anticipate Rank:",
            safe_text(row.get("anticipate_rank")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Anticipation Distance:",
            format_distance(row.get("anticipate_distance_m")),
            "Candidate Anticipate Abs.:",
            (
                format_distance(row.get("anticipate_abscissa_m"))
                or "To be reviewed"
            ),
        ],
    )

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
        set_cell_text(
            test_table.rows[0].cells[index],
            header_text,
            bold=True,
        )

    for action, expected, unit in definition["checks"]:
        add_docx_row(
            test_table,
            [
                action,
                expected,
                f"{unit}: ",
                "☐ Y   ☐ N   ☐ N/A",
            ],
        )

    document.add_paragraph()

    notes_title = document.add_paragraph()
    notes_title.add_run(
        "Comments / Engineering Remarks"
    ).bold = True

    notes_table = document.add_table(rows=1, cols=1)
    notes_table.style = "Table Grid"

    note_text = (
        f"Source Page: {safe_text(row.get('source_page'))}\n"
        f"Source Event: {safe_text(row.get('event_name'))}\n"
        f"Source Evidence: {safe_text(row.get('source_evidence'))}\n\n"
        f"Engineering Remarks: {safe_text(row.get('remarks'))}"
    )

    set_cell_text(notes_table.rows[0].cells[0], note_text)

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
    progress_bar: Any,
    progress_text: Any,
) -> bytes:
    """Create ZIP file containing DOCX test sheets."""
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

            progress_text.info(
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
                    sheet_number=sheet_number,
                ),
            )

            progress_bar.progress(
                int(sequence / total * 100)
            )

    progress_bar.progress(100)

    progress_text.success(
        f"DOCX generation completed: {total} test sheet(s)."
    )

    return output.getvalue()


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    extracted_events: pd.DataFrame,
    variant_map: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
    """Create Excel register including source events and variant map."""
    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        cover = pd.DataFrame(
            [
                ["Procedure Reference", project_info["procedure_reference"]],
                ["Procedure Title", PROCEDURE_TITLE],
                ["Project / Contract", project_info["project"]],
                ["Trackside Version", project_info["trackside_version"]],
                ["ATP Version", project_info["atp_version"]],
                ["ATO Version", project_info["ato_version"]],
                ["Generated Date", str(date.today())],
                ["Test Register Rows", len(register)],
                ["Extracted Event Rows", len(extracted_events)],
                ["Variant Map Rows", len(variant_map)],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(
            writer,
            sheet_name="Cover",
            index=False,
        )

        register.to_excel(
            writer,
            sheet_name="Test Register",
            index=False,
        )

        extracted_events.to_excel(
            writer,
            sheet_name="Extracted Events",
            index=False,
        )

        variant_map.to_excel(
            writer,
            sheet_name="Safety Variant Map",
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
# UI
# =============================================================================

def render_styles() -> None:
    """Render local CSS."""
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


def render_test_sheet_generator_app() -> None:
    """
    Render Test Sheet Generator.

    This is called by app.py.
    Do not use st.set_page_config() in this module.
    """
    render_styles()

    st.markdown(
        """
        <div class="generator-hero">
            <h1>ATC Low-Speed Dynamic Test Sheet Generator</h1>
            <p>
                Upload an Appendix J Station SACEM Subdivision PDF. The tool
                links Safety Variant Map data to Subdivision associated
                variants and generates reviewable low-speed dynamic test
                sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="generator-warning">
            <b>Engineering control:</b><br>
            This application creates a draft test register. All linked
            variants, anticipated information, branches, abscissae and
            applicable tests must be verified against approved project
            subdivision, safety-variant, invariant, PAT and test documents.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # Project information
    # -------------------------------------------------------------------------
    with st.expander("Project Information", expanded=True):
        col1, col2, col3 = st.columns(3)

        with col1:
            project = st.text_input(
                "Project / Contract",
                key="generator_project",
            )

            trackside_version = st.text_input(
                "Trackside Version",
                key="generator_trackside_version",
            )

        with col2:
            atp_version = st.text_input(
                "ATP Version",
                key="generator_atp_version",
            )

            ato_version = st.text_input(
                "ATO Version",
                key="generator_ato_version",
            )

        with col3:
            procedure_reference = st.text_input(
                "Procedure Reference",
                value=PROCEDURE_REFERENCE,
                key="generator_procedure_reference",
            )

            sheet_prefix = st.text_input(
                "Test Sheet Prefix",
                value=PROCEDURE_REFERENCE,
                key="generator_sheet_prefix",
            )

    project_info = {
        "project": project,
        "trackside_version": trackside_version,
        "atp_version": atp_version,
        "ato_version": ato_version,
        "procedure_reference": procedure_reference,
        "sheet_prefix": sheet_prefix or PROCEDURE_REFERENCE,
    }

    # -------------------------------------------------------------------------
    # PDF input
    # -------------------------------------------------------------------------
    st.markdown("## 1. Upload Station SACEM Subdivision PDF")

    uploaded_file = st.file_uploader(
        "Upload Appendix J SACEM Subdivision / Variant Map PDF",
        type=["pdf"],
        key="generator_subdivision_pdf",
    )

    if uploaded_file is None:
        st.info(
            "Upload one station SACEM Subdivision PDF containing "
            "singularity tables and Safety Variants Message pages."
        )
        return

    pdf_bytes = uploaded_file.getvalue()
    pdf_size_mb = len(pdf_bytes) / (1024 * 1024)

    if pdf_size_mb > MAX_PDF_SIZE_MB:
        st.error(
            f"PDF size is {pdf_size_mb:.1f} MB. Maximum supported "
            f"size is {MAX_PDF_SIZE_MB} MB."
        )
        return

    current_hash = get_file_hash(pdf_bytes)

    if current_hash != st.session_state.get("generator_pdf_hash"):
        st.session_state.pop("generator_events", None)
        st.session_state.pop("generator_variant_map", None)
        st.session_state.pop("generator_warnings", None)
        st.session_state.pop("generator_register", None)
        st.session_state.pop("generator_docx_zip", None)

    # -------------------------------------------------------------------------
    # PDF extraction
    # -------------------------------------------------------------------------
    if st.button(
        "🔎 Extract Events and Variant Maps",
        type="primary",
        use_container_width=True,
        key="generator_extract_pdf_button",
    ):
        progress_text = st.empty()
        progress_bar = st.progress(0)

        try:
            progress_text.info(
                "Reading selectable text from PDF. "
                "Extraction will be cached after this first run."
            )

            pages = extract_pdf_text_pages(pdf_bytes)

            progress_text.info(
                f"PDF loaded: {len(pages)} pages. "
                "Extracting singularity events..."
            )

            events, event_warnings = extract_subdivision_events(
                pages=pages,
                progress_bar=progress_bar,
                progress_text=progress_text,
            )

            progress_text.info(
                "Extracting Safety Variant Map records..."
            )

            variant_map, variant_warnings = extract_variant_maps(
                pages=pages,
                progress_bar=progress_bar,
                progress_text=progress_text,
            )

            warnings = event_warnings + variant_warnings

            st.session_state["generator_pdf_hash"] = current_hash
            st.session_state["generator_events"] = events
            st.session_state["generator_variant_map"] = variant_map
            st.session_state["generator_warnings"] = warnings
            st.session_state["generator_page_count"] = len(pages)

            st.session_state.pop("generator_register", None)
            st.session_state.pop("generator_docx_zip", None)

            progress_bar.progress(100)

            progress_text.success(
                f"Extraction completed: {len(events)} subdivision "
                f"event(s) and {len(variant_map)} variant-map row(s)."
            )

        except Exception as exc:
            progress_bar.empty()

            progress_text.error(
                f"PDF extraction failed: "
                f"{type(exc).__name__}: {exc}"
            )

            st.exception(exc)
            return

    events = st.session_state.get(
        "generator_events",
        pd.DataFrame(columns=EXTRACTED_EVENT_COLUMNS),
    )

    variant_map = st.session_state.get(
        "generator_variant_map",
        pd.DataFrame(columns=VARIANT_MAP_COLUMNS),
    )

    if events.empty:
        st.warning(
            "Click 'Extract Events and Variant Maps' to begin."
        )
        return

    warnings = st.session_state.get(
        "generator_warnings",
        [],
    )

    if warnings:
        with st.expander(
            f"Extraction Warnings ({len(warnings)})",
            expanded=False,
        ):
            for warning in warnings:
                st.write(f"• {warning}")

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric(
        "Subdivision Events",
        len(events),
    )

    metric2.metric(
        "Safety Variant Rows",
        len(variant_map),
    )

    metric3.metric(
        "PDF Pages",
        st.session_state.get("generator_page_count", 0),
    )

    with st.expander(
        "Review Extracted Subdivision Events",
        expanded=False,
    ):
        st.dataframe(
            events,
            use_container_width=True,
            hide_index=True,
        )

    with st.expander(
        "Review Extracted Safety Variant Map",
        expanded=False,
    ):
        if variant_map.empty:
            st.warning(
                "No Variant Map rows were extracted. The PDF may not "
                "contain selectable Variant Map text, or extraction "
                "format may require review."
            )
        else:
            st.dataframe(
                variant_map,
                use_container_width=True,
                hide_index=True,
            )

    # -------------------------------------------------------------------------
    # KP selection
    # -------------------------------------------------------------------------
    st.markdown("## 2. Select Start and End Point")

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
            "No valid abscissa values found for selected branch."
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

    # -------------------------------------------------------------------------
    # Register generation
    # -------------------------------------------------------------------------
    if st.button(
        "Create Variant-Linked Draft Test Register",
        type="primary",
        use_container_width=True,
        key="generator_create_register_button",
    ):
        st.session_state["generator_register"] = create_test_register(
            events=events,
            variant_map=variant_map,
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
            "No variant-linked low-speed test points were created in "
            "the selected range."
        )
        return

    # -------------------------------------------------------------------------
    # Review register
    # -------------------------------------------------------------------------
    st.markdown("## 3. Review Variant-Linked Test Register")

    st.caption(
        "The generator linked Safety Variant Map composition to the "
        "Subdivision associated variant. Verify and approve before "
        "generating DOCX test sheets."
    )

    reviewed_register = st.data_editor(
        register,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="generator_register_editor",
        column_config={
            "test_code": st.column_config.SelectboxColumn(
                "Test Code",
                options=[""] + list(TEST_DEFINITIONS.keys()),
            ),
            "needs_abscissa": st.column_config.CheckboxColumn(
                "Needs Abscissa"
            ),
            "abscissa_m": st.column_config.NumberColumn(
                "Participating Abscissa (m)",
                format="%.3f",
            ),
            "anticipate_distance_m": st.column_config.NumberColumn(
                "Anticipation Distance (m)",
                format="%.3f",
            ),
            "anticipate_abscissa_m": st.column_config.NumberColumn(
                "Candidate Anticipated Abscissa (m)",
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
                "Engineering Remarks",
                width="large",
            ),
        },
    )

    reviewed_register = apply_test_details(reviewed_register)

    st.session_state["generator_register"] = reviewed_register

    review_col1, review_col2, review_col3, review_col4 = st.columns(4)

    review_col1.metric(
        "Draft Test Sheets",
        len(reviewed_register),
    )

    review_col2.metric(
        "Assigned Tests",
        int(
            reviewed_register["test_code"]
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ),
    )

    review_col3.metric(
        "Rows Requiring Abscissa",
        int(
            reviewed_register["needs_abscissa"]
            .fillna(False)
            .astype(bool)
            .sum()
        ),
    )

    review_col4.metric(
        "Approved for Generation",
        int(
            reviewed_register["review_status"]
            .eq("Approved for generation")
            .sum()
        ),
    )

    # -------------------------------------------------------------------------
    # Downloads
    # -------------------------------------------------------------------------
    st.markdown("## 4. Generate and Download")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        extracted_events=events,
        variant_map=variant_map,
        project_info=project_info,
    )

    approved_register = reviewed_register[
        reviewed_register["test_code"]
        .astype(str)
        .str.strip()
        .isin(TEST_DEFINITIONS.keys())
        & reviewed_register["review_status"]
        .eq("Approved for generation")
    ].copy()

    download_col1, download_col2, download_col3 = st.columns(3)

    with download_col1:
        st.download_button(
            "⬇️ Download Excel Register",
            data=excel_bytes,
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_"
                f"Variant_Linked_Test_Register.xlsx"
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
                f"{project_info['sheet_prefix']}_"
                f"Variant_Linked_Test_Register.csv"
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
                "Set Review Status to 'Approved for generation' for "
                "one or more valid test rows."
            )

        else:
            if st.button(
                "📦 Generate DOCX Test Sheets",
                type="primary",
                use_container_width=True,
                key="generator_create_docx_button",
            ):
                docx_progress_text = st.empty()
                docx_progress_bar = st.progress(0)

                try:
                    docx_zip = create_docx_zip(
                        register=approved_register,
                        project_info=project_info,
                        progress_bar=docx_progress_bar,
                        progress_text=docx_progress_text,
                    )

                    st.session_state["generator_docx_zip"] = docx_zip

                except Exception as exc:
                    docx_progress_bar.empty()

                    docx_progress_text.error(
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
