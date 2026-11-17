from __future__ import annotations

import io
import re
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


# =============================================================================
# CONFIGURATION
# =============================================================================

PROCEDURE_REFERENCE = "C1252-TAC-0274"
PROCEDURE_TITLE = "Trackside ATC Low-Speed Dynamic Test Procedure"

ALSTOM_NAVY = "#102A43"
ALSTOM_BLUE = "#0064A6"
ALSTOM_RED = "#E52B1E"
ALSTOM_LIGHT_BLUE = "#EAF4FB"
ALSTOM_GREY = "#F4F7FA"


TEST_TYPES: Dict[str, Dict[str, Any]] = {
    "LS01": {
        "id": "ATC-TNC-TRA-LS01",
        "title": "MTIB Initialization Test",
        "short_title": "MTIB Initialization",
        "icon": "📡",
        "colour": "#0064A6",
        "purpose": "Verify that ATC on-board can be localized and calibrated on an MTIB beacon.",
        "checks": [
            ("Step 1", "Selected Mode", "RM"),
            ("Step 1", "Localisation Status", "Out Loc"),
            ("Step 2", "Selected Mode", "RM"),
            ("Step 2", "Localisation Status", "Wait Loc"),
            ("Step 2", "Beacon Code Byte 1", "Record actual value"),
            ("Step 2", "Beacon Code Byte 2", "Record actual value"),
            ("Step 2", "Beacon Code Byte 3", "Record actual value"),
            ("Step 2", "Calibration / A-car Number", "Record actual value"),
            ("Step 2", "Calibration Maximum", "Record actual value (mm)"),
            ("Step 3", "Selected Mode", "RM"),
            ("Step 3", "Localisation Status", "Total Loc"),
        ],
    },
    "LS02": {
        "id": "ATC-TNC-TRA-LS02",
        "title": "STIB Initialization Test",
        "short_title": "STIB Initialization",
        "icon": "📍",
        "colour": "#0064A6",
        "purpose": "Verify that ATC provides accurate localization upon STIB.",
        "checks": [
            ("Step 1", "Selected Mode", "RM"),
            ("Step 1", "Localisation Status", "Out Loc"),
            ("Step 2", "Selected Mode", "RM"),
            ("Step 2", "Localisation Status", "Wait Inv"),
            ("Step 2", "Beacon codes", "Record and compare with expected codes"),
            ("Step 2", "Calibration Maximum", "Record actual value (mm)"),
            ("Step 3", "Selected Mode", "RM"),
            ("Step 3", "Localisation Status", "Total Loc / Wait Beacon"),
            ("Step 3", "Mode Available", "CM/AM"),
            ("Step 4", "Selected Mode", "CM"),
            ("Step 4", "Emergency Brake", "No EB after first RB / MTIB"),
        ],
    },
    "LS03": {
        "id": "ATC-TNC-TRA-LS03",
        "title": "Spacing Stopping Point Test – Track Circuit",
        "short_title": "Track Circuit Stopping Point",
        "icon": "🚦",
        "colour": "#00875A",
        "purpose": "Verify correspondence between variants and invariants for a track-circuit stopping point.",
        "checks": [
            ("1. Restrictive", "Track circuit occupied", "Yes"),
            ("1. Restrictive", "Available mode", "CM/AUTO"),
            ("1. Restrictive", "Loc Max", "Record value"),
            ("1. Restrictive", "Target Distance", "Record value"),
            ("1. Restrictive", "Singularity Abscissa", "Record value"),
            ("1. Restrictive", "Loc Max + Target Distance = Abscissa", "Within ±0.75 m when target speed is 0"),
            ("1. Restrictive", "OMAP Target Speed", "0"),
            ("1. Restrictive", "Speedometer Target Speed", "0"),
            ("1. Restrictive", "Associated Variant", "Restrictive"),
            ("2. Permissive", "Track circuit free", "Yes"),
            ("2. Permissive", "Target Distance", "Greater than or equal to restrictive target distance"),
            ("2. Permissive", "OMAP Target Speed", "Record value"),
            ("2. Permissive", "Speedometer Target Speed", "> 0"),
            ("2. Permissive", "Associated Variant", "Permissive"),
            ("3. Log Check", "Variant toggled", "Yes"),
        ],
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "title": "Special Stopping Point Test – Signal Without Overlap",
        "short_title": "Signal Without Overlap",
        "icon": "🔴",
        "colour": "#D64545",
        "purpose": "Verify correspondence between variants and invariants for a signal stopping point without overlap.",
        "checks": [
            ("1. Restrictive", "Signal restrictive / red", "Yes"),
            ("1. Restrictive", "Available mode", "CM/AUTO"),
            ("1. Restrictive", "Loc Max", "Record value"),
            ("1. Restrictive", "Target Distance", "Record value"),
            ("1. Restrictive", "Singularity Abscissa", "Record value"),
            ("1. Restrictive", "OMAP Target Speed", "0"),
            ("1. Restrictive", "Speedometer Target Speed", "0"),
            ("1. Restrictive", "Associated Variant", "Restrictive"),
            ("2. Permissive", "Signal permissive / blue", "Yes"),
            ("2. Permissive", "Target Distance", "Greater than or equal to restrictive target distance"),
            ("2. Permissive", "OMAP Target Speed", "Record value"),
            ("2. Permissive", "Speedometer Target Speed", "> 0"),
            ("2. Permissive", "Associated Variant", "Permissive"),
        ],
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "title": "Complex Stopping Point Test – Signal With Overlap",
        "short_title": "Signal With Overlap",
        "icon": "🔴",
        "colour": "#C0392B",
        "purpose": "Verify correspondence between variants and invariants for a signal stopping point with overlap.",
        "checks": [
            ("1. Signal Red / Overlap Occupied", "Available mode", "CM/AUTO"),
            ("1. Signal Red / Overlap Occupied", "OMAP Target Speed", "0"),
            ("1. Signal Red / Overlap Occupied", "Signal and overlap variants", "Restrictive"),
            ("2. Signal Red / Overlap Free", "Proceed train in CM/AM", "Yes"),
            ("2. Signal Red / Overlap Free", "Overlap variant", "Permissive"),
            ("3. Signal Blue / Overlap Free", "Target Distance", "Greater than or equal to restrictive target distance"),
            ("3. Signal Blue / Overlap Free", "Signal and overlap variants", "Permissive"),
        ],
    },
    "LS06": {
        "id": "ATC-TNC-TRA-LS06",
        "title": "Stopping Point Test – Point",
        "short_title": "Convergence / Divergence Point",
        "icon": "🔀",
        "colour": "#7D3C98",
        "purpose": "Verify correspondence between variants and invariants for a convergence or divergence point.",
        "checks": [
            ("1. Point Uncontrolled", "Available mode", "CM/AM"),
            ("1. Point Uncontrolled", "Loc Max / Target Distance / Abscissa", "Record values"),
            ("1. Point Uncontrolled", "OMAP Target Speed", "0"),
            ("1. Point Uncontrolled", "Associated Variant", "Restrictive"),
            ("2. Point Controlled", "Target Distance", "Greater than or equal to restrictive target distance"),
            ("2. Point Controlled", "OMAP Target Speed", "Record value"),
            ("2. Point Controlled", "Speedometer Target Speed", "> 0"),
            ("2. Point Controlled", "Associated Variant", "Permissive"),
        ],
    },
    "LS07": {
        "id": "ATC-TNC-TRA-LS07",
        "title": "Stopping Point Test – End of SACEM",
        "short_title": "End of SACEM",
        "icon": "⛔",
        "colour": "#5D6D7E",
        "purpose": "Verify that no CM/AM movement is available beyond the End of SACEM.",
        "checks": [
            ("1", "Available mode before End of SACEM", "CM/AUTO"),
            ("1", "Loc Max", "Record value"),
            ("1", "Target Distance", "Record value"),
            ("1", "Singularity Abscissa", "Record value"),
            ("1", "OMAP Target Speed", "0"),
            ("1", "Speedometer Target Speed", "0"),
            ("2", "CM/AM availability beyond End of SACEM", "Not available"),
        ],
    },
    "LS08": {
        "id": "ATC-TNC-TRA-LS08",
        "title": "Station Stopping Point and Door Side Verification",
        "short_title": "Station Stop / Door Side",
        "icon": "🚉",
        "colour": "#00796B",
        "purpose": "Verify station stopping point, door enable window and correct door side.",
        "checks": [
            ("1", "Localisation Status", "Total Loc"),
            ("1", "Available Mode", "CM/AM"),
            ("1", "Selected Mode", "CM"),
            ("1", "Loc Max", "Record value"),
            ("1", "Precise Stop", "Record value"),
            ("1", "Distance from precise stop", "Within ±0.35 m"),
            ("1", "Correct door side enabled", "Yes"),
            ("2", "Door enabled when stop short", "Within 0.35 m"),
            ("3", "Door enabled when overrun", "Within 0.35 m"),
        ],
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "title": "PSD Operation Pre-Test",
        "short_title": "PSD Operation",
        "icon": "🚪",
        "colour": "#1565C0",
        "purpose": "Verify ATC/PSD door-open and door-close command interface behaviour.",
        "checks": [
            ("1", "Door open on incorrect side", "Relay not activated / PSD remains closed"),
            ("2", "Door open on correct side", "Relay activated / PSD opens"),
            ("2", "All doors closed relay", "De-energized"),
            ("2", "Associated variants", "Restrictive"),
            ("2", "Available mode", "RM only"),
            ("3", "PIOS / interlock override", "CM/AM available and variants permissive"),
            ("3", "After 60 seconds", "RM only and variants restrictive"),
            ("4", "Door close on correct side", "Relay activated / PSD closed"),
            ("4", "All doors closed relay", "Energized and variants permissive"),
        ],
    },
    "LS10": {
        "id": "ATC-TNC-TRA-LS10",
        "title": "Emergency Stop Zone Test",
        "short_title": "Emergency Stop Zone",
        "icon": "⚠️",
        "colour": "#D35400",
        "purpose": "Verify no CM/AUTO movement is available in an active Emergency Stop Zone.",
        "checks": [
            ("1. ESZ Active", "Associated Variant", "Restrictive"),
            ("1. ESZ Active", "Available mode before ESZ", "CM/AUTO"),
            ("1. ESZ Active", "Loc Max / Target Distance / Abscissa", "Record values"),
            ("1. ESZ Active", "OMAP Target Speed", "0"),
            ("2. ESZ Restored", "Associated Variant", "Permissive"),
            ("2. ESZ Restored", "Target Distance", "Greater than or equal to restrictive target distance"),
            ("3. Run through ESZ", "CM/AM inside ESZ", "Not available"),
            ("3. Run through ESZ", "CM/AM at end of ESZ", "Available"),
        ],
    },
    "LS11": {
        "id": "ATC-TNC-TRA-LS11",
        "title": "Change of End Test",
        "short_title": "Change of End",
        "icon": "🔄",
        "colour": "#5E35B1",
        "purpose": "Verify Change of End can be completed without delocalization or Emergency Brake.",
        "checks": [
            ("Step 1 – Cab 1", "Localisation Status", "Total Loc"),
            ("Step 1 – Cab 1", "Available Mode", "CM/AUTO"),
            ("Step 1 – Cab 1", "Loc Max / Loc Min", "Record values"),
            ("Step 2 – Cab 2", "Selected Mode", "Shutdown"),
            ("Step 2 – Cab 2", "Distance to signal", "< 12 m"),
            ("Step 3 – Cab 2", "Localisation Status", "Total Loc"),
            ("Step 3 – Cab 2", "CM/AM availability", "Available within 20 seconds"),
            ("Step 4 – Cab 1", "Localisation Status", "Wait Cab"),
            ("Step 4", "First RB / MTIB", "No delocalization"),
        ],
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "title": "Temporary Speed Restriction Test",
        "short_title": "Temporary Speed Restriction",
        "icon": "🚧",
        "colour": "#EF6C00",
        "purpose": "Verify correct train behaviour when a TSR is applied.",
        "checks": [
            ("1", "Track Circuit", "Record selected track circuit"),
            ("1", "ATC Section", "Record section"),
            ("1", "TSR Value", "Record TSR value below 22 km/h where applicable"),
            ("1", "ATC / ATS acceptance", "Accepted"),
            ("2", "Train target speed", "Follows TSR set in signalling room"),
            ("2", "Emergency Brake", "No EB"),
        ],
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "title": "Return Channel Test",
        "short_title": "Return Channel",
        "icon": "📶",
        "colour": "#00838F",
        "purpose": "Verify train information is received by ATC trackside through the return-channel loop.",
        "checks": [
            ("1", "Return-channel loop location", "Record location"),
            ("1", "Train information received by ATC", "Yes"),
            ("1", "Evidence source", "OMAP / ATC_SOL / ATC_MT / ATS log"),
        ],
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "title": "End of Shunt Test",
        "short_title": "End of Shunt",
        "icon": "🚂",
        "colour": "#455A64",
        "purpose": "Verify end-of-shunt declaration and CM/AM recovery position.",
        "checks": [
            ("Step 1", "Shunt Route Name", "Record value"),
            ("Step 1", "Begin Branch / Loc Max / Target Distance", "Record values"),
            ("Step 2", "Selected Mode", "RM"),
            ("Step 3", "CM/AM resumes", "Record branch and Loc Max"),
            ("Step 3", "Recovery location", "Matches approved invariant data"),
        ],
    },
}


EVENT_COLUMNS = [
    "test_code",
    "event_description",
    "sector",
    "section",
    "branch",
    "subsection",
    "abscissa_m",
    "variant_number",
    "anticipated_variant_rank",
    "signal_name",
    "track_circuit",
    "point_name",
    "station_name",
    "door_side",
    "source_page",
    "review_status",
    "remarks",
]


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class RouteDefinition:
    route_name: str
    direction: str
    sector: str
    section: str
    branches_to_run: str
    anticipated_branches: str
    entry_speed_kph: Optional[float]
    initialization_distance_m: Optional[float]
    source_page: int


# =============================================================================
# HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalise_branch(value: Any) -> str:
    text = safe_text(value)
    match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})", text)

    if not match:
        return text

    sector, section, subsection, branch = match.groups()

    return (
        f"{int(sector):02d}-"
        f"{int(section):02d}-"
        f"{int(subsection)}-"
        f"{int(branch)}"
    )


def branch_tokens(value: Any) -> List[str]:
    found = re.findall(
        r"\b\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2}\b",
        safe_text(value),
    )

    unique_items = []

    for item in found:
        normalised = normalise_branch(item)

        if normalised not in unique_items:
            unique_items.append(normalised)

    return unique_items


def subsection_from_branch(branch: str) -> str:
    values = safe_text(branch).split("-")

    if len(values) >= 3:
        return "-".join(values[:3])

    return ""


def identify_event_type(text: str) -> str:
    value = safe_text(text).upper()

    if "MTIB" in value or "MOVING TRAIN INITIAL" in value:
        return "LS01"

    if "STIB" in value or "STATIONARY TRAIN INITIAL" in value:
        return "LS02"

    if "END OF SHUNT" in value or "EXIT SACEM" in value:
        return "LS14"

    if "CHANGE OF END" in value or "CHANGE ENDS" in value:
        return "LS11"

    if "EMERGENCY STOP ZONE" in value or "EMERGENCY ZONE" in value:
        return "LS10"

    if "RETURN CHANNEL" in value or "R/C LOOP" in value:
        return "LS13"

    if "TEMPORARY SPEED RESTRICTION" in value or "TSR" in value:
        return "LS12"

    if "PSD" in value:
        return "LS09"

    if "STATION STOP" in value or "DOOR SIDE" in value:
        return "LS08"

    if "END OF SACEM" in value:
        return "LS07"

    if "OVERLAP" in value or "O/L" in value:
        return "LS05"

    if "POINT" in value or re.search(r"\b(RP|NP)\b", value):
        return "LS06"

    if "SIGNAL" in value or "PEP" in value:
        return "LS04"

    if "TRACK CIRCUIT" in value or "T/C" in value:
        return "LS03"

    return ""


# =============================================================================
# PDF EXTRACTION
# =============================================================================

def extract_pdf_text(pdf_bytes: bytes) -> List[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: pypdf. Run: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))

    return [
        page.extract_text() or ""
        for page in reader.pages
    ]


def parse_routes_from_pdf(page_texts: List[str]) -> pd.DataFrame:
    """
    Parses Appendix J / sector subdivision route-definition pages.

    Expected style:
    SECTOR 09, SECTION 01
    1 - First route, to Up TCL : 09-01-0-0, 09-01-1-0
    - Anticipated branches to be run : 09-01-2-0
    Entry speed : Ve = 135 km/h
    Initialisation distance at 22 km/h = 160 m
    """

    rows: List[Dict[str, Any]] = []

    route_pattern = re.compile(
        r"(?P<number>\d+)\s*-\s*"
        r"(?P<route_order>First|Second|Third|Fourth)\s+route"
        r"(?:\s*,?\s*(?:to|section branches run)\s*"
        r"(?P<direction>Up|Dn|Down|UP|DN)\s*TCL)?"
        r"\s*:\s*(?P<branches>.*?)"
        r"(?="
        r"\n\s*-\s*Anticipated branches"
        r"|\n\s*Entry speed"
        r"|\n\s*Initiali[sz]ation distance"
        r"|\n\s*\d+\s*-\s*(?:First|Second|Third|Fourth)\s+route"
        r"|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for page_number, page_text in enumerate(page_texts, start=1):
        clean_text = re.sub(r"[ \t]+", " ", page_text)
        clean_text = re.sub(r"\n{2,}", "\n", clean_text)

        sector_match = re.search(
            r"SECTOR\s*[: ]?\s*(\d{1,2}).{0,30}?"
            r"SECTION\s*[: ]?\s*(\d{1,2})",
            clean_text,
            flags=re.IGNORECASE,
        )

        if not sector_match:
            continue

        sector = f"{int(sector_match.group(1)):02d}"
        section = f"{int(sector_match.group(2)):02d}"

        for match in route_pattern.finditer(clean_text):
            route_block = clean_text[
                match.start(): min(len(clean_text), match.end() + 1800)
            ]

            anticipated_match = re.search(
                r"Anticipated branches to be run\s*:\s*(.*?)"
                r"(?="
                r"\n\s*Entry speed"
                r"|\n\s*Initiali[sz]ation distance"
                r"|\n\s*\d+\s*-\s*(?:First|Second|Third|Fourth)\s+route"
                r"|$)",
                route_block,
                flags=re.IGNORECASE | re.DOTALL,
            )

            speed_match = re.search(
                r"Entry speed\s*:\s*Ve\s*=\s*(\d+(?:\.\d+)?)",
                route_block,
                flags=re.IGNORECASE,
            )

            distance_match = re.search(
                r"Initiali[sz]ation distance.*?=\s*(\d+(?:\.\d+)?)\s*m",
                route_block,
                flags=re.IGNORECASE,
            )

            route_order = match.group("route_order").title()
            direction = safe_text(match.group("direction")).title()

            if direction == "Dn":
                direction = "Down"

            route = RouteDefinition(
                route_name=f"{sector}-{section} {route_order} Route",
                direction=direction or "Direction not extracted",
                sector=sector,
                section=section,
                branches_to_run=", ".join(branch_tokens(match.group("branches"))),
                anticipated_branches=(
                    ", ".join(branch_tokens(anticipated_match.group(1)))
                    if anticipated_match
                    else ""
                ),
                entry_speed_kph=(
                    float(speed_match.group(1))
                    if speed_match
                    else None
                ),
                initialization_distance_m=(
                    float(distance_match.group(1))
                    if distance_match
                    else None
                ),
                source_page=page_number,
            )

            if route.branches_to_run:
                rows.append(route.__dict__)

    if not rows:
        return pd.DataFrame(
            columns=[
                "route_name",
                "direction",
                "sector",
                "section",
                "branches_to_run",
                "anticipated_branches",
                "entry_speed_kph",
                "initialization_distance_m",
                "source_page",
            ]
        )

    return (
        pd.DataFrame(rows)
        .drop_duplicates()
        .reset_index(drop=True)
    )


def extract_event_candidates(page_texts: List[str]) -> pd.DataFrame:
    """
    Extracts possible LS01-LS14 events from text/OCR PDF content.

    This is intentionally conservative. Engineering review is mandatory.
    """

    rows: List[Dict[str, Any]] = []

    abscissa_pattern = re.compile(
        r"(?:Abs(?:cissa)?|K\.?P\.?|Installation\s*K\.?P\.?)"
        r"\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)",
        flags=re.IGNORECASE,
    )

    branch_pattern = re.compile(
        r"\b\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2}\b"
    )

    for page_number, page_text in enumerate(page_texts, start=1):
        lines = [
            re.sub(r"\s+", " ", item).strip()
            for item in page_text.splitlines()
            if item.strip()
        ]

        current_sector = ""
        current_section = ""
        current_branch = ""
        current_abscissa: Optional[float] = None

        for index, line in enumerate(lines):
            sector_match = re.search(
                r"Sector\s*[:=]?\s*(\d{1,2})",
                line,
                flags=re.IGNORECASE,
            )

            if sector_match:
                current_sector = f"{int(sector_match.group(1)):02d}"

            section_match = re.search(
                r"Section\s*[:=]?\s*(\d{1,2})",
                line,
                flags=re.IGNORECASE,
            )

            if section_match:
                current_section = f"{int(section_match.group(1)):02d}"

            branch_match = branch_pattern.search(line)

            if branch_match:
                current_branch = normalise_branch(branch_match.group(0))

                parts = current_branch.split("-")

                if len(parts) == 4:
                    current_sector = parts[0]
                    current_section = parts[1]

            abscissa_match = abscissa_pattern.search(line)

            if abscissa_match:
                current_abscissa = float(
                    abscissa_match.group(1).replace(",", ".")
                )

            context = " ".join(
                lines[max(index - 1, 0): min(index + 2, len(lines))]
            )

            test_code = identify_event_type(context)

            if not test_code:
                continue

            variant_match = re.search(
                r"(?:Associated\s+Variant|Variant(?:\s+Number)?)"
                r"\s*[:=]?\s*(\d+)",
                context,
                flags=re.IGNORECASE,
            )

            rows.append(
                {
                    "test_code": test_code,
                    "event_description": context,
                    "sector": current_sector,
                    "section": current_section,
                    "branch": current_branch,
                    "subsection": subsection_from_branch(current_branch),
                    "abscissa_m": current_abscissa,
                    "variant_number": (
                        variant_match.group(1)
                        if variant_match
                        else ""
                    ),
                    "anticipated_variant_rank": "",
                    "signal_name": "",
                    "track_circuit": "",
                    "point_name": "",
                    "station_name": "",
                    "door_side": "",
                    "source_page": page_number,
                    "review_status": "To be reviewed",
                    "remarks": "",
                }
            )

    if not rows:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    return (
        pd.DataFrame(rows)
        .drop_duplicates(
            subset=[
                "test_code",
                "event_description",
                "source_page",
            ]
        )
        .reset_index(drop=True)
    )


# =============================================================================
# ROUTE AND TEST-PLAN LOGIC
# =============================================================================

def event_matches_route(
    event: pd.Series,
    route: pd.Series,
) -> bool:
    if safe_text(event.get("sector")) not in ("", safe_text(route["sector"])):
        return False

    if safe_text(event.get("section")) not in ("", safe_text(route["section"])):
        return False

    route_branches = branch_tokens(route["branches_to_run"])
    anticipated_branches = branch_tokens(route["anticipated_branches"])

    event_branch = normalise_branch(event.get("branch", ""))
    event_subsection = safe_text(event.get("subsection"))

    if not event_branch and not event_subsection:
        return True

    if event_branch:
        return (
            event_branch in route_branches
            or event_branch in anticipated_branches
        )

    return any(
        event_subsection in branch
        for branch in route_branches + anticipated_branches
    )


def create_test_plan(
    route: pd.Series,
    events: pd.DataFrame,
    include_tsr: bool,
    include_end_sacem: bool,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    if not events.empty:
        for _, event in events.iterrows():
            if not event_matches_route(event, route):
                continue

            test_codes = [
                item.strip()
                for item in safe_text(event.get("test_code")).split("/")
                if item.strip()
            ]

            for test_code in test_codes:
                if test_code not in TEST_TYPES:
                    continue

                record = event.to_dict()
                record["test_code"] = test_code
                record["test_id"] = TEST_TYPES[test_code]["id"]
                record["test_name"] = TEST_TYPES[test_code]["title"]
                record["route_name"] = route["route_name"]
                record["route_direction"] = route["direction"]
                record["route_branches"] = route["branches_to_run"]
                record["anticipated_branches"] = route["anticipated_branches"]
                rows.append(record)

    if include_tsr:
        rows.append(
            {
                "test_code": "LS12",
                "test_id": TEST_TYPES["LS12"]["id"],
                "test_name": TEST_TYPES["LS12"]["title"],
                "event_description": (
                    "TSR sample test. Engineer must nominate one suitable "
                    "track circuit and TSR value."
                ),
                "sector": route["sector"],
                "section": route["section"],
                "branch": "",
                "subsection": "",
                "abscissa_m": None,
                "variant_number": "",
                "anticipated_variant_rank": "",
                "route_name": route["route_name"],
                "route_direction": route["direction"],
                "route_branches": route["branches_to_run"],
                "anticipated_branches": route["anticipated_branches"],
                "review_status": "To be reviewed",
                "remarks": "",
            }
        )

    if include_end_sacem:
        rows.append(
            {
                "test_code": "LS07",
                "test_id": TEST_TYPES["LS07"]["id"],
                "test_name": TEST_TYPES["LS07"]["title"],
                "event_description": (
                    "End of SACEM location to be confirmed from approved "
                    "sector subdivision."
                ),
                "sector": route["sector"],
                "section": route["section"],
                "branch": "",
                "subsection": "",
                "abscissa_m": None,
                "variant_number": "",
                "anticipated_variant_rank": "",
                "route_name": route["route_name"],
                "route_direction": route["direction"],
                "route_branches": route["branches_to_run"],
                "anticipated_branches": route["anticipated_branches"],
                "review_status": "To be reviewed",
                "remarks": "",
            }
        )

    plan = pd.DataFrame(rows)

    if plan.empty:
        return plan

    return (
        plan.drop_duplicates(
            subset=[
                "test_code",
                "route_name",
                "branch",
                "subsection",
                "abscissa_m",
                "event_description",
            ]
        )
        .reset_index(drop=True)
    )


# =============================================================================
# VISUAL ROUTE DIAGRAM
# =============================================================================

def build_route_diagram(route: pd.Series) -> str:
    branches = branch_tokens(route["branches_to_run"])
    anticipated = branch_tokens(route["anticipated_branches"])

    all_branches = branches + anticipated

    if not all_branches:
        all_branches = ["No route branches extracted"]

    node_width = max(150, int(900 / max(len(all_branches), 1)))
    svg_width = max(900, len(all_branches) * node_width + 120)

    nodes = []

    for index, branch in enumerate(all_branches):
        x = 70 + (index * node_width)
        is_anticipated = branch in anticipated

        fill = "#FFF3E0" if is_anticipated else "#E8F4FD"
        stroke = "#EF6C00" if is_anticipated else "#0064A6"
        label = "ANTICIPATE" if is_anticipated else "RUN"

        nodes.append(
            f"""
            <line x1="{x - 55}" y1="125" x2="{x + 55}" y2="125"
                  stroke="{stroke}" stroke-width="7" stroke-linecap="round"/>
            <circle cx="{x}" cy="125" r="25" fill="{fill}"
                    stroke="{stroke}" stroke-width="5"/>
            <text x="{x}" y="180" text-anchor="middle"
                  font-size="14" fill="#102A43" font-weight="700">
                {branch}
            </text>
            <text x="{x}" y="204" text-anchor="middle"
                  font-size="11" fill="{stroke}" font-weight="700">
                {label}
            </text>
            """
        )

    direction = safe_text(route.get("direction")) or "Direction not extracted"

    return f"""
    <div style="
        overflow-x:auto;
        background:#ffffff;
        border:1px solid #D9E2EC;
        border-radius:16px;
        padding:12px;
        margin-top:8px;
    ">
        <svg width="{svg_width}" height="245" xmlns="http://www.w3.org/2000/svg">
            <rect width="{svg_width}" height="245" rx="14" fill="#F8FBFD"/>
            <text x="35" y="42" font-size="20" font-weight="700" fill="#102A43">
                {route["route_name"]}
            </text>
            <text x="35" y="68" font-size="14" fill="#52606D">
                Direction: {direction} | Blue = route to run | Orange = anticipated route
            </text>
            <line x1="30" y1="125" x2="{svg_width - 30}" y2="125"
                  stroke="#B0BEC5" stroke-width="4"/>
            <polygon points="{svg_width - 25},125 {svg_width - 45},115 {svg_width - 45},135"
                     fill="#102A43"/>
            {''.join(nodes)}
        </svg>
    </div>
    """


# =============================================================================
# DOCX EXPORT
# =============================================================================

def set_cell_text(cell: Any, text: Any, bold: bool = False) -> None:
    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(text))
    run.bold = bold


def create_docx_test_sheet(
    row: pd.Series,
    project: Dict[str, str],
    sheet_number: str,
) -> bytes:
    try:
        from docx import Document
        from docx.enum.section import WD_SECTION
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: python-docx. "
            "Run: pip install python-docx"
        ) from exc

    test_code = safe_text(row.get("test_code"))
    test = TEST_TYPES.get(test_code, TEST_TYPES["LS03"])

    document = Document()
    section = document.sections[0]

    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.42)
    section.right_margin = Inches(0.42)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run("ALSTOM")
    title_run.bold = True
    title_run.font.size = Pt(18)
    title_run.font.color.rgb = RGBColor(16, 42, 67)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle_run = subtitle.add_run(
        f"{test['id']} – {test['title']}"
    )
    subtitle_run.bold = True
    subtitle_run.font.size = Pt(13)

    document.add_paragraph()

    header_table = document.add_table(rows=4, cols=4)
    header_table.style = "Table Grid"

    header_values = [
        ("Procedure Reference", project["procedure_reference"]),
        ("Procedure Title", project["procedure_title"]),
        ("Test Sheet No.", sheet_number),
        ("Project", project["project"]),
        ("Trackside Version", project["trackside_version"]),
        ("ATP Version", project["atp_version"]),
        ("ATO Version", project["ato_version"]),
        ("Generated Date", str(date.today())),
    ]

    for index, (label, value) in enumerate(header_values):
        table_row = index // 2
        column = (index % 2) * 2

        set_cell_text(header_table.cell(table_row, column), label, bold=True)
        set_cell_text(header_table.cell(table_row, column + 1), value)

    document.add_paragraph()

    location_heading = document.add_paragraph()
    location_heading.add_run("Test Location / Design Data").bold = True

    location_table = document.add_table(rows=5, cols=4)
    location_table.style = "Table Grid"

    location_values = [
        ("Route", row.get("route_name", "")),
        ("Direction", row.get("route_direction", "")),
        ("Sector", row.get("sector", "")),
        ("Section", row.get("section", "")),
        ("Branch Number", row.get("branch", "")),
        ("Sub-section", row.get("subsection", "")),
        ("Abscissa", f"{safe_text(row.get('abscissa_m'))} m"),
        ("Variant Number", row.get("variant_number", "")),
        ("Anticipated Variant Rank", row.get("anticipated_variant_rank", "")),
        ("Source Page", row.get("source_page", "")),
    ]

    for index, (label, value) in enumerate(location_values):
        table_row = index // 2
        column = (index % 2) * 2

        set_cell_text(location_table.cell(table_row, column), label, bold=True)
        set_cell_text(location_table.cell(table_row, column + 1), value)

    document.add_paragraph()

    purpose = document.add_paragraph()
    purpose.add_run("Purpose of Test: ").bold = True
    purpose.add_run(test["purpose"])

    document.add_paragraph()

    record_heading = document.add_paragraph()
    record_heading.add_run("Test Record").bold = True

    test_table = document.add_table(rows=1, cols=5)
    test_table.style = "Table Grid"

    headers = [
        "Step",
        "Check / Record",
        "Expected Value / Requirement",
        "Actual Result",
        "Pass / Fail",
    ]

    for index, value in enumerate(headers):
        set_cell_text(test_table.rows[0].cells[index], value, bold=True)

    for step, check, expected in test["checks"]:
        cells = test_table.add_row().cells
        set_cell_text(cells[0], step)
        set_cell_text(cells[1], check)
        set_cell_text(cells[2], expected)
        set_cell_text(cells[3], "")
        set_cell_text(cells[4], "☐ Pass  ☐ Fail  ☐ N/A")

    document.add_paragraph()

    remarks_heading = document.add_paragraph()
    remarks_heading.add_run("Engineering Remarks / Evidence Reference").bold = True

    remarks = document.add_paragraph()
    remarks.add_run(
        safe_text(row.get("remarks"))
        or "Source event: "
        + safe_text(row.get("event_description"))
    )

    document.add_paragraph()

    result_table = document.add_table(rows=3, cols=3)
    result_table.style = "Table Grid"

    set_cell_text(
        result_table.cell(0, 0),
        "Test Result",
        bold=True,
    )
    set_cell_text(
        result_table.cell(0, 1),
        "☐ Passed    ☐ Failed    ☐ Punchlist Raised",
    )
    result_table.cell(0, 1).merge(result_table.cell(0, 2))

    for index, value in enumerate(["Role", "Name / Signature", "Date"]):
        set_cell_text(result_table.cell(1, index), value, bold=True)

    set_cell_text(result_table.cell(2, 0), "Alstom Test Engineer")
    set_cell_text(result_table.cell(2, 1), "")
    set_cell_text(result_table.cell(2, 2), "")

    witness_row = result_table.add_row().cells
    set_cell_text(witness_row[0], "MTR Witness")
    set_cell_text(witness_row[1], "")
    set_cell_text(witness_row[2], "")

    document.add_paragraph()

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – Verify the applicable approved version before use."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(8)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_docx_zip(
    plan: pd.DataFrame,
    project: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for index, (_, row) in enumerate(plan.iterrows(), start=1):
            test_code = safe_text(row.get("test_code")) or "MANUAL"
            branch = safe_text(row.get("branch")) or "GENERAL"
            abscissa = safe_text(row.get("abscissa_m")) or "NA"

            sheet_number = (
                f"{project['sheet_prefix']}-{index:03d}"
            )

            filename = (
                f"{sheet_number}_{test_code}_{branch}_{abscissa}m.docx"
            )

            filename = re.sub(
                r'[\\/:*?"<>|]',
                "_",
                filename,
            )

            archive.writestr(
                filename,
                create_docx_test_sheet(
                    row=row,
                    project=project,
                    sheet_number=sheet_number,
                ),
            )

    return output.getvalue()


def create_excel_bytes(
    plan: pd.DataFrame,
    route: pd.Series,
    project: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        cover = pd.DataFrame(
            [
                ["Procedure Reference", project["procedure_reference"]],
                ["Procedure Title", project["procedure_title"]],
                ["Project", project["project"]],
                ["Trackside Version", project["trackside_version"]],
                ["ATP Version", project["atp_version"]],
                ["ATO Version", project["ato_version"]],
                ["Generated Date", str(date.today())],
                ["Selected Route", route["route_name"]],
                ["Direction", route["direction"]],
                ["Sector", route["sector"]],
                ["Section", route["section"]],
                ["Branches to Run", route["branches_to_run"]],
                ["Anticipated Branches", route["anticipated_branches"]],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(
            writer,
            sheet_name="Cover",
            index=False,
        )

        plan.to_excel(
            writer,
            sheet_name="Test Register",
            index=False,
        )

        workbook = writer.book

        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column in worksheet.columns:
                maximum = max(
                    len(safe_text(cell.value))
                    for cell in column
                )

                worksheet.column_dimensions[
                    column[0].column_letter
                ].width = min(maximum + 2, 55)

    return output.getvalue()


# =============================================================================
# UI
# =============================================================================

def render_styles() -> None:
    st.markdown(
        """
        <style>
        .ts-hero {
            background: linear-gradient(135deg, #102A43 0%, #164E72 70%, #E52B1E 160%);
            padding: 28px 34px;
            border-radius: 20px;
            color: white;
            margin-bottom: 20px;
            box-shadow: 0 10px 26px rgba(16,42,67,.18);
        }

        .ts-hero h1 {
            color: white !important;
            margin: 0 !important;
            font-size: 30px !important;
        }

        .ts-hero p {
            color: #E4F2FC !important;
            margin: 8px 0 0 0 !important;
            font-size: 15px;
        }

        .instruction-card {
            background: #F8FBFD;
            border: 1px solid #D9E2EC;
            border-left: 5px solid #0064A6;
            border-radius: 12px;
            padding: 16px 18px;
            margin: 12px 0;
        }

        .warning-card {
            background: #FFF7E8;
            border: 1px solid #F4C66A;
            border-left: 5px solid #E37400;
            border-radius: 12px;
            padding: 16px 18px;
            margin: 12px 0;
        }

        .route-help {
            color: #52606D;
            font-size: 14px;
        }

        div.stButton > button {
            border-radius: 12px;
            min-height: 54px;
            font-weight: 650;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_route_cards(routes: pd.DataFrame) -> Optional[int]:
    """
    Uses cards/buttons rather than a normal selectbox.
    """

    if routes.empty:
        return None

    st.subheader("Choose your route")

    st.caption(
        "Step 1: Find the route name. "
        "Step 2: Click the large route button. "
        "Step 3: Check the blue and orange route diagram."
    )

    if "selected_route_index" not in st.session_state:
        st.session_state["selected_route_index"] = 0

    columns = st.columns(3)

    for index, (_, route) in enumerate(routes.iterrows()):
        with columns[index % 3]:
            branches = branch_tokens(route["branches_to_run"])

            label = (
                f"🛤️ {route['route_name']}\n\n"
                f"Direction: {route['direction'] or 'Not extracted'}\n"
                f"Route branches: {len(branches)}"
            )

            if st.button(
                label,
                key=f"route_card_{index}",
                use_container_width=True,
            ):
                st.session_state["selected_route_index"] = index

    selected_index = st.session_state["selected_route_index"]

    if selected_index >= len(routes):
        selected_index = 0
        st.session_state["selected_route_index"] = 0

    return selected_index


# =============================================================================
# MAIN STREAMLIT FUNCTION
# =============================================================================

def render_test_sheet_generator_app() -> None:
    render_styles()

    st.markdown(
        """
        <div class="ts-hero">
            <h1>ATC Low-Speed Test Sheet Generator</h1>
            <p>
                Upload approved PDF documents, choose the train route visually,
                review extracted test points, and generate DOCX test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="warning-card">
            <b>Engineering control:</b> PDF extraction is a planning aid only.
            The responsible ATC engineer must verify all route branches,
            singularities, abscissae, variants and anticipated locations
            against the approved controlled design documents before issue.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Project and document information", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            project_name = st.text_input(
                "Project / Contract",
                value="Contract 1252A – Signalling Works",
                key="ts_project_name",
            )

            trackside_version = st.text_input(
                "Trackside Version",
                key="ts_trackside_version",
            )

            atp_version = st.text_input(
                "ATP Version",
                key="ts_atp_version",
            )

        with col2:
            procedure_reference = st.text_input(
                "Procedure Reference",
                value=PROCEDURE_REFERENCE,
                key="ts_procedure_reference",
            )

            procedure_title = st.text_input(
                "Procedure Title",
                value=PROCEDURE_TITLE,
                key="ts_procedure_title",
            )

            ato_version = st.text_input(
                "ATO Version",
                key="ts_ato_version",
            )

        sheet_prefix = st.text_input(
            "Test Sheet Number Prefix",
            value="C1252-TAC-0274",
            key="ts_sheet_prefix",
        )

    project = {
        "project": project_name,
        "procedure_reference": procedure_reference,
        "procedure_title": procedure_title,
        "trackside_version": trackside_version,
        "atp_version": atp_version,
        "ato_version": ato_version,
        "sheet_prefix": sheet_prefix,
    }

    st.markdown("## 1. Upload PDF documents")

    st.markdown(
        """
        <div class="instruction-card">
            <b>What to upload?</b><br>
            <b>Document A:</b> The low-speed dynamic test procedure PDF.<br>
            <b>Document B:</b> The ATC Sector Subdivision / Appendix J /
            transmission-route PDF containing route branches and singularities.
            <br><br>
            Only PDF files are required. No CSV or Excel input file is needed.
        </div>
        """,
        unsafe_allow_html=True,
    )

    upload_col1, upload_col2 = st.columns(2)

    with upload_col1:
        procedure_pdf = st.file_uploader(
            "Document A — Low-Speed Test Procedure PDF",
            type=["pdf"],
            key="ts_procedure_pdf",
            help=(
                "Example: C1252-TAC-0274 Trackside ATC "
                "Low-Speed Dynamic Test Procedure."
            ),
        )

    with upload_col2:
        subdivision_pdf = st.file_uploader(
            "Document B — ATC Sector Subdivision / Appendix J PDF",
            type=["pdf"],
            key="ts_subdivision_pdf",
            help=(
                "This document must contain route branches, sector/section "
                "details and preferably singularity information."
            ),
        )

    if subdivision_pdf is None:
        st.info(
            "Upload the ATC Sector Subdivision or Appendix J PDF to begin. "
            "The procedure PDF defines the test sheets, but the subdivision "
            "PDF supplies the project-specific route and singularity data."
        )
        return

    try:
        with st.spinner("Reading PDF text and finding route information..."):
            subdivision_pages = extract_pdf_text(
                subdivision_pdf.getvalue()
            )

            routes = parse_routes_from_pdf(subdivision_pages)
            events = extract_event_candidates(subdivision_pages)

            procedure_pages: List[str] = []

            if procedure_pdf is not None:
                procedure_pages = extract_pdf_text(
                    procedure_pdf.getvalue()
                )

        st.success(
            f"PDF processed successfully. "
            f"Pages scanned: {len(subdivision_pages)} | "
            f"Routes found: {len(routes)} | "
            f"Possible events found: {len(events)}"
        )

    except Exception as exc:
        st.error(f"Unable to process PDF: {exc}")
        return

    if routes.empty:
        st.error(
            "No route definitions were found in this PDF. "
            "The route document must contain text similar to "
            "'SECTOR 09, SECTION 01', 'First route', and route branch "
            "numbers such as 09-01-1-0."
        )

        with st.expander("View extracted PDF text for troubleshooting"):
            for index, text in enumerate(subdivision_pages[:10], start=1):
                st.text_area(
                    f"PDF Page {index}",
                    value=text[:5000],
                    height=250,
                    disabled=True,
                    key=f"debug_page_{index}",
                )

        return

    st.markdown("## 2. Choose the route visually")

    selected_route_index = render_route_cards(routes)

    if selected_route_index is None:
        return

    selected_route = routes.iloc[selected_route_index]

    st.markdown(
        build_route_diagram(selected_route),
        unsafe_allow_html=True,
    )

    route_metric_1, route_metric_2, route_metric_3, route_metric_4 = st.columns(4)

    route_metric_1.metric("Sector", selected_route["sector"])
    route_metric_2.metric("Section", selected_route["section"])
    route_metric_3.metric(
        "Entry Speed",
        (
            f"{selected_route['entry_speed_kph']} km/h"
            if pd.notna(selected_route["entry_speed_kph"])
            else "Not extracted"
        ),
    )
    route_metric_4.metric(
        "Initialisation Distance",
        (
            f"{selected_route['initialization_distance_m']} m"
            if pd.notna(selected_route["initialization_distance_m"])
            else "Not extracted"
        ),
    )

    with st.expander("Show selected route information", expanded=False):
        st.write(
            "**Branches to run:**",
            selected_route["branches_to_run"] or "Not extracted",
        )
        st.write(
            "**Anticipated branches:**",
            selected_route["anticipated_branches"] or "None extracted",
        )
        st.write(
            "**Source PDF page:**",
            selected_route["source_page"],
        )

    st.markdown("## 3. Review detected test locations")

    st.markdown(
        """
        <div class="instruction-card">
            The table below contains possible test locations found in the PDF.
            Please review each row. You can correct the branch, abscissa,
            test type, variant number, station name and other engineering data
            before generating test sheets.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if events.empty:
        st.warning(
            "No singularity events could be detected automatically. "
            "This is common for scanned or highly graphical subdivision PDFs. "
            "Use the Add Row button in the table below to enter approved "
            "test locations manually."
        )

        events = pd.DataFrame(
            [
                {
                    "test_code": "",
                    "event_description": "",
                    "sector": selected_route["sector"],
                    "section": selected_route["section"],
                    "branch": "",
                    "subsection": "",
                    "abscissa_m": None,
                    "variant_number": "",
                    "anticipated_variant_rank": "",
                    "signal_name": "",
                    "track_circuit": "",
                    "point_name": "",
                    "station_name": "",
                    "door_side": "",
                    "source_page": "",
                    "review_status": "To be reviewed",
                    "remarks": "",
                }
            ],
            columns=EVENT_COLUMNS,
        )

    reviewed_events = st.data_editor(
        events,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="ts_event_review_editor",
        column_config={
            "test_code": st.column_config.SelectboxColumn(
                "Test Code",
                options=[""] + list(TEST_TYPES.keys()),
                required=False,
            ),
            "review_status": st.column_config.SelectboxColumn(
                "Review Status",
                options=[
                    "To be reviewed",
                    "Reviewed",
                    "Approved for generation",
                    "Not applicable",
                ],
            ),
            "abscissa_m": st.column_config.NumberColumn(
                "Abscissa (m)",
                format="%.3f",
            ),
        },
    )

    st.markdown("## 4. Choose test scope")

    scope_col1, scope_col2, scope_col3 = st.columns(3)

    with scope_col1:
        include_tsr = st.checkbox(
            "Include LS12 TSR sample test",
            value=True,
            help=(
                "Adds one Temporary Speed Restriction test sheet. "
                "Engineer must nominate the final track circuit and TSR value."
            ),
        )

    with scope_col2:
        include_end_sacem = st.checkbox(
            "Include LS07 End of SACEM test",
            value=False,
        )

    with scope_col3:
        only_approved = st.checkbox(
            "Use only rows marked 'Approved for generation'",
            value=False,
        )

    if only_approved:
        events_for_plan = reviewed_events[
            reviewed_events["review_status"]
            .astype(str)
            .str.lower()
            .eq("approved for generation")
        ].copy()
    else:
        events_for_plan = reviewed_events.copy()

    st.markdown("## 5. Generate test-sheet plan")

    generate_clicked = st.button(
        "✨ Generate Proposed Test Sheets",
        type="primary",
        use_container_width=True,
        key="ts_generate_button",
    )

    if generate_clicked:
        plan = create_test_plan(
            route=selected_route,
            events=events_for_plan,
            include_tsr=include_tsr,
            include_end_sacem=include_end_sacem,
        )

        st.session_state["ts_generated_plan"] = plan
        st.session_state["ts_generated_route"] = selected_route.to_dict()

    generated_plan = st.session_state.get(
        "ts_generated_plan",
        pd.DataFrame(),
    )

    if generated_plan.empty:
        st.info(
            "Choose the route, review the event table, then click "
            "'Generate Proposed Test Sheets'."
        )
        return

    st.markdown("## 6. Final review and download")

    st.markdown(
        """
        <div class="warning-card">
            <b>Final engineering check required:</b>
            Confirm route, branch, abscissa, variant number, anticipated
            information, test type and expected behaviour before issuing
            documents for testing.
        </div>
        """,
        unsafe_allow_html=True,
    )

    final_plan = st.data_editor(
        generated_plan,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="ts_final_plan_editor",
        column_config={
            "test_code": st.column_config.SelectboxColumn(
                "Test Code",
                options=list(TEST_TYPES.keys()),
            ),
            "review_status": st.column_config.SelectboxColumn(
                "Review Status",
                options=[
                    "To be reviewed",
                    "Reviewed",
                    "Approved for generation",
                    "Not applicable",
                ],
            ),
        },
    )

    test_count = len(final_plan)

    reviewed_count = (
        final_plan["review_status"]
        .astype(str)
        .str.contains(
            "reviewed|approved",
            case=False,
            regex=True,
            na=False,
        )
        .sum()
        if "review_status" in final_plan.columns
        else 0
    )

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric("Test Sheets", test_count)
    metric2.metric("Reviewed / Approved", int(reviewed_count))
    metric3.metric("Selected Route", selected_route["route_name"])

    download_col1, download_col2, download_col3 = st.columns(3)

    with download_col1:
        excel_bytes = create_excel_bytes(
            plan=final_plan,
            route=selected_route,
            project=project,
        )

        st.download_button(
            "⬇️ Download Excel Test Register",
            data=excel_bytes,
            file_name=(
                f"{sheet_prefix}_"
                f"{selected_route['sector']}-{selected_route['section']}_"
                f"Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
        )

    with download_col2:
        st.download_button(
            "⬇️ Download Test Plan CSV",
            data=final_plan.to_csv(index=False).encode("utf-8"),
            file_name=(
                f"{sheet_prefix}_"
                f"{selected_route['sector']}-{selected_route['section']}_"
                f"Test_Plan.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with download_col3:
        try:
            docx_zip = create_docx_zip(
                plan=final_plan,
                project=project,
            )

            st.download_button(
                "⬇️ Download DOCX Test Sheets",
                data=docx_zip,
                file_name=(
                    f"{sheet_prefix}_"
                    f"{selected_route['sector']}-{selected_route['section']}_"
                    f"Test_Sheets.zip"
                ),
                mime="application/zip",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(
                f"DOCX generation failed: {exc}. "
                "Install python-docx using: pip install python-docx"
            )
