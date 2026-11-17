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

EXTRACTED_EVENT_COLUMNS = [
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
    "anticipate_variant",
    "anticipate_rank",
    "anticipate_distance_m",
    "anticipate_abscissa_m",
    "source_page",
    "source_evidence",
    "remarks",
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
# GENERIC PROCEDURE RULES
#
# These are procedure-level test definitions.
# There is no station, sector, branch, KP or variant hard-coding here.
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
            ("Beacon Codes", "Record and compare with design", "Record"),
            ("Calibration", "Record calibration value", "mm"),
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
            ("Beacon Codes", "Record and compare with design", "Record"),
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
        "patterns": [
            r"SPACING\s+STOPPING\s+POINT",
            r"TRACK\s+CIRCUIT.*STOPPING",
        ],
        "checks": [
            ("Track circuit occupied", "Associated variant restrictive", "Y / N"),
            ("Available mode", "CM/AUTO", "Y / N"),
            ("Loc Max", "Record", "m"),
            ("Target Distance", "Record", "m"),
            ("Singularity Abscissa", "Record", "m"),
            ("OMAP Target Speed", "Equal 0 where applicable", "Y / N"),
            ("Track circuit free", "Associated variant permissive", "Y / N"),
            ("Target comparison", "Free target >= occupied target", "Y / N"),
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
            ("Loc Max / Target Distance / Abscissa", "Record", "m"),
            ("Permissive signal", "Associated variant permissive", "Y / N"),
            ("Target comparison", "Permissive target >= restrictive", "Y / N"),
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
            ("Signal red / overlap occupied", "Variants restrictive", "Y / N"),
            ("Signal red / overlap free", "Record stopping behaviour", "Record"),
            ("Signal blue / overlap free", "Variants permissive", "Y / N"),
            ("Target comparison", "Permissive target >= restrictive", "Y / N"),
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
            ("Loc Max / Target Distance / Abscissa", "Record", "m"),
            ("Point controlled", "Associated variant permissive", "Y / N"),
            ("Target comparison", "Controlled >= uncontrolled", "Y / N"),
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
        "patterns": [
            r"\bPSD\b",
            r"ALL\s+DOOR\s+CLOSED",
        ],
        "checks": [
            ("Correct platform side", "Door side enabled", "Y / N"),
            ("Open wrong-side doors", "PSD / relay not activated", "Y / N"),
            ("Open correct-side doors", "PSD / relay activated", "Y / N"),
            ("All doors closed lost", "Variants restrictive; RM only", "Y / N"),
            ("PIOS / override", "CM/AM then timeout behaviour checked", "Y / N"),
            ("Close correct-side doors", "PSD closed and variants permissive", "Y / N"),
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
            ("Loc Max / Target Distance / Abscissa", "Record", "m"),
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
            ("Cab 2 before CoE", "Shutdown; distance to signal < 12 m", "Y / N"),
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
            ("TSR Value", "Record TSR value", "kph"),
            ("Train Behaviour", "Target speed follows TSR; no EB", "Y / N"),
        ],
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "short_name": "Return Channel Test",
        "needs_abscissa": False,
        "patterns": [
            r"RETURN\s+CHANNEL",
            r"R/C\s+LOOP",
        ],
        "checks": [
            ("Return channel location", "Record location", "Record"),
            ("Return channel loop", "Record equipment reference", "Record"),
            ("Trackside reception", "Train information received", "Y / N"),
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
    return value[:150] or "ATC_Test_Sheet"


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


def normalise_branch(value: Any) -> str:
    match = re.search(
        r"(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,3})",
        safe_text(value),
    )

    if not match:
        return safe_text(value)

    sector, section, subsection, branch = match.groups()

    return (
        f"{int(sector):02d}-"
        f"{int(section):02d}-"
        f"{int(subsection)}-"
        f"{int(branch)}"
    )


def branch_parts(branch: str) -> Tuple[str, str, str]:
    parts = safe_text(branch).split("-")

    return (
        parts[0] if len(parts) > 0 else "",
        parts[1] if len(parts) > 1 else "",
        "-".join(parts[:3]) if len(parts) > 2 else "",
    )


def format_distance(value: Any) -> str:
    numeric = to_float(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


# =============================================================================
# PDF INTERPRETATION
# =============================================================================

def extract_page_metadata(page_text: str) -> Dict[str, str]:
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

    options = [
        ("MOVING TRAIN INITIALISATION", "Moving Train Initialisation"),
        ("MOVING TRAIN INITIALIZATION", "Moving Train Initialisation"),
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

    for phrase, name in options:
        if phrase in upper:
            return name

    return ""


def looks_like_subdivision_page(page_text: str) -> bool:
    text = page_text.upper()

    return any(
        phrase in text
        for phrase in [
            "SINGULARITIES",
            "TEST ABS",
            "INTER SINGUL",
            "SUB SECTION",
            "SUB-SECTION",
            "UP TRACK NORMAL DIRECTION",
            "DN TRACK NORMAL DIRECTION",
        ]
    )


def extract_events_from_text(
    page_text: str,
    page_number: int,
    metadata: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Safe line-based extraction fallback.

    It intentionally does not assume PDF table cells are perfect.
    It searches for K.P. values and associates surrounding text.
    """
    output: List[Dict[str, Any]] = []

    lines = [
        normalise_spaces(line)
        for line in page_text.splitlines()
        if normalise_spaces(line)
    ]

    current_subsection = ""
    current_branch = ""

    kp_pattern = re.compile(
        r"(?:\bK\.?\s*P\.?\b|INSTALLATION\s+KP)"
        r"\s*[:=]?\s*(-?\d{1,7}(?:[.,]\d{1,3})?)",
        re.IGNORECASE,
    )

    loose_kp_pattern = re.compile(
        r"\b(-?\d{1,5}[.,]\d{1,3})\b"
    )

    for line_index, line in enumerate(lines):
        subsection_match = re.search(
            r"SUB\s*-?\s*SECTION\s*:\s*"
            r"(\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2})",
            line,
            flags=re.IGNORECASE,
        )

        if subsection_match:
            current_subsection = subsection_match.group(1).replace(" ", "")
            current_branch = f"{current_subsection}-0"

        branch_match = re.search(
            r"\b\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,3}\b",
            line,
        )

        if branch_match:
            current_branch = normalise_branch(branch_match.group(0))

        kp_match = kp_pattern.search(line)

        # Some PDF extraction formats contain KP values without "KP".
        if not kp_match and (
            "BEACON" in line.upper()
            or "STOPPING" in line.upper()
            or "VARIANT" in line.upper()
            or "RETURN CHANNEL" in line.upper()
            or "STATION" in line.upper()
            or "PSR" in line.upper()
        ):
            kp_match = loose_kp_pattern.search(line)

        if not kp_match:
            continue

        kp = to_float(kp_match.group(1))

        if kp is None:
            continue

        context = " ".join(
            lines[max(0, line_index - 2):min(len(lines), line_index + 3)]
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
                "remarks": context[:1200],
            }
        )

    return output


def extract_events_from_table(
    table: List[List[Any]],
    page_number: int,
    metadata: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Extract records from table output where pdfplumber finds grid cells.

    The function is deliberately defensive. If a page has unexpected
    merged cells it returns no rows rather than failing the app.
    """
    output: List[Dict[str, Any]] = []

    if not table:
        return output

    current_subsection = ""
    current_branch = ""

    for raw_row in table:
        if not raw_row:
            continue

        cells = [
            normalise_spaces(cell)
            for cell in raw_row
        ]

        combined = normalise_spaces(" ".join(cells))

        if not combined:
            continue

        subsection_match = re.search(
            r"SUB\s*-?\s*SECTION\s*:\s*"
            r"(\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2})",
            combined,
            flags=re.IGNORECASE,
        )

        if subsection_match:
            current_subsection = subsection_match.group(1).replace(" ", "")
            current_branch = f"{current_subsection}-0"

        branch_match = re.search(
            r"\b\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,2}\s*-\s*\d{1,3}\b",
            combined,
        )

        if branch_match:
            current_branch = normalise_branch(branch_match.group(0))

        # In the standard drawing KP is usually one of the early numeric cells.
        possible_numbers = []

        for cell in cells[:8]:
            value = to_float(cell)

            if value is not None:
                possible_numbers.append(value)

        if not possible_numbers:
            continue

        # Typical KP is generally the largest number in left-side cells.
        kp = max(possible_numbers)

        # Ignore row numbers, element counts and clearly implausible values.
        if kp < -1000 or kp > 100000:
            continue

        event_name = classify_event_name(combined)

        if not event_name and not any(
            keyword in combined.upper()
            for keyword in [
                "BEACON",
                "VARIANT",
                "PSR",
                "FIXED CHARACTERISTICS",
                "COMPENSATED",
            ]
        ):
            continue

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
                "event_name": event_name,
                "location_name": extract_location_name(combined),
                "participate_variant": extract_associated_variant(combined),
                "anticipate_variant": "",
                "anticipate_rank": extract_anticipate_rank(combined),
                "anticipate_distance_m": None,
                "anticipate_abscissa_m": None,
                "source_page": page_number,
                "source_evidence": combined[:1200],
                "remarks": combined[:1200],
            }
        )

    return output


def extract_pdf_content(
    pdf_bytes: bytes,
    progress_bar: Any,
    progress_message: Any,
) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """
    Extract pages safely.

    A single damaged, rotated or complex page will not crash the application.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: pdfplumber. "
            "Add pdfplumber>=0.11.0 to requirements.txt."
        ) from exc

    rows: List[Dict[str, Any]] = []
    page_texts: List[str] = []
    warnings: List[str] = []

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise RuntimeError(f"Cannot open PDF: {exc}") from exc

    with pdf:
        total_pages = len(pdf.pages)

        if total_pages == 0:
            raise RuntimeError("Uploaded PDF has no pages.")

        for page_number, page in enumerate(pdf.pages, start=1):
            progress_message.info(
                f"Reading page {page_number} of {total_pages}..."
            )

            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                page_text = ""
                warnings.append(
                    f"Page {page_number}: text extraction failed: {exc}"
                )

            page_texts.append(page_text)

            progress_bar.progress(
                int(page_number / total_pages * 100)
            )

            if not looks_like_subdivision_page(page_text):
                continue

            metadata = extract_page_metadata(page_text)
            page_rows: List[Dict[str, Any]] = []

            try:
                tables = page.extract_tables(
                    table_settings={
                        "vertical_strategy": "lines",
                        "horizontal_strategy": "lines",
                        "intersection_tolerance": 5,
                        "snap_tolerance": 4,
                        "join_tolerance": 4,
                    }
                ) or []

                for table in tables:
                    try:
                        page_rows.extend(
                            extract_events_from_table(
                                table=table,
                                page_number=page_number,
                                metadata=metadata,
                            )
                        )
                    except Exception as exc:
                        warnings.append(
                            f"Page {page_number}: table row extraction "
                            f"skipped: {exc}"
                        )

            except Exception as exc:
                warnings.append(
                    f"Page {page_number}: table extraction skipped: {exc}"
                )

            # Text fallback is always attempted if no useful table rows.
            if not page_rows:
                try:
                    page_rows = extract_events_from_text(
                        page_text=page_text,
                        page_number=page_number,
                        metadata=metadata,
                    )
                except Exception as exc:
                    warnings.append(
                        f"Page {page_number}: text fallback skipped: {exc}"
                    )

            rows.extend(page_rows)

    progress_bar.progress(100)

    dataframe = pd.DataFrame(rows)

    if dataframe.empty:
        return (
            pd.DataFrame(columns=EXTRACTED_EVENT_COLUMNS),
            page_texts,
            warnings,
        )

    for column in EXTRACTED_EVENT_COLUMNS:
        if column not in dataframe.columns:
            dataframe[column] = ""

    dataframe["abscissa_m"] = pd.to_numeric(
        dataframe["abscissa_m"],
        errors="coerce",
    )

    dataframe = dataframe.dropna(subset=["abscissa_m"]).copy()

    dataframe = dataframe.drop_duplicates(
        subset=[
            "source_page",
            "abscissa_m",
            "event_name",
            "source_evidence",
        ]
    )

    dataframe = dataframe[
        EXTRACTED_EVENT_COLUMNS
    ].reset_index(drop=True)

    return dataframe, page_texts, warnings


# =============================================================================
# TEST REGISTER
# =============================================================================

def identify_test_codes(text: str) -> List[str]:
    matched: List[str] = []

    for test_code, rule in TEST_RULES.items():
        for pattern in rule["patterns"]:
            if re.search(pattern, safe_text(text), re.IGNORECASE):
                matched.append(test_code)
                break

    return list(dict.fromkeys(matched))


def calculate_candidate_anticipate_abscissa(
    abscissa_m: Optional[float],
    anticipation_distance_m: Optional[float],
    direction: str,
) -> Optional[float]:
    if abscissa_m is None or anticipation_distance_m is None:
        return None

    if safe_text(direction).lower().startswith("up"):
        return abscissa_m + anticipation_distance_m

    if safe_text(direction).lower().startswith("down"):
        return abscissa_m - anticipation_distance_m

    return None


def create_test_register(
    extracted_events: pd.DataFrame,
    start_kp: float,
    end_kp: float,
    selected_branch: str,
) -> pd.DataFrame:
    lower = min(start_kp, end_kp)
    upper = max(start_kp, end_kp)

    selected = extracted_events[
        extracted_events["abscissa_m"].between(lower, upper)
    ].copy()

    if selected_branch != "All branches":
        selected = selected[
            selected["branch"].eq(selected_branch)
        ].copy()

    rows: List[Dict[str, Any]] = []

    for _, event in selected.iterrows():
        source_text = " ".join(
            [
                safe_text(event.get("event_name")),
                safe_text(event.get("remarks")),
                safe_text(event.get("source_evidence")),
                safe_text(event.get("location_name")),
            ]
        )

        test_codes = identify_test_codes(source_text)

        for test_code in test_codes:
            rule = TEST_RULES[test_code]

            abscissa = (
                float(event["abscissa_m"])
                if rule["needs_abscissa"]
                and pd.notna(event["abscissa_m"])
                else None
            )

            anticipated = calculate_candidate_anticipate_abscissa(
                abscissa_m=abscissa,
                anticipation_distance_m=to_float(
                    event.get("anticipate_distance_m")
                ),
                direction=safe_text(event.get("direction")),
            )

            rows.append(
                {
                    "test_code": test_code,
                    "test_id": rule["id"],
                    "test_name": rule["name"],
                    "needs_abscissa": rule["needs_abscissa"],
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
                    "anticipate_abscissa_m": anticipated,
                    "event_name": safe_text(event.get("event_name")),
                    "location_name": safe_text(event.get("location_name")),
                    "source_page": safe_text(event.get("source_page")),
                    "source_evidence": safe_text(
                        event.get("source_evidence")
                    ),
                    "review_status": "To be reviewed",
                    "remarks": (
                        "Automatically extracted. Verify source event, "
                        "branch, variant, anticipation and abscissa."
                    ),
                }
            )

    if not rows:
        return pd.DataFrame(columns=REGISTER_COLUMNS)

    register = pd.DataFrame(rows)

    return register.drop_duplicates(
        subset=[
            "test_code",
            "branch",
            "abscissa_m",
            "source_page",
            "event_name",
        ]
    ).sort_values(
        by=["branch", "abscissa_m", "test_code"],
        na_position="last",
    ).reset_index(drop=True)


# =============================================================================
# DOCX GENERATION
# =============================================================================

def set_cell_text(cell: Any, text: Any, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(text))
    run.bold = bold


def add_docx_row(
    table: Any,
    values: List[Any],
    bold: bool = False,
) -> None:
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(cells[index], value, bold)


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
            "Missing dependency python-docx."
        ) from exc

    test_code = safe_text(row.get("test_code"))

    if test_code not in TEST_RULES:
        raise ValueError(f"Unknown test code: {test_code}")

    rule = TEST_RULES[test_code]

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.40)
    section.right_margin = Inches(0.40)

    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(8)

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    logo = heading.add_run("ALSTOM\n")
    logo.bold = True
    logo.font.size = Pt(16)

    procedure = heading.add_run(project_info["procedure_title"])
    procedure.bold = True
    procedure.font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    test_title = title.add_run(
        f"{rule['name']} ({rule['id']})"
    )
    test_title.bold = True
    test_title.font.size = Pt(12)

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

    add_docx_row(
        location_table,
        [
            "Abscissa:",
            (
                format_distance(row.get("abscissa_m"))
                if rule["needs_abscissa"]
                else "Not required for this test"
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
            "Candidate Anticipate Abs.:",
            format_distance(row.get("anticipate_abscissa_m"))
            or "To be reviewed",
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

    for index, text in enumerate(headers):
        set_cell_text(test_table.rows[0].cells[index], text, True)

    for action, expected, unit in rule["checks"]:
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

    comments = document.add_paragraph()
    comments.add_run("Comments / Engineering Remarks").bold = True

    evidence_table = document.add_table(rows=1, cols=1)
    evidence_table.style = "Table Grid"

    evidence_text = (
        f"Source Page: {safe_text(row.get('source_page'))}\n"
        f"Source Event: {safe_text(row.get('event_name'))}\n"
        f"Evidence: {safe_text(row.get('source_evidence'))}\n\n"
        f"Remarks: {safe_text(row.get('remarks'))}"
    )

    set_cell_text(evidence_table.rows[0].cells[0], evidence_text)

    document.add_paragraph()

    approval = document.add_table(rows=0, cols=3)
    approval.style = "Table Grid"

    add_docx_row(
        approval,
        [
            "The test covered by this Test Sheet is considered:",
            "☐ Passed",
            "☐ Failed",
        ],
    )

    add_docx_row(approval, ["", "Alstom", "MTR"])
    add_docx_row(approval, ["Name:", "", ""])
    add_docx_row(approval, ["Signature:", "", ""])
    add_docx_row(approval, ["Date:", "", ""])

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – Verify approved project data before use."
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
    progress_message: Any,
) -> bytes:
    output = io.BytesIO()
    total = len(register)

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for sequence, (_, row) in enumerate(register.iterrows(), start=1):
            sheet_number = (
                f"{project_info['sheet_prefix']}-{sequence:03d}"
            )

            progress_message.info(
                f"Generating DOCX {sequence} of {total}: "
                f"{safe_text(row.get('test_code'))}"
            )

            filename = clean_filename(
                f"{sheet_number}_"
                f"{safe_text(row.get('test_code'))}_"
                f"{safe_text(row.get('branch'))}_"
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

            progress_bar.progress(int(sequence / total * 100))

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
    project_info: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
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
        events.to_excel(writer, sheet_name="Extracted Events", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for cells in worksheet.columns:
                length = max(
                    len(safe_text(cell.value))
                    for cell in cells
                )

                worksheet.column_dimensions[
                    cells[0].column_letter
                ].width = min(max(length + 2, 12), 65)

    return output.getvalue()


# =============================================================================
# USER INTERFACE
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
    This function is imported by app.py.
    Do not add st.set_page_config() in this module.
    """
    render_styles()

    st.markdown(
        """
        <div class="generator-hero">
            <h1>ATC Low-Speed Dynamic Test Sheet Generator</h1>
            <p>
                Upload SACEM Subdivision PDF, select a KP range, review
                extracted data and generate DOCX test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="generator-warning">
            <b>Engineering warning:</b><br>
            PDF extraction provides draft data only. Confirm branches,
            variants, anticipated information, abscissae and test
            applicability against approved project documents before issue.
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
        "Upload approved SACEM Subdivision / Variant Map PDF",
        type=["pdf"],
        key="generator_pdf_file",
    )

    if uploaded_file is None:
        st.info("Upload one PDF to begin.")
        return

    if st.button(
        "🔎 Extract PDF Events",
        type="primary",
        use_container_width=True,
        key="generator_extract_button",
    ):
        progress_message = st.empty()
        progress_bar = st.progress(0)

        try:
            events, page_texts, warnings = extract_pdf_content(
                pdf_bytes=uploaded_file.getvalue(),
                progress_bar=progress_bar,
                progress_message=progress_message,
            )

            st.session_state["generator_events"] = events
            st.session_state["generator_page_count"] = len(page_texts)
            st.session_state["generator_warnings"] = warnings
            st.session_state.pop("generator_register", None)
            st.session_state.pop("generator_docx_zip", None)

            if events.empty:
                progress_message.warning(
                    "Extraction completed but no usable event rows were found."
                )
            else:
                progress_message.success(
                    f"Extraction completed: {len(events)} event row(s) found."
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
            "Click 'Extract PDF Events'. If extraction fails, the full "
            "technical detail will be displayed above."
        )
        return

    warnings = st.session_state.get("generator_warnings", [])

    if warnings:
        with st.expander(
            f"Extraction Warnings ({len(warnings)})",
            expanded=False,
        ):
            for warning in warnings:
                st.write(f"• {warning}")

    with st.expander("Extracted Source Events", expanded=False):
        st.dataframe(events, use_container_width=True, hide_index=True)

    st.markdown("## 2. Select Test Range")

    branches = sorted(
        branch
        for branch in events["branch"].dropna().unique()
        if safe_text(branch)
    )

    selected_branch = st.selectbox(
        "Branch Filter",
        options=["All branches"] + branches,
        key="generator_branch",
    )

    range_events = events.copy()

    if selected_branch != "All branches":
        range_events = range_events[
            range_events["branch"].eq(selected_branch)
        ].copy()

    range_events = range_events.dropna(subset=["abscissa_m"]).copy()

    if range_events.empty:
        st.error(
            "No valid abscissa values found for this branch selection."
        )
        return

    min_kp = float(range_events["abscissa_m"].min())
    max_kp = float(range_events["abscissa_m"].max())

    range_col1, range_col2 = st.columns(2)

    with range_col1:
        start_kp = st.number_input(
            "Start KP / Abscissa",
            min_value=min_kp,
            max_value=max_kp,
            value=min_kp,
            step=0.1,
            format="%.3f",
            key="generator_start_kp",
        )

    with range_col2:
        end_kp = st.number_input(
            "End KP / Abscissa",
            min_value=min_kp,
            max_value=max_kp,
            value=max_kp,
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
        st.session_state["generator_register"] = create_test_register(
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
            "No test rules matched extracted source events in the selected range."
        )
        return

    st.markdown("## 3. Review Test Register")

    reviewed_register = st.data_editor(
        register,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="generator_register_editor",
        column_config={
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
        "Approved Rows",
        int(
            reviewed_register["review_status"].eq(
                "Approved for generation"
            ).sum()
        ),
    )

    st.markdown("## 4. Downloads")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        events=events,
        project_info=project_info,
    )

    approved_register = reviewed_register[
        reviewed_register["review_status"].eq(
            "Approved for generation"
        )
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
            data=reviewed_register.to_csv(index=False).encode("utf-8"),
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
                "Set one or more rows to 'Approved for generation'."
            )
        else:
            if st.button(
                "📦 Generate DOCX Test Sheets",
                type="primary",
                use_container_width=True,
                key="generator_create_docx_zip",
            ):
                docx_message = st.empty()
                docx_progress = st.progress(0)

                try:
                    st.session_state["generator_docx_zip"] = create_docx_zip(
                        register=approved_register,
                        project_info=project_info,
                        progress_bar=docx_progress,
                        progress_message=docx_message,
                    )
                except Exception as exc:
                    docx_progress.empty()
                    docx_message.error(
                        f"DOCX generation failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    st.exception(exc)

            docx_zip = st.session_state.get("generator_docx_zip")

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
                    key="generator_download_docx_zip",
                )
