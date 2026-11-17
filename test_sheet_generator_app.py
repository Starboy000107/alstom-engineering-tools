from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st


# =============================================================================
# CONFIGURATION
# =============================================================================

PROCEDURE_REFERENCE_DEFAULT = "C1252-TAC-0274"
PROCEDURE_TITLE_DEFAULT = "Trackside ATC Low-Speed Dynamic Test Procedure"

MAX_PDF_SIZE_MB = 100
MAX_EVIDENCE_LENGTH = 2200


# =============================================================================
# FIXED PROCEDURE TEST RULES
#
# These are procedure-level definitions.
# No station-specific sector, section, branch, abscissa, variant, equipment
# name, or location is hard-coded here.
#
# Anticipated data is intentionally excluded because this application accepts
# only a Subdivision PDF.
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
            (
                "Beacon Codes",
                "Record and compare with approved design",
                "Record",
            ),
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
            (
                "Beacon Codes",
                "Record and compare with approved design",
                "Record",
            ),
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
        ],
        "checks": [
            (
                "Track Circuit Occupied",
                "Associated variant restrictive",
                "Y / N",
            ),
            ("Available Mode", "CM/AUTO", "Y / N"),
            ("Localisation", "Record Loc Max", "m"),
            ("Target Distance", "Record", "m"),
            ("Singularity Abscissa", "Record", "m"),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            (
                "Track Circuit Free",
                "Associated variant permissive",
                "Y / N",
            ),
            (
                "Target Comparison",
                "Free target >= occupied target",
                "Y / N",
            ),
        ],
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "name": "Special Stopping Point – Signal Without Overlap",
        "short_name": "Special Stopping Point",
        "patterns": [
            r"SPECIAL\s+STOPPING\s+POINT",
            r"\bSSP\b",
        ],
        "checks": [
            (
                "Restrictive Signal",
                "Associated variant restrictive",
                "Y / N",
            ),
            ("Available Mode", "CM/AUTO", "Y / N"),
            (
                "Loc Max / Target Distance / Abscissa",
                "Record values",
                "m",
            ),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            (
                "Permissive Signal",
                "Associated variant permissive",
                "Y / N",
            ),
            (
                "Target Comparison",
                "Permissive target >= restrictive",
                "Y / N",
            ),
        ],
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "name": "Complex Stopping Point – Signal With Overlap",
        "short_name": "Complex Stopping Point",
        "patterns": [
            r"COMPLEX\s+STOPPING\s+POINT",
            r"SIGNAL.*OVERLAP",
        ],
        "checks": [
            (
                "Signal Red / Overlap Occupied",
                "Variants restrictive",
                "Y / N",
            ),
            (
                "Signal Red / Overlap Free",
                "Record stopping behaviour",
                "Record",
            ),
            (
                "Signal Blue / Overlap Free",
                "Variants permissive",
                "Y / N",
            ),
            (
                "Target Comparison",
                "Permissive target >= restrictive",
                "Y / N",
            ),
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
        ],
        "checks": [
            (
                "Point Uncontrolled",
                "Associated variant restrictive",
                "Y / N",
            ),
            ("Available Mode", "CM/AM", "Y / N"),
            (
                "Loc Max / Target Distance / Abscissa",
                "Record",
                "m",
            ),
            (
                "Point Controlled",
                "Associated variant permissive",
                "Y / N",
            ),
            (
                "Target Comparison",
                "Controlled >= uncontrolled",
                "Y / N",
            ),
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
            (
                "Available Mode Before End of SACEM",
                "CM/AUTO",
                "Y / N",
            ),
            (
                "Loc Max / Target Distance / Abscissa",
                "Record",
                "m",
            ),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            (
                "Beyond End of SACEM",
                "CM/AM unavailable",
                "Y / N",
            ),
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
        ],
        "checks": [
            ("Localisation Status", "Total Loc", "Y / N"),
            ("Available Mode", "CM/AM", "Y / N"),
            ("Selected Mode", "CM", "Y / N"),
            (
                "Precise Stop",
                "Record Loc Max and measured distance",
                "m",
            ),
            ("Door Side", "Correct side enabled", "A / B"),
            (
                "Stop Short / Overrun",
                "Door enabled within approved tolerance",
                "Y / N",
            ),
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
            (
                "Open Wrong-Side Doors",
                "PSD / relay not activated",
                "Y / N",
            ),
            (
                "Open Correct-Side Doors",
                "PSD / relay activated",
                "Y / N",
            ),
            (
                "All Doors Closed Lost",
                "Variants restrictive; RM only",
                "Y / N",
            ),
            (
                "PIOS / Override Active",
                "CM/AM and timeout checked",
                "Y / N",
            ),
            (
                "Close Correct-Side Doors",
                "PSD closed and variants permissive",
                "Y / N",
            ),
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
            r"REPEAT\s+ENTRANCE\s+EMERGENCY",
            r"ESA\s+TEMP\s+POINT",
        ],
        "checks": [
            (
                "ESZ Condition Active",
                "Associated variant restrictive",
                "Y / N",
            ),
            (
                "Loc Max / Target Distance / Abscissa",
                "Record",
                "m",
            ),
            (
                "ESZ Condition Resumed",
                "Associated variant permissive",
                "Y / N",
            ),
            (
                "Inside Restrictive Zone",
                "CM/AM unavailable",
                "Y / N",
            ),
            (
                "End of ESZ",
                "CM becomes available",
                "Y / N",
            ),
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
        ],
        "checks": [
            (
                "Cab 1 Arrival",
                "Total Loc and CM/AM available",
                "Y / N",
            ),
            ("Cab 1 Loc Max / Loc Min", "Record", "m"),
            (
                "Cab 2 Before Change of End",
                "Shutdown and distance to signal checked",
                "Y / N",
            ),
            (
                "Cab 2 After Change of End",
                "Total Loc and CM/AM available",
                "Y / N",
            ),
            (
                "First RB / MTIB",
                "No delocalisation / no emergency brake",
                "Y / N",
            ),
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
            (
                "Train Behaviour",
                "Target speed follows TSR; no emergency brake",
                "Y / N",
            ),
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
            (
                "Return Channel Loop",
                "Record loop / equipment reference",
                "Record",
            ),
            (
                "Trackside Reception",
                "Train information received",
                "Y / N",
            ),
        ],
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "name": "End of Shunt Test",
        "short_name": "End of Shunt",
        "patterns": [
            r"END\s+OF\s+SHUNT",
            r"SHUNT\s+ROUTE",
        ],
        "checks": [
            ("Shunt Route Name", "Record", "Record"),
            (
                "Step 1 – Begin",
                "Record branch, Loc Max and target distance",
                "Record",
            ),
            ("Step 2", "Train in RM", "Y / N"),
            (
                "Step 3 – End",
                "Record CM-resume branch and Loc Max",
                "Record",
            ),
            (
                "Acceptance",
                "CM/AM resumes at approved location",
                "Y / N",
            ),
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
    "inter_singularity_m",
    "row_number",
    "singularity_code",
    "event_name",
    "associated_variant",
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
    """Safely convert a value to a stripped string."""
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
    """Return a filename safe for Windows/macOS/Linux."""
    value = re.sub(r'[\\/:*?"<>|]', "_", safe_text(value))
    value = re.sub(r"\s+", "_", value)
    return value[:160] or "ATC_Test_Sheet"


def get_file_hash(data: bytes) -> str:
    """Hash PDF content to reset state when the uploaded PDF changes."""
    return hashlib.sha256(data).hexdigest()


def to_float(value: Any) -> Optional[float]:
    """Convert PDF number formats such as 2,191.0 m into float."""
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
    """Format a numerical value as metres."""
    numeric = to_float(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


def normalise_subsection(value: str) -> str:
    """
    Standardise a subsection reference.

    Examples:
        9-1-0      -> 09-01-0
        09 01 2    -> 09-01-2
        09-01-2    -> 09-01-2
    """
    match = re.search(
        r"(\d{1,2})\s*[- ]\s*(\d{1,2})\s*[- ]\s*(\d{1,2})",
        safe_text(value),
    )

    if not match:
        return safe_text(value)

    sector, section, subsection = match.groups()

    return f"{int(sector):02d}-{int(section):02d}-{int(subsection)}"


# =============================================================================
# PDF TEXT EXTRACTION
# =============================================================================

@st.cache_data(show_spinner=False, max_entries=4)
def extract_pdf_text_pages(pdf_bytes: bytes) -> List[str]:
    """
    Extract selectable PDF text with PyMuPDF.

    Image-only or scanned PDFs require OCR and are not supported by this
    selectable-text extraction function.
    """
    document = fitz.open(stream=pdf_bytes, filetype="pdf")

    pages: List[str] = []

    for page in document:
        pages.append(page.get_text("text") or "")

    document.close()

    return pages


# =============================================================================
# PDF PAGE CLASSIFICATION / METADATA
# =============================================================================

def is_subdivision_page(page_text: str) -> bool:
    """Identify Appendix J singularity table pages."""
    upper = page_text.upper()

    return any(
        marker in upper
        for marker in [
            "SINGULARITIES",
            "SINGULARITY",
            "INTER SINGUL",
            "TEST ABSC",
            "SUB SECTION",
            "UP TRACK NORMAL DIRECTION",
            "DN TRACK NORMAL DIRECTION",
            "DOWN TRACK NORMAL DIRECTION",
        ]
    )


def page_metadata(page_text: str) -> Dict[str, str]:
    """Extract sector, section and direction from a Subdivision PDF page."""
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

    elif re.search(
        r"\b(?:DN|DOWN)\s+TRACK\b",
        text,
        flags=re.IGNORECASE,
    ):
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


# =============================================================================
# EVENT EXTRACTION
# =============================================================================

def extract_associated_variant(text: str) -> str:
    """Extract Associated Variant wording from a singularity context."""
    match = re.search(
        r"ASSOCIATED\s+VARIANTS?"
        r"(?:\s+NUMBER)?\s*[:=]?\s*"
        r"(.+?)"
        r"(?="
        r"$|"
        r"RANK\s+OF\s+FIRST|"
        r"NUMBER\s+OF\s+ANTICIPATED|"
        r"SUB\s*-?\s*SECTION|"
        r"SPACING\s+STOPPING|"
        r"SPECIAL\s+STOPPING|"
        r"COMPLEX\s+STOPPING|"
        r"BEACON\s+TYPE"
        r")",
        normalise_spaces(text),
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    return normalise_spaces(match.group(1)).strip("() ")


def extract_location_name(text: str) -> str:
    """Extract asset/location name where one exists in the source text."""
    patterns = [
        r"RETURN\s+CHANNEL\s*(?:NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"(?:SIGNAL|SIG)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"(?:POINT|TURNOUT)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"(?:TRACK\s*CIRCUIT|T/C)\s*(?:NO\.?|NUMBER)?\s*[:=]?\s*[A-Z0-9/_ -]+",
        r"ESA\s+TEMP\s+POINT\s*\d+",
        r"EMERGENCY\s+ZONE\s+ENDS",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return normalise_spaces(match.group(0))

    return ""


def infer_event_name(text: str) -> str:
    """Produce an event label from PDF source wording."""
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
        ("PERMANENT SPEED RESTRICTION", "Speed Restriction"),
        ("BEACON", "Beacon"),
    ]

    for phrase, label in labels:
        if phrase in upper:
            return label

    return "Subdivision Event"


def identify_tests(source_text: str) -> List[str]:
    """Identify applicable fixed procedure test types."""
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
    Parse a text-extracted Appendix-J row.

    Typical format:

        45 ! 4.8 m 2,191.0 11 Associated variant : (TUC KF T/C)

    This can vary substantially between PDF generators; the surrounding
    event context is checked before creating any test-sheet record.
    """
    pattern = re.compile(
        r"^\s*(?P<row>\d{1,3})\s+"
        r"(?:!\s*)?"
        r"(?:(?P<inter>-?\d+(?:[,.]\d+)?)\s*m?\s+)?"
        r"(?P<abscissa>-?\d+(?:[,.]\d+)?)\s+"
        r"(?P<code>\d+(?:_\d+)?)\s+"
        r"(?P<remaining>.+)$",
        flags=re.IGNORECASE,
    )

    match = pattern.match(normalise_spaces(line))

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


def extract_test_register_from_pdf(
    pages: List[str],
    progress_bar: Any,
    progress_text: Any,
) -> pd.DataFrame:
    """
    Extract only test-sheet candidate locations from a Subdivision PDF.

    Anticipated location information is intentionally excluded.
    """
    records: List[Dict[str, Any]] = []
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

        metadata = page_metadata(page_text)

        lines = [
            normalise_spaces(line)
            for line in page_text.splitlines()
            if normalise_spaces(line)
        ]

        current_subsection = ""

        for line_index, line in enumerate(lines):
            subsection_match = re.search(
                r"SUB\s*-?\s*SECTION\s*(?:NUMBER)?\s*[:=]?\s*"
                r"(\d{1,2}\s*[- ]\s*\d{1,2}\s*[- ]\s*\d{1,2})",
                line,
                flags=re.IGNORECASE,
            )

            if subsection_match:
                current_subsection = normalise_subsection(
                    subsection_match.group(1)
                )

            parsed_row = parse_singularity_row(line)

            if parsed_row is None:
                continue

            # A nearby context window handles PDFs that split singularity
            # row content across several extracted text lines.
            context_start = max(0, line_index - 2)
            context_end = min(len(lines), line_index + 4)

            context = normalise_spaces(
                " ".join(lines[context_start:context_end])
            )

            matched_tests = identify_tests(context)

            if not matched_tests:
                continue

            subsection = current_subsection

            # Fallback if a subsection can be identified in nearby content.
            if not subsection:
                inferred_match = re.search(
                    r"\b(\d{1,2}\s*-\s*\d{1,2}\s*[- ]\s*\d{1,2})\b",
                    context,
                )

                if inferred_match:
                    subsection = normalise_subsection(
                        inferred_match.group(1)
                    )

            sector = metadata["sector"]
            section = metadata["section"]

            if subsection:
                subsection_parts = subsection.split("-")

                if len(subsection_parts) == 3:
                    sector = subsection_parts[0]
                    section = subsection_parts[1]

            for test_code in matched_tests:
                test_rule = TEST_RULES[test_code]

                records.append(
                    {
                        "test_code": test_code,
                        "test_id": test_rule["id"],
                        "test_name": test_rule["name"],
                        "sector": sector,
                        "section": section,
                        "subsection": subsection,
                        # Subdivision PDFs often give subsection but not full
                        # branch. We do not invent a branch number.
                        "branch": subsection,
                        "direction": metadata["direction"],
                        "abscissa_m": parsed_row["abscissa_m"],
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
                        "source_evidence": context[
                            :MAX_EVIDENCE_LENGTH
                        ],
                        "review_status": "To be reviewed",
                        "remarks": (
                            "Generated from Subdivision PDF only. "
                            "Anticipated branch, sub-section, variant, "
                            "distance and abscissa are not included."
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
            "abscissa_m",
            "subsection",
        ]
    )

    dataframe = dataframe.sort_values(
        by=[
            "sector",
            "section",
            "subsection",
            "abscissa_m",
            "test_code",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return dataframe[REGISTER_COLUMNS]


def apply_test_details(register: pd.DataFrame) -> pd.DataFrame:
    """Refresh ID and name after a manual test-code change."""
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
# DOCX GENERATION
# =============================================================================

def set_cell_text(
    cell: Any,
    value: Any,
    bold: bool = False,
) -> None:
    """Write text to a python-docx table cell."""
    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(value))
    run.bold = bold


def add_docx_row(
    table: Any,
    values: List[Any],
    bold: bool = False,
) -> None:
    """Add a row to a DOCX table."""
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(
            cells[index],
            value,
            bold=bold,
        )


def set_table_font_size(
    table: Any,
    point_size: int = 8,
) -> None:
    """Set a consistent font size in all table cells."""
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
    """
    Generate one Word test sheet.

    No anticipated fields are rendered because only the Subdivision PDF is
    uploaded in this application.
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency python-docx. "
            "Install python-docx>=1.1.2."
        ) from exc

    test_code = safe_text(row.get("test_code"))

    if test_code not in TEST_RULES:
        raise ValueError(f"Unknown test code: {test_code}")

    test_rule = TEST_RULES[test_code]

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.35)
    section.right_margin = Inches(0.35)

    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(8)

    # -------------------------------------------------------------------------
    # Header
    # -------------------------------------------------------------------------
    header = document.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER

    company_name = header.add_run("ALSTOM\n")
    company_name.bold = True
    company_name.font.size = Pt(16)

    procedure_title = header.add_run(
        project_info["procedure_title"]
    )
    procedure_title.bold = True
    procedure_title.font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{test_rule['name']} ({test_rule['id']})"
    )
    title_run.bold = True
    title_run.font.size = Pt(12)

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # General information table
    # -------------------------------------------------------------------------
    information_table = document.add_table(rows=0, cols=6)
    information_table.style = "Table Grid"

    add_docx_row(
        information_table,
        [
            "Project / Contract:",
            project_info["project"],
            "Procedure Reference:",
            project_info["procedure_reference"],
            "Test Sheet No.:",
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

    # -------------------------------------------------------------------------
    # Location table
    # -------------------------------------------------------------------------
    subtitle = document.add_paragraph()
    subtitle.add_run(test_rule["short_name"]).bold = True

    location_table = document.add_table(rows=0, cols=4)
    location_table.style = "Table Grid"

    add_docx_row(
        location_table,
        [
            "Sector:",
            safe_text(row.get("sector")),
            "Direction:",
            safe_text(row.get("direction")) or "Not stated",
        ],
    )

    add_docx_row(
        location_table,
        [
            "Section:",
            safe_text(row.get("section")),
            "Sub-section:",
            safe_text(row.get("subsection")) or "Not stated",
        ],
    )

    add_docx_row(
        location_table,
        [
            "Branch Reference:",
            safe_text(row.get("branch")) or "Not stated",
            "Abscissa:",
            format_distance(row.get("abscissa_m")) or "Not stated",
        ],
    )

    add_docx_row(
        location_table,
        [
            "Event:",
            safe_text(row.get("event_name")),
            "Location / Asset:",
            safe_text(row.get("location_name")),
        ],
    )

    add_docx_row(
        location_table,
        [
            "Associated Variant:",
            safe_text(row.get("associated_variant")) or "Not stated",
            "Source PDF Page:",
            safe_text(row.get("source_page")),
        ],
    )

    set_table_font_size(location_table)

    note = document.add_paragraph()
    note_run = note.add_run(
        "Anticipated branch, sub-section, variant, distance and abscissa "
        "are not included because this document was generated from the "
        "Subdivision PDF only."
    )
    note_run.italic = True
    note_run.font.size = Pt(8)

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Test checks table
    # -------------------------------------------------------------------------
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

    # -------------------------------------------------------------------------
    # Evidence / comments
    # -------------------------------------------------------------------------
    comments_heading = document.add_paragraph()
    comments_heading.add_run(
        "Source Evidence / Engineering Remarks"
    ).bold = True

    comments_table = document.add_table(rows=1, cols=1)
    comments_table.style = "Table Grid"

    comment_text = (
        f"Source Page: {safe_text(row.get('source_page'))}\n"
        f"Singularity Row: {safe_text(row.get('row_number'))}\n"
        f"Singularity Code: {safe_text(row.get('singularity_code'))}\n"
        f"Event: {safe_text(row.get('event_name'))}\n\n"
        f"PDF Source Evidence:\n"
        f"{safe_text(row.get('source_evidence'))}\n\n"
        f"Engineering Remarks:\n"
        f"{safe_text(row.get('remarks'))}"
    )

    set_cell_text(
        comments_table.rows[0].cells[0],
        comment_text,
    )

    set_table_font_size(comments_table)

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Approval table
    # -------------------------------------------------------------------------
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
    """Build a ZIP archive of approved Word test sheets."""
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
            subsection = safe_text(row.get("subsection")) or "NO_SUBSECTION"

            progress_text.info(
                f"Generating test sheet {sequence} of {total}: "
                f"{test_code} / {subsection}"
            )

            sheet_number = (
                f"{project_info['sheet_prefix']}-{sequence:03d}"
            )

            filename = clean_filename(
                f"{sheet_number}_{test_code}_{subsection}_"
                f"KP_{safe_text(row.get('abscissa_m'))}.docx"
            )

            document_bytes = create_docx_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            archive.writestr(filename, document_bytes)

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
    project_info: Dict[str, str],
) -> bytes:
    """Create an Excel test register."""
    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        cover = pd.DataFrame(
            [
                ["Project / Contract", project_info["project"]],
                [
                    "Procedure Reference",
                    project_info["procedure_reference"],
                ],
                ["Procedure Title", project_info["procedure_title"]],
                ["Generated Date", str(date.today())],
                ["Test Register Rows", len(register)],
                [
                    "Anticipated Data",
                    "Not included – Subdivision PDF only",
                ],
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
# STREAMLIT USER INTERFACE
# =============================================================================

def initialise_test_sheet_state() -> None:
    """Initialise namespaced session-state entries."""
    defaults = {
        "test_sheet_pdf_hash": "",
        "test_sheet_register": pd.DataFrame(
            columns=REGISTER_COLUMNS
        ),
        "test_sheet_page_count": 0,
        "test_sheet_docx_zip": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_test_sheet_generator_app() -> None:
    """
    Render the Test Sheet Generator page.

    Important:
    st.set_page_config() is intentionally NOT used here because it is already
    defined in the project's main app.py.
    """
    initialise_test_sheet_state()

    st.title("ATC Low-Speed Dynamic Test Sheet Generator")

    st.warning(
        "Engineering control: this tool produces draft test sheets. "
        "The user must verify all extracted locations, variants, abscissae, "
        "test applicability and approved project information before use."
    )

    st.info(
        "Input requirement: upload one Station SACEM Subdivision PDF. "
        "Anticipated information is excluded because it requires a separate "
        "approved anticipation/transmission source document."
    )

    # -------------------------------------------------------------------------
    # Project information
    # -------------------------------------------------------------------------
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
        "sheet_prefix": (
            sheet_prefix
            or PROCEDURE_REFERENCE_DEFAULT
        ),
    }

    # -------------------------------------------------------------------------
    # PDF upload
    # -------------------------------------------------------------------------
    st.markdown("## 1. Upload Subdivision PDF")

    uploaded_pdf = st.file_uploader(
        "Upload Appendix J / SACEM Subdivision PDF",
        type=["pdf"],
        accept_multiple_files=False,
        key="test_sheet_subdivision_pdf",
    )

    if uploaded_pdf is None:
        st.info(
            "Upload a selectable-text SACEM Subdivision PDF containing "
            "singularity tables."
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

    if current_pdf_hash != st.session_state["test_sheet_pdf_hash"]:
        st.session_state["test_sheet_pdf_hash"] = current_pdf_hash
        st.session_state["test_sheet_register"] = pd.DataFrame(
            columns=REGISTER_COLUMNS
        )
        st.session_state["test_sheet_page_count"] = 0
        st.session_state["test_sheet_docx_zip"] = None

    # -------------------------------------------------------------------------
    # Extraction
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
                "Reading selectable text from the Subdivision PDF..."
            )

            pages = extract_pdf_text_pages(pdf_bytes)

            st.session_state["test_sheet_page_count"] = len(pages)

            progress_text.info(
                f"PDF loaded: {len(pages)} page(s). "
                "Extracting low-speed test points..."
            )

            register = extract_test_register_from_pdf(
                pages=pages,
                progress_bar=progress_bar,
                progress_text=progress_text,
            )

            st.session_state["test_sheet_register"] = register
            st.session_state["test_sheet_docx_zip"] = None

            if register.empty:
                progress_text.warning(
                    "No low-speed test points were identified. "
                    "The PDF may be image-only, may not contain selectable "
                    "text, or its event wording/table format may need "
                    "additional parsing rules."
                )
            else:
                progress_text.success(
                    f"Extraction complete: {len(register)} draft "
                    f"test sheet record(s) found."
                )

        except Exception as exc:
            progress_bar.empty()
            progress_text.error(
                f"PDF extraction failed: "
                f"{type(exc).__name__}: {exc}"
            )
            st.exception(exc)
            return

    register = st.session_state["test_sheet_register"]

    if register.empty:
        st.warning(
            "Click 'Extract Low-Speed Test Points' to create the draft "
            "register."
        )
        return

    # -------------------------------------------------------------------------
    # Metrics
    # -------------------------------------------------------------------------
    metric_1, metric_2, metric_3 = st.columns(3)

    metric_1.metric(
        "Draft Test Sheets",
        len(register),
    )

    metric_2.metric(
        "PDF Pages",
        st.session_state["test_sheet_page_count"],
    )

    metric_3.metric(
        "Test Types Detected",
        register["test_code"].nunique(),
    )

    # -------------------------------------------------------------------------
    # Review register
    # -------------------------------------------------------------------------
    st.markdown("## 2. Review and Approve Test Register")

    st.caption(
        "Edit extracted values where required. Only rows marked "
        "'Approved for generation' will be included in the Word-document ZIP."
    )

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
                "Abscissa (m)",
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
            "remarks": st.column_config.TextColumn(
                "Engineering Remarks",
                width="large",
            ),
        },
    )

    reviewed_register = apply_test_details(reviewed_register)

    st.session_state["test_sheet_register"] = reviewed_register

    review_metric_1, review_metric_2, review_metric_3 = st.columns(3)

    review_metric_1.metric(
        "Approved for Generation",
        int(
            reviewed_register["review_status"]
            .eq("Approved for generation")
            .sum()
        ),
    )

    review_metric_2.metric(
        "Rows with Abscissa",
        int(
            reviewed_register["abscissa_m"]
            .notna()
            .sum()
        ),
    )

    review_metric_3.metric(
        "Rows with Associated Variant",
        int(
            reviewed_register["associated_variant"]
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ),
    )

    # -------------------------------------------------------------------------
    # Exports
    # -------------------------------------------------------------------------
    st.markdown("## 3. Download Register and DOCX Test Sheets")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        project_info=project_info,
    )

    approved_register = reviewed_register[
        reviewed_register["review_status"]
        .eq("Approved for generation")
        & reviewed_register["test_code"]
        .isin(TEST_RULES.keys())
    ].copy()

    download_col_1, download_col_2, download_col_3 = st.columns(3)

    with download_col_1:
        st.download_button(
            "⬇️ Download Excel Register",
            data=excel_bytes,
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_"
                f"Subdivision_Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
            key="test_sheet_download_excel",
        )

    with download_col_2:
        st.download_button(
            "⬇️ Download CSV Register",
            data=reviewed_register.to_csv(
                index=False
            ).encode("utf-8"),
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_"
                f"Subdivision_Test_Register.csv"
            ),
            mime="text/csv",
            use_container_width=True,
            key="test_sheet_download_csv",
        )

    with download_col_3:
        if approved_register.empty:
            st.button(
                "📦 Generate DOCX Test Sheets",
                disabled=True,
                use_container_width=True,
                key="test_sheet_generate_docx_disabled",
            )

            st.caption(
                "Set Review Status to 'Approved for generation' for "
                "at least one valid test row."
            )

        else:
            if st.button(
                f"📦 Generate {len(approved_register)} DOCX Test Sheet(s)",
                type="primary",
                use_container_width=True,
                key="test_sheet_generate_docx",
            ):
                progress_text = st.empty()
                progress_bar = st.progress(0)

                try:
                    docx_zip = create_docx_zip(
                        register=approved_register,
                        project_info=project_info,
                        progress_bar=progress_bar,
                        progress_text=progress_text,
                    )

                    st.session_state["test_sheet_docx_zip"] = docx_zip

                except Exception as exc:
                    progress_bar.empty()
                    progress_text.error(
                        f"DOCX generation failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    st.exception(exc)

            docx_zip = st.session_state.get(
                "test_sheet_docx_zip"
            )

            if docx_zip:
                st.download_button(
                    "⬇️ Download DOCX Test Sheets ZIP",
                    data=docx_zip,
                    file_name=clean_filename(
                        f"{project_info['sheet_prefix']}_"
                        f"ATC_Low_Speed_Test_Sheets.zip"
                    ),
                    mime="application/zip",
                    use_container_width=True,
                    key="test_sheet_download_docx_zip",
                )
