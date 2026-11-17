from __future__ import annotations

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
# PROCEDURE TEST DEFINITIONS
#
# These are generic procedure rules, not station-specific hard coding.
# Station KP, branch, variant and anticipated information comes from PDF data.
# =============================================================================

TEST_RULES: Dict[str, Dict[str, Any]] = {
    "LS01": {
        "id": "ATC-TNC-TRA-LS01",
        "name": "MTIB Initialization Test",
        "short_name": "MTIB",
        "needs_abscissa": False,
        "patterns": [
            r"\bMTIB\b",
            r"MOVING\s+TRAIN\s+INITIALISATION",
            r"MOVING\s+TRAIN\s+INITIALIZATION",
        ],
        "checks": [
            ("Selected Mode", "RM", "Y / N"),
            ("Localisation Status", "Out Loc", "Y / N"),
            ("Cross MTIB", "Localisation becomes Wait Loc", "Y / N"),
            ("Beacon codes", "Record received codes and compare with design", "Record"),
            ("Calibration", "Record A-car number and Cal. Max", "mm"),
            ("Localisation Status", "Total Loc", "Y / N"),
        ],
    },
    "LS02": {
        "id": "ATC-TNC-TRA-LS02",
        "name": "STIB Initialization Test",
        "short_name": "STIB",
        "needs_abscissa": False,
        "patterns": [
            r"\bSTIB\b",
            r"STATIONARY\s+TRAIN\s+INITIALISATION",
            r"STATIONARY\s+TRAIN\s+INITIALIZATION",
        ],
        "checks": [
            ("Selected Mode", "RM", "Y / N"),
            ("Localisation Status", "Out Loc", "Y / N"),
            ("Stop on STIB", "Localisation becomes Wait Inv", "Y / N"),
            ("Beacon codes", "Record received codes and compare with design", "Record"),
            ("Localisation Status", "Total Loc / Wait Beacon as applicable", "Y / N"),
            ("Mode Available", "CM/AM available where required", "Y / N"),
            ("First RB / MTIB", "No Emergency Brake", "Y / N"),
        ],
    },
    "LS03": {
        "id": "ATC-TNC-TRA-LS03",
        "name": "Spacing Stopping Point Test – Track Circuit",
        "short_name": "Spacing Stopping Point",
        "needs_abscissa": True,
        "patterns": [
            r"SPACING\s+STOPPING\s+POINT",
            r"\bT/C\b.*STOPPING",
            r"TRACK\s+CIRCUIT.*STOPPING",
        ],
        "checks": [
            ("Track circuit occupied", "Associated variant restrictive", "Y / N"),
            ("Available mode", "CM/AUTO", "Y / N"),
            ("Loc Max", "Record", "m"),
            ("Target distance", "Record", "m"),
            ("Singularity abscissa", "Record", "m"),
            ("OMAP target speed", "0 when applicable", "Y / N"),
            ("Track circuit free", "Associated variant permissive", "Y / N"),
            ("Target distance comparison", "Free target distance >= occupied target distance", "Y / N"),
        ],
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "name": "Special Stopping Point Test – Signal Without Overlap",
        "short_name": "Special Stopping Point",
        "needs_abscissa": True,
        "patterns": [
            r"SPECIAL\s+STOPPING\s+POINT",
            r"SIGNAL.*WITHOUT\s+OVERLAP",
        ],
        "checks": [
            ("Restrictive signal", "Associated variant restrictive", "Y / N"),
            ("Available mode", "CM/AUTO", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record values", "m"),
            ("OMAP target speed", "0 when applicable", "Y / N"),
            ("Permissive signal", "Associated variant permissive", "Y / N"),
            ("Target distance comparison", "Permissive >= restrictive", "Y / N"),
        ],
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "name": "Complex Stopping Point Test – Signal With Overlap",
        "short_name": "Complex Stopping Point",
        "needs_abscissa": True,
        "patterns": [
            r"COMPLEX\s+STOPPING\s+POINT",
            r"SIGNAL.*OVERLAP",
            r"\bO/L\b",
        ],
        "checks": [
            ("Signal red and overlap occupied", "Signal and overlap variants restrictive", "Y / N"),
            ("Signal red and overlap free", "Record stopping behaviour", "Record"),
            ("Signal blue and overlap free", "Signal and overlap variants permissive", "Y / N"),
            ("Target distance comparison", "Permissive >= restrictive", "Y / N"),
        ],
    },
    "LS06": {
        "id": "ATC-TNC-TRA-LS06",
        "name": "Stopping Point Test – Point",
        "short_name": "Convergence / Divergence",
        "needs_abscissa": True,
        "patterns": [
            r"\bCONVERGENCE\b",
            r"\bDIVERGENCE\b",
            r"\bPOINT\b",
            r"\bTURNOUT\b",
        ],
        "checks": [
            ("Point uncontrolled", "Associated variant restrictive", "Y / N"),
            ("Available mode", "CM/AM", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record values", "m"),
            ("Point controlled", "Associated variant permissive", "Y / N"),
            ("Target distance comparison", "Controlled >= uncontrolled", "Y / N"),
        ],
    },
    "LS07": {
        "id": "ATC-TNC-TRA-LS07",
        "name": "Stopping Point Test – End of SACEM",
        "short_name": "End of SACEM",
        "needs_abscissa": True,
        "patterns": [
            r"END\s+OF\s+SACEM",
            r"END\s+SACEM",
        ],
        "checks": [
            ("Available mode before End of SACEM", "CM/AUTO", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record values", "m"),
            ("OMAP target speed", "0 when applicable", "Y / N"),
            ("Beyond End of SACEM", "CM/AM unavailable", "Y / N"),
        ],
    },
    "LS08": {
        "id": "ATC-TNC-TRA-LS08",
        "name": "Station Stopping Point and Door Side Verification",
        "short_name": "Station Stopping Point / Door Side",
        "needs_abscissa": True,
        "patterns": [
            r"STATION\s+CHARACTERISTICS",
            r"STATION\s+STOPPING",
            r"PLATFORM\s+STOPPING",
        ],
        "checks": [
            ("Localisation status", "Total Loc", "Y / N"),
            ("Available mode", "CM/AM", "Y / N"),
            ("Selected mode", "CM", "Y / N"),
            ("Precise stop", "Record Loc Max and measured distance", "m"),
            ("Door side", "Correct side enabled", "A / B"),
            ("Stop short / overrun", "Door enabled within ±0.35 m", "Y / N"),
        ],
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "name": "PSD Operation Pre-Test",
        "short_name": "PSD Opening Pre-Test",
        "needs_abscissa": False,
        "patterns": [
            r"\bPSD\b",
            r"ALL\s+DOOR\s+CLOSED",
        ],
        "checks": [
            ("Correct platform side", "Door side enabled", "Y / N"),
            ("Open wrong-side doors", "PSD / relay not activated", "Y / N"),
            ("Open correct-side doors", "PSD / relay activated", "Y / N"),
            ("All doors closed lost", "Variants restrictive; RM only", "Y / N"),
            ("PIOS / override active", "CM/AM available then timeout behaviour checked", "Y / N"),
            ("Close correct-side doors", "PSD / relay closed and variants permissive", "Y / N"),
        ],
    },
    "LS10": {
        "id": "ATC-TNC-TRA-LS10",
        "name": "Emergency Stop Zone Test",
        "short_name": "Emergency Stop Zone",
        "needs_abscissa": True,
        "patterns": [
            r"EMERGENCY\s+ZONE",
            r"EMERGENCY\s+STOP\s+ZONE",
            r"\bESZ\b",
        ],
        "checks": [
            ("ESZ condition active", "Associated variant restrictive", "Y / N"),
            ("Loc Max / Target Distance / Abscissa", "Record values", "m"),
            ("ESZ condition resumed", "Associated variant permissive", "Y / N"),
            ("Inside restrictive zone", "CM/AM unavailable", "Y / N"),
            ("End of ESZ", "CM becomes available", "Y / N"),
        ],
    },
    "LS11": {
        "id": "ATC-TNC-TRA-LS11",
        "name": "Change of End Test",
        "short_name": "Change of End Zone",
        "needs_abscissa": True,
        "patterns": [
            r"CHANGE\s+OF\s+END",
            r"CHANGE\s+ENDS",
            r"REVERSE\s+SUB-?SECTION",
            r"\bCOE\b",
        ],
        "checks": [
            ("Cab 1 arrival", "Total Loc and CM/AM available", "Y / N"),
            ("Cab 1 Loc Max / Loc Min", "Record", "m"),
            ("Cab 2 before CoE", "Shutdown and distance to signal < 12 m", "Y / N"),
            ("Cab 2 after CoE", "Total Loc and CM/AM available", "Y / N"),
            ("First RB / MTIB", "No delocalisation / no EB", "Y / N"),
        ],
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "name": "Temporary Speed Restriction Test",
        "short_name": "Temporary Speed Restriction",
        "needs_abscissa": False,
        "patterns": [
            r"\bTSR\b",
            r"TEMPORARY\s+SPEED\s+RESTRICTION",
        ],
        "checks": [
            ("Track Circuit", "Record selected TC", "Record"),
            ("Section", "Record selected section", "Record"),
            ("TSR value", "Record TSR value", "kph"),
            ("Train behaviour", "Target speed follows TSR; no EB", "Y / N"),
        ],
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "short_name": "Return Channel Test",
        "needs_abscissa": False,
        "patterns": [
            r"RETURN\s+CHANNEL",
            r"RETURN\s+CHANNEL\s+NUMBER",
            r"R/C\s+LOOP",
        ],
        "checks": [
            ("Return channel location", "Record location", "Record"),
            ("Return channel loop", "Record loop / equipment reference", "Record"),
            ("Trackside reception", "Train information received by trackside", "Y / N"),
        ],
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "name": "End of Shunt Test",
        "short_name": "End of Shunt Test",
        "needs_abscissa": False,
        "patterns": [
            r"END\s+OF\s+SHUNT",
            r"SHUNT\s+ROUTE",
            r"SHUNT.*CM.*RESUME",
        ],
        "checks": [
            ("Shunt route name", "Record", "Record"),
            ("Step 1 – Begin", "Record Branch, Loc Max and Target Distance", "Record"),
            ("Step 2", "Train in RM", "Y / N"),
            ("Step 3 – End", "Record CM resume Branch and Loc Max", "Record"),
            ("Acceptance", "CM/AM resumes at approved End of Shunt position", "Y / N"),
        ],
    },
}


# =============================================================================
# BASIC HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (ValueError, TypeError):
        pass

    return str(value).strip()


def normalise_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", safe_text(value)).strip()


def clean_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", safe_text(value))
    value = re.sub(r"\s+", "_", value)
    return value[:150] or "ATC_Test_Sheet"


def to_float(value: Any) -> Optional[float]:
    text = safe_text(value)

    if not text:
        return None

    text = text.replace(",", "").replace("m", "").strip()

    try:
        return float(text)
    except ValueError:
        return None


def normalise_branch(value: Any) -> str:
    text = safe_text(value)

    match = re.search(
        r"(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,3})",
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
    parts = safe_text(branch).split("-")

    sector = parts[0] if len(parts) > 0 else ""
    section = parts[1] if len(parts) > 1 else ""
    subsection = "-".join(parts[:3]) if len(parts) > 2 else ""

    return sector, section, subsection


def find_first_number(text: str) -> Optional[float]:
    match = re.search(r"-?\d{1,7}(?:[.,]\d{1,3})?", safe_text(text))

    if not match:
        return None

    return to_float(match.group(0))


# =============================================================================
# PDF EXTRACTION
# =============================================================================

@st.cache_data(show_spinner=False)
def extract_pdf_content(
    pdf_bytes: bytes,
    progress_bar: Any = None,
    progress_text: Any = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Extract subdivision records from a vector/selectable-text PDF.

    Progress is displayed page by page when progress_bar and progress_text
    Streamlit objects are supplied.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'pdfplumber'. Add pdfplumber to "
            "requirements.txt."
        ) from exc

    rows: List[Dict[str, Any]] = []
    page_texts: List[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        total_pages = len(pdf.pages)

        for page_number, page in enumerate(pdf.pages, start=1):
            if progress_text is not None:
                progress_text.info(
                    f"Extracting page {page_number} of {total_pages}..."
                )

            page_text = page.extract_text() or ""
            page_texts.append(page_text)

            metadata = extract_page_metadata(page_text)

            tables = page.extract_tables(
                {
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "intersection_tolerance": 5,
                }
            )

            for table in tables:
                rows.extend(
                    extract_events_from_table(
                        table=table,
                        page_number=page_number,
                        metadata=metadata,
                    )
                )

            # Fallback where table extraction does not produce a table.
            if not tables:
                rows.extend(
                    extract_events_from_text(
                        page_text=page_text,
                        page_number=page_number,
                        metadata=metadata,
                    )
                )

            if progress_bar is not None:
                progress_bar.progress(
                    int(page_number / total_pages * 100)
                )

    if progress_text is not None:
        progress_text.success(
            f"PDF extraction complete. Processed {len(page_texts)} page(s)."
        )

    dataframe = pd.DataFrame(rows)

    if dataframe.empty:
        return pd.DataFrame(columns=REGISTER_COLUMNS), page_texts

    dataframe = dataframe.dropna(
        subset=["abscissa_m"],
    ).drop_duplicates(
        subset=[
            "source_page",
            "abscissa_m",
            "event_name",
            "source_evidence",
        ]
    )

    return dataframe.reset_index(drop=True), page_texts


def extract_page_metadata(page_text: str) -> Dict[str, str]:
    """Extract Line, Sector, Section and direction from page heading."""
    text = normalise_spaces(page_text)

    line_match = re.search(
        r"\bLINE\s*:\s*([A-Z0-9 _/-]+)",
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


def clean_table_cell(value: Any) -> str:
    return normalise_spaces(value).replace("\n", " ")


def find_header_index(row: List[Any], keywords: List[str]) -> Optional[int]:
    for index, cell in enumerate(row):
        cell_text = clean_table_cell(cell).upper()

        for keyword in keywords:
            if keyword.upper() in cell_text:
                return index

    return None


def extract_events_from_table(
    table: List[List[Any]],
    page_number: int,
    metadata: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Extract singularity rows from a pdfplumber table.

    The source PDF table can contain merged headers; this function searches
    header text rather than assuming fixed column numbers.
    """
    output: List[Dict[str, Any]] = []

    if not table or len(table) < 2:
        return output

    header_index = None
    kp_index = None
    remarks_index = None
    name_index = None
    reference_index = None
    code_index = None
    inter_index = None

    for row_number, row in enumerate(table[:8]):
        if not row:
            continue

        possible_kp = find_header_index(row, ["K.P", "KP"])
        possible_remarks = find_header_index(row, ["REMARK"])
        possible_name = find_header_index(row, ["NAME"])

        if possible_kp is not None and (
            possible_remarks is not None or possible_name is not None
        ):
            header_index = row_number
            kp_index = possible_kp
            remarks_index = possible_remarks
            name_index = possible_name
            reference_index = find_header_index(row, ["REFERENCE"])
            code_index = find_header_index(row, ["CODE"])
            inter_index = find_header_index(row, ["INTER"])
            break

    if header_index is None or kp_index is None:
        return output

    current_subsection = ""
    current_branch = ""

    for raw_row in table[header_index + 1:]:
        if not raw_row:
            continue

        cells = [clean_table_cell(cell) for cell in raw_row]

        if kp_index >= len(cells):
            continue

        kp = to_float(cells[kp_index])

        all_text = " ".join(cells)
        all_text = normalise_spaces(all_text)

        subsection_match = re.search(
            r"SUB\s*SECTION\s*:\s*"
            r"(\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2})",
            all_text,
            flags=re.IGNORECASE,
        )

        if subsection_match:
            current_subsection = (
                subsection_match.group(1)
                .replace(" ", "")
            )

        if reference_index is not None and reference_index < len(cells):
            current_branch = normalise_branch(cells[reference_index])

        branch_match = re.search(
            r"\b\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,3}\b",
            all_text,
        )

        if branch_match:
            current_branch = normalise_branch(branch_match.group(0))

        if not current_branch and current_subsection:
            current_branch = f"{current_subsection}-0"

        if kp is None:
            continue

        remarks = (
            cells[remarks_index]
            if remarks_index is not None and remarks_index < len(cells)
            else ""
        )

        name = (
            cells[name_index]
            if name_index is not None and name_index < len(cells)
            else ""
        )

        code = (
            cells[code_index]
            if code_index is not None and code_index < len(cells)
            else ""
        )

        inter_singularity = (
            to_float(cells[inter_index])
            if inter_index is not None and inter_index < len(cells)
            else None
        )

        event_text = normalise_spaces(f"{name} {remarks}")

        if not event_text:
            continue

        sector, section, subsection = branch_parts(current_branch)

        if not sector:
            sector = metadata["sector"]

        if not section:
            section = metadata["section"]

        if not subsection:
            subsection = current_subsection

        participate_variant = extract_associated_variant(event_text)
        anticipate_rank = extract_anticipate_rank(event_text)

        output.append(
            {
                "sector": sector,
                "section": section,
                "subsection": subsection,
                "branch": current_branch,
                "direction": metadata["direction"],
                "abscissa_m": kp,
                "inter_singularity_m": inter_singularity,
                "code": code,
                "event_name": name or classify_event_name(event_text),
                "location_name": extract_location_name(event_text),
                "participate_variant": participate_variant,
                "anticipate_variant": "",
                "anticipate_rank": anticipate_rank,
                "anticipate_distance_m": None,
                "anticipate_abscissa_m": None,
                "source_page": page_number,
                "source_evidence": event_text[:1200],
                "remarks": remarks,
            }
        )

    return output


def extract_events_from_text(
    page_text: str,
    page_number: int,
    metadata: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Basic fallback when a PDF table cannot be extracted.

    It detects explicit 'Installation KP' and nearby event wording.
    """
    output: List[Dict[str, Any]] = []
    lines = [
        normalise_spaces(line)
        for line in page_text.splitlines()
        if normalise_spaces(line)
    ]

    current_subsection = ""
    current_branch = ""

    for index, line in enumerate(lines):
        subsection_match = re.search(
            r"SUB\s*SECTION\s*:\s*"
            r"(\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2})",
            line,
            flags=re.IGNORECASE,
        )

        if subsection_match:
            current_subsection = subsection_match.group(1).replace(" ", "")
            current_branch = f"{current_subsection}-0"

        kp_match = re.search(
            r"(?:INSTALLATION\s*)?K\.?P\.?\s*[:=]?\s*"
            r"(-?\d{1,7}(?:[.,]\d{1,3})?)",
            line,
            flags=re.IGNORECASE,
        )

        if not kp_match:
            continue

        kp = to_float(kp_match.group(1))

        context = " ".join(
            lines[max(0, index - 2):min(len(lines), index + 3)]
        )

        sector, section, subsection = branch_parts(current_branch)

        output.append(
            {
                "sector": sector or metadata["sector"],
                "section": section or metadata["section"],
                "subsection": subsection or current_subsection,
                "branch": current_branch,
                "direction": metadata["direction"],
                "abscissa_m": kp,
                "inter_singularity_m": None,
                "code": "",
                "event_name": classify_event_name(context),
                "location_name": extract_location_name(context),
                "participate_variant": extract_associated_variant(context),
                "anticipate_variant": "",
                "anticipate_rank": extract_anticipate_rank(context),
                "anticipate_distance_m": None,
                "anticipate_abscissa_m": None,
                "source_page": page_number,
                "source_evidence": context[:1200],
                "remarks": context,
            }
        )

    return output


# =============================================================================
# SOURCE DATA INTERPRETATION
# =============================================================================

def extract_associated_variant(text: str) -> str:
    match = re.search(
        r"(?:ASSOCIATED|PARTICIPATE|PARTICIPATING)\s+VARIANT"
        r"(?:\s+NUMBER)?\s*[:=]?\s*\(?([^()\n;]+?)\)?"
        r"(?=$|;|,?\s+(?:RANK|NUMBER|ANTICIPATE|SUB\s*SECTION))",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    return normalise_spaces(match.group(1)) if match else ""


def extract_anticipate_rank(text: str) -> str:
    match = re.search(
        r"RANK\s+OF\s+FIRST\s+ANTICIPATED\s+VARIANT\s*:\s*(\d+)",
        safe_text(text),
        flags=re.IGNORECASE,
    )

    return match.group(1) if match else ""


def extract_location_name(text: str) -> str:
    patterns = [
        r"(?:SIGNAL|SIG)\s*(?:NO\.?)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:TRACK\s*CIRCUIT|T/C)\s*(?:NO\.?)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:POINT|TURNOUT)\s*(?:NO\.?)?\s*[:=]?\s*([A-Z0-9/_-]+)",
        r"(?:RETURN\s+CHANNEL)\s*(?:NUMBER)?\s*[:=]?\s*([A-Z0-9/_-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, safe_text(text), flags=re.IGNORECASE)

        if match:
            return normalise_spaces(match.group(0))

    return ""


def classify_event_name(text: str) -> str:
    upper = safe_text(text).upper()

    labels = [
        ("MOVING TRAIN INITIALISATION", "Moving Train Initialisation"),
        ("STATIONARY TRAIN INITIALISATION", "Stationary Train Initialisation"),
        ("SPACING STOPPING POINT", "Spacing Stopping Point"),
        ("SPECIAL STOPPING POINT", "Special Stopping Point"),
        ("RETURN CHANNEL", "Return Channel"),
        ("EMERGENCY ZONE", "Emergency Stop Zone"),
        ("CHANGE OF END", "Change of End"),
        ("STATION CHARACTERISTICS", "Station Stopping Point"),
        ("END OF SHUNT", "End of Shunt"),
        ("END OF SACEM", "End of SACEM"),
        ("DIVERGENCE", "Divergence"),
        ("CONVERGENCE", "Convergence"),
    ]

    for phrase, name in labels:
        if phrase in upper:
            return name

    return ""


def identify_test_codes(text: str) -> List[str]:
    """
    Determine generic procedure tests from source singularity/event wording.

    This does not contain station, sector, KP or branch hard coding.
    """
    matched_codes: List[str] = []
    value = safe_text(text)

    for test_code, rule in TEST_RULES.items():
        for pattern in rule["patterns"]:
            if re.search(pattern, value, flags=re.IGNORECASE):
                matched_codes.append(test_code)
                break

    # Station characteristics generally require both LS08 and LS09
    # if PSD wording is included in the project source data.
    return list(dict.fromkeys(matched_codes))


def calculate_candidate_anticipate_abscissa(
    participate_abscissa: Optional[float],
    anticipation_distance: Optional[float],
    direction: str,
) -> Optional[float]:
    """
    Candidate only.

    Up direction:   KP + anticipation distance
    Down direction: KP - anticipation distance

    It must be reviewed because branch changes and reverse sections can
    invalidate a simple arithmetic result.
    """
    if participate_abscissa is None or anticipation_distance is None:
        return None

    if safe_text(direction).lower().startswith("down"):
        return participate_abscissa - anticipation_distance

    if safe_text(direction).lower().startswith("up"):
        return participate_abscissa + anticipation_distance

    return None


# =============================================================================
# TEST REGISTER
# =============================================================================

def create_test_register(
    extracted_events: pd.DataFrame,
    start_kp: float,
    end_kp: float,
    selected_branch: str,
) -> pd.DataFrame:
    lower_kp = min(start_kp, end_kp)
    upper_kp = max(start_kp, end_kp)

    selected = extracted_events[
        extracted_events["abscissa_m"].between(
            lower_kp,
            upper_kp,
            inclusive="both",
        )
    ].copy()

    if selected_branch and selected_branch != "All branches":
        selected = selected[
            selected["branch"].eq(selected_branch)
        ].copy()

    rows: List[Dict[str, Any]] = []

    for _, event in selected.iterrows():
        searchable_text = " ".join(
            [
                safe_text(event.get("event_name")),
                safe_text(event.get("remarks")),
                safe_text(event.get("source_evidence")),
                safe_text(event.get("location_name")),
            ]
        )

        test_codes = identify_test_codes(searchable_text)

        for test_code in test_codes:
            rule = TEST_RULES[test_code]

            abscissa = (
                float(event["abscissa_m"])
                if rule["needs_abscissa"]
                and pd.notna(event["abscissa_m"])
                else None
            )

            candidate_anticipated = calculate_candidate_anticipate_abscissa(
                participate_abscissa=abscissa,
                anticipation_distance=event.get("anticipate_distance_m"),
                direction=safe_text(event.get("direction")),
            )

            rows.append(
                {
                    "test_code": test_code,
                    "test_id": rule["id"],
                    "test_name": rule["name"],
                    "sector": safe_text(event.get("sector")),
                    "section": safe_text(event.get("section")),
                    "subsection": safe_text(event.get("subsection")),
                    "branch": safe_text(event.get("branch")),
                    "direction": safe_text(event.get("direction")),
                    "abscissa_m": abscissa,
                    "participate_variant": safe_text(
                        event.get("participate_variant")
                    ),
                    "anticipate_variant": safe_text(
                        event.get("anticipate_variant")
                    ),
                    "anticipate_rank": safe_text(
                        event.get("anticipate_rank")
                    ),
                    "anticipate_distance_m": event.get(
                        "anticipate_distance_m"
                    ),
                    "anticipate_abscissa_m": candidate_anticipated,
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
                        "Automatically extracted from subdivision PDF. "
                        "Verify branch, participating variant, anticipated "
                        "variant and all abscissae against approved data."
                    ),
                }
            )

    if not rows:
        return pd.DataFrame(columns=REGISTER_COLUMNS)

    register = pd.DataFrame(rows)

    register = register.drop_duplicates(
        subset=[
            "test_code",
            "branch",
            "abscissa_m",
            "event_name",
            "source_page",
        ]
    )

    return register.sort_values(
        by=["branch", "abscissa_m", "test_code"],
        na_position="last",
    ).reset_index(drop=True)


# =============================================================================
# DOCX GENERATION
# =============================================================================

def set_cell_text(cell: Any, value: Any, bold: bool = False) -> None:
    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(value))
    run.bold = bold


def add_row(table: Any, values: List[Any], bold: bool = False) -> None:
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(cells[index], value, bold=bold)


def format_distance(value: Any) -> str:
    number = to_float(value)

    if number is None:
        return ""

    return f"{number:.3f} m"


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> bytes:
    """
    Create a DOCX test sheet based on Appendix A style.

    It is intentionally marked uncontrolled when printed.
    It must be reviewed before issue.
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency python-docx. Add python-docx to "
            "requirements.txt."
        ) from exc

    test_code = safe_text(row.get("test_code"))

    if test_code not in TEST_RULES:
        raise ValueError(
            f"Unknown test code '{test_code}'."
        )

    rule = TEST_RULES[test_code]

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

    heading = header.add_run(project_info["procedure_title"])
    heading.bold = True
    heading.font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{rule['name']} ({rule['id']})"
    )
    title_run.bold = True
    title_run.font.size = Pt(12)

    info_table = document.add_table(rows=0, cols=6)
    info_table.style = "Table Grid"

    add_row(
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

    add_row(
        info_table,
        [
            "Trackside version:",
            project_info["trackside_version"],
            "ATP version:",
            project_info["atp_version"],
            "ATO version:",
            project_info["ato_version"],
        ],
    )

    document.add_paragraph()

    subtitle = document.add_paragraph()
    subtitle.add_run(rule["short_name"]).bold = True

    location_table = document.add_table(rows=0, cols=4)
    location_table.style = "Table Grid"

    add_row(
        location_table,
        [
            "Sector:",
            safe_text(row.get("sector")),
            "Branch Number:",
            safe_text(row.get("branch")),
        ],
    )

    if rule["needs_abscissa"]:
        add_row(
            location_table,
            [
                "Abscissa:",
                format_distance(row.get("abscissa_m")),
                "Direction:",
                safe_text(row.get("direction")),
            ],
        )
    else:
        add_row(
            location_table,
            [
                "Abscissa:",
                "Not required for this test",
                "Direction:",
                safe_text(row.get("direction")),
            ],
        )

    add_row(
        location_table,
        [
            "Participate Variant:",
            safe_text(row.get("participate_variant")),
            "Anticipate Variant:",
            safe_text(row.get("anticipate_variant")),
        ],
    )

    add_row(
        location_table,
        [
            "Anticipate Rank:",
            safe_text(row.get("anticipate_rank")),
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

    for action, expected, unit in rule["checks"]:
        add_row(
            test_table,
            [
                action,
                expected,
                f"{unit}: ",
                "☐ Y   ☐ N   ☐ N/A",
            ],
        )

    document.add_paragraph()

    remarks_title = document.add_paragraph()
    remarks_title.add_run(
        "Comments / Engineering Remarks"
    ).bold = True

    notes_table = document.add_table(rows=1, cols=1)
    notes_table.style = "Table Grid"

    notes = (
        f"Source Page: {safe_text(row.get('source_page'))}\n"
        f"Source Event: {safe_text(row.get('event_name'))}\n"
        f"Location / Asset: {safe_text(row.get('location_name'))}\n"
        f"Source Evidence: {safe_text(row.get('source_evidence'))}\n\n"
        f"Engineering Remarks: {safe_text(row.get('remarks'))}"
    )

    set_cell_text(notes_table.rows[0].cells[0], notes)

    document.add_paragraph()

    approval_table = document.add_table(rows=0, cols=3)
    approval_table.style = "Table Grid"

    add_row(
        approval_table,
        [
            "The test covered by this Test Sheet being completed, "
            "the test is considered as:",
            "☐ Passed",
            "☐ Failed",
        ],
    )

    add_row(approval_table, ["", "Alstom", "MTR"])
    add_row(approval_table, ["Name:", "", ""])
    add_row(approval_table, ["Signature:", "", ""])
    add_row(approval_table, ["Date:", "", ""])

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – Verify approved design and "
        "project-specific data before use."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(7)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_docx_zip(
    register: pd.DataFrame,
    project_info: Dict[str, str],
    progress_bar: Any = None,
    progress_text: Any = None,
) -> bytes:
    """
    Create ZIP archive containing generated DOCX test sheets.

    Progress is displayed sheet by sheet when progress objects are supplied.
    """
    output = io.BytesIO()

    total_sheets = len(register)

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

            if progress_text is not None:
                progress_text.info(
                    f"Generating DOCX test sheet {sequence} of "
                    f"{total_sheets}: {test_code} / {branch}"
                )

            sheet_number = (
                f"{project_info['sheet_prefix']}-{sequence:03d}"
            )

            filename = clean_filename(
                f"{sheet_number}_"
                f"{test_code}_"
                f"{branch}_"
                f"KP_{safe_text(row.get('abscissa_m'))}.docx"
            )

            docx_bytes = create_docx_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            archive.writestr(filename, docx_bytes)

            if progress_bar is not None:
                progress_bar.progress(
                    int(sequence / total_sheets * 100)
                )

    if progress_text is not None:
        progress_text.success(
            f"DOCX generation complete. Created {total_sheets} test sheet(s)."
        )

    return output.getvalue()


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    extracted_events: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
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
                ["Generated Test Sheets", len(register)],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(writer, sheet_name="Cover", index=False)
        register.to_excel(writer, sheet_name="Test Register", index=False)
        extracted_events.to_excel(
            writer,
            sheet_name="Extracted Source Events",
            index=False,
        )

        workbook = writer.book

        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                width = max(
                    len(safe_text(cell.value))
                    for cell in column_cells
                )

                worksheet.column_dimensions[
                    column_cells[0].column_letter
                ].width = min(max(width + 2, 12), 65)

    return output.getvalue()


# =============================================================================
# UI
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
        .generator-warning {
            background: #FFF7E8;
            border-left: 5px solid #E37400;
            padding: 14px 16px;
            border-radius: 8px;
            margin: 12px 0 18px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_test_sheet_generator_app() -> None:
    """
    Imported and called by app.py.

    Do not call st.set_page_config() in this file because app.py already
    controls the Streamlit page configuration.
    """
    render_styles()

    st.markdown(
        """
        <div class="generator-hero">
            <h1>ATC Low-Speed Dynamic Test Sheet Generator</h1>
            <p>
                Upload a SACEM Subdivision PDF, select a KP range, review
                extracted test points and generate DOCX test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="generator-warning">
            <b>Engineering limitation:</b><br>
            This tool creates a draft register from the approved PDF.
            Branches, variants, anticipation distances and calculated
            anticipated abscissae must be independently checked against
            approved subdivision, variant-map, invariant and PAT documents
            before generating controlled test records.
        </div>
        """,
        unsafe_allow_html=True,
    )

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

    st.markdown("## 1. Upload SACEM Subdivision PDF")

    uploaded_file = st.file_uploader(
        "Upload one approved SACEM Subdivision / Variant Map PDF",
        type=["pdf"],
        key="generator_pdf_input",
    )

    if uploaded_file is None:
        st.info(
            "Upload a selectable-text PDF containing subdivision "
            "singularity tables. The PDF format should be similar to "
            "the CUC-003215 SACEM Subdivision document."
        )
        return

    pdf_bytes = uploaded_file.getvalue()

    if st.button(
        "🔎 Extract Subdivision Events",
        type="primary",
        use_container_width=True,
        key="generator_extract_pdf",
    ):
        progress_text = st.empty()
        progress_bar = st.progress(0)

        try:
            extracted_events, page_texts = extract_pdf_content(
                pdf_bytes=pdf_bytes,
                progress_bar=progress_bar,
                progress_text=progress_text,
            )

            st.session_state["generator_events"] = extracted_events
            st.session_state["generator_page_count"] = len(page_texts)
            st.session_state["generator_file_name"] = uploaded_file.name

            progress_bar.progress(100)

            progress_text.success(
                f"Extraction completed: {len(extracted_events)} "
                f"potential event row(s) found from {len(page_texts)} page(s)."
            )

        except Exception as exc:
            progress_bar.empty()
            progress_text.empty()

            st.error(f"PDF extraction failed: {exc}")
            return

    extracted_events = st.session_state.get(
        "generator_events",
        pd.DataFrame(),
    )

    if extracted_events.empty:
        st.warning(
            "No events have been extracted yet. Click "
            "'Extract Subdivision Events'."
        )
        return

    st.success(
        f"Extracted {len(extracted_events)} potential subdivision "
        f"event row(s) from "
        f"{st.session_state.get('generator_page_count', 0)} page(s)."
    )

    with st.expander("Review Extracted Source Events"):
        st.dataframe(
            extracted_events,
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("## 2. Select Start and End KP")

    available_branches = sorted(
        branch
        for branch in extracted_events["branch"].dropna().unique()
        if safe_text(branch)
    )

    branch_options = ["All branches"] + available_branches

    selected_branch = st.selectbox(
        "Branch Filter",
        options=branch_options,
        key="generator_branch_filter",
    )

    range_events = extracted_events.copy()

    if selected_branch != "All branches":
        range_events = range_events[
            range_events["branch"].eq(selected_branch)
        ]

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
        key="generator_create_register",
    ):
        register = create_test_register(
            extracted_events=extracted_events,
            start_kp=float(start_kp),
            end_kp=float(end_kp),
            selected_branch=selected_branch,
        )

        st.session_state["generator_register"] = register

    register = st.session_state.get(
        "generator_register",
        pd.DataFrame(columns=REGISTER_COLUMNS),
    )

    if register.empty:
        st.warning(
            "No procedure test rules matched extracted events in the "
            "selected range. Review the extracted source events and "
            "confirm the PDF contains selectable text."
        )
        return

    st.markdown("## 3. Review Generated Test Register")

    st.caption(
        "Review every row. In particular, verify participating variant, "
        "anticipated variant, branch, KP and candidate anticipated "
        "abscissa before issuing test sheets."
    )

    reviewed_register = st.data_editor(
        register,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="generator_register_editor",
        column_config={
            "abscissa_m": st.column_config.NumberColumn(
                "Abscissa (m)",
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
                "Remarks",
                width="large",
            ),
        },
    )

    st.session_state["generator_register"] = reviewed_register

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric("Draft Test Sheets", len(reviewed_register))

    metric2.metric(
        "Rows With Abscissa",
        int(reviewed_register["abscissa_m"].notna().sum()),
    )

    metric3.metric(
        "Rows With Anticipated Variant",
        int(
            reviewed_register["anticipate_variant"]
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ),
    )

    st.markdown("## 4. Generate Downloads")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        extracted_events=extracted_events,
        project_info=project_info,
    )

    approved_register = reviewed_register[
        reviewed_register["review_status"].eq(
            "Approved for generation"
        )
    ].copy()

    col1, col2, col3 = st.columns(3)

    with col1:
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

    with col2:
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

    with col3:
        if approved_register.empty:
            st.button(
                "⬇️ Download Approved DOCX Test Sheets",
                disabled=True,
                use_container_width=True,
            )

            st.caption(
                "Set Review Status to 'Approved for generation' for "
                "one or more rows to enable DOCX generation."
            )

        else:
            if st.button(
                "📦 Generate Approved DOCX Test Sheets",
                type="primary",
                use_container_width=True,
                key="generator_create_docx_zip",
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

                    docx_progress_bar.progress(100)

                except Exception as exc:
                    docx_progress_bar.empty()
                    docx_progress_text.empty()

                    st.error(f"DOCX generation failed: {exc}")

            docx_zip = st.session_state.get("generator_docx_zip")

            if docx_zip:
                st.download_button(
                    "⬇️ Download Approved DOCX Test Sheets",
                    data=docx_zip,
                    file_name=clean_filename(
                        f"{project_info['sheet_prefix']}_"
                        f"ATC_Low_Speed_Test_Sheets.zip"
                    ),
                    mime="application/zip",
                    use_container_width=True,
                    key="generator_download_docx_zip",
                )
