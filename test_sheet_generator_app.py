from __future__ import annotations

import io
import re
import textwrap
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


# ============================================================================
# CONFIGURATION
# ============================================================================

PROCEDURE_REFERENCE = "C1252-TAC-0274"
PROCEDURE_TITLE = "Trackside ATC Low-Speed Dynamic Test Procedure"
DEFAULT_TEST_SHEET_PREFIX = "ATC-TNC-TRA"

TEST_TYPES = {
    "LS01": {
        "id": "ATC-TNC-TRA-LS01",
        "name": "MTIB Initialization Test",
        "purpose": "Verify that ATC on-board can be localized and calibrated on an MTIB beacon.",
    },
    "LS02": {
        "id": "ATC-TNC-TRA-LS02",
        "name": "STIB Initialization Test",
        "purpose": "Verify that ATC provides accurate localization upon STIB.",
    },
    "LS03": {
        "id": "ATC-TNC-TRA-LS03",
        "name": "Spacing Stopping Point Test – Track Circuit",
        "purpose": "Verify correspondence between variants and invariants for a track-circuit stopping point.",
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "name": "Special Stopping Point Test – Signal Without Overlap",
        "purpose": "Verify correspondence between variants and invariants for a signal stopping point without overlap.",
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "name": "Complex Stopping Point Test – Signal With Overlap",
        "purpose": "Verify correspondence between variants and invariants for a signal stopping point with overlap.",
    },
    "LS06": {
        "id": "ATC-TNC-TRA-LS06",
        "name": "Stopping Point Test – Point",
        "purpose": "Verify correspondence between variants and invariants for a convergence/divergence point.",
    },
    "LS07": {
        "id": "ATC-TNC-TRA-LS07",
        "name": "Stopping Point Test – End of SACEM",
        "purpose": "Verify no ATC CM/AM movement is available beyond the end of SACEM.",
    },
    "LS08": {
        "id": "ATC-TNC-TRA-LS08",
        "name": "Station Stopping Point and Door Side Verification",
        "purpose": "Verify station stopping point, door enable window and correct door side.",
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "name": "PSD Operation Pre-Test",
        "purpose": "Verify ATC/PSD door-open and door-close command interface behaviour.",
    },
    "LS10": {
        "id": "ATC-TNC-TRA-LS10",
        "name": "Emergency Stop Zone (ESZ)",
        "purpose": "Verify no CM/AUTO movement is available in an active Emergency Stop Zone.",
    },
    "LS11": {
        "id": "ATC-TNC-TRA-LS11",
        "name": "Change of End (CoE)",
        "purpose": "Verify Change of End can be completed without delocalization or EB.",
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "name": "Temporary Speed Restriction (TSR)",
        "purpose": "Verify correct train behaviour when a TSR is applied.",
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "purpose": "Verify train information is received by ATC trackside through the return-channel loop.",
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "name": "End of Shunt Test",
        "purpose": "Verify end-of-shunt declaration and CM/AM recovery position.",
    },
}

NORMALISED_COLUMNS = [
    "route_name",
    "route_direction",
    "sector",
    "section",
    "branch",
    "subsection",
    "abscissa_m",
    "variant_number",
    "anticipated_variant_rank",
    "event_description",
    "event_type",
    "signal_name",
    "track_circuit",
    "point_name",
    "station_name",
    "door_side",
    "source_page",
    "source_evidence",
    "engineer_review_status",
    "engineer_remarks",
]


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class RouteDefinition:
    route_name: str
    direction: str
    sector: str
    section: str
    branches_to_run: str
    anticipated_branches: str
    entry_speed_kph: Optional[float] = None
    initialization_distance_m: Optional[float] = None
    source_page: Optional[int] = None


# ============================================================================
# STYLES
# ============================================================================

def render_test_sheet_generator_styles() -> None:
    css = """
    <style>
    .test-sheet-header {
        position: relative;
        overflow: hidden;
        background: linear-gradient(135deg, #102A43 0%, #163D5A 58%, #1C587D 100%);
        border-radius: 20px;
        padding: 28px 32px;
        margin-bottom: 22px;
        box-shadow: 0 10px 28px rgba(16,42,67,.22);
        color: white;
    }

    .test-sheet-header h1 {
        margin: 0;
        color: white !important;
        font-size: 30px;
        font-weight: 750;
    }

    .test-sheet-header p {
        margin: 8px 0 0;
        color: #D9EAF5 !important;
        font-size: 15px;
        line-height: 1.5;
    }

    .test-sheet-card {
        background: var(--surface, #FFFFFF);
        border: 1px solid var(--border, #DADCE0);
        border-radius: 16px;
        padding: 20px 22px;
        margin-bottom: 18px;
        box-shadow: 0 5px 18px rgba(16,42,67,.06);
    }

    .test-sheet-card-title {
        color: var(--navy, #202124);
        font-size: 18px;
        font-weight: 750;
        margin-bottom: 8px;
    }

    .test-sheet-card-description {
        color: var(--muted, #5F6368);
        font-size: 14px;
        line-height: 1.6;
    }

    .test-sheet-warning {
        border-left: 4px solid #E37400;
        background: #FEF7E0;
        color: #7A4F01;
        border-radius: 8px;
        padding: 12px 15px;
        margin: 12px 0;
        font-size: 14px;
    }

    .test-sheet-metric {
        background: var(--surface-secondary, #F8F9FA);
        border: 1px solid var(--border, #DADCE0);
        border-radius: 10px;
        padding: 14px;
        text-align: center;
    }

    .test-sheet-metric-value {
        color: var(--blue, #0064A6);
        font-size: 24px;
        font-weight: 750;
    }

    .test-sheet-metric-label {
        color: var(--muted, #5F6368);
        font-size: 12px;
        margin-top: 4px;
    }
    </style>
    """
    st.markdown(textwrap.dedent(css), unsafe_allow_html=True)


# ============================================================================
# FILE AND PDF UTILITIES
# ============================================================================

def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalise_branch(branch: str) -> str:
    """
    Converts branch notation to a standard format where possible.

    Examples:
        09-01-2-0 -> 09-01-2-0
        9-1-2-0   -> 09-01-2-0
    """
    text = safe_text(branch)
    match = re.search(
        r"(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})",
        text,
    )
    if not match:
        return text

    return "-".join(
        f"{int(part):02d}" if index < 2 else str(int(part))
        for index, part in enumerate(match.groups())
    )


def branch_tokens(text: str) -> List[str]:
    matches = re.findall(
        r"\b\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2}\b",
        safe_text(text),
    )
    return [normalise_branch(item) for item in matches]


def read_uploaded_tabular_file(uploaded_file: Any) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)

    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded_file)

    raise ValueError("Only CSV, XLSX and XLS files are supported.")


def extract_pdf_text(pdf_bytes: bytes, use_ocr: bool = False) -> List[str]:
    """
    Extract embedded PDF text using pypdf.

    OCR is optional. It requires:
      - pymupdf
      - pytesseract
      - Tesseract installed at operating-system level

    OCR is only attempted for pages with very little embedded text.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: pypdf. Install it with: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: List[str] = []

    for page in reader.pages:
        pages.append(page.extract_text() or "")

    if not use_ocr:
        return pages

    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError:
        st.warning(
            "OCR was selected but optional OCR packages are not installed. "
            "Using embedded PDF text only."
        )
        return pages

    document = fitz.open(stream=pdf_bytes, filetype="pdf")

    for index, page_text in enumerate(pages):
        if len(page_text.strip()) >= 100:
            continue

        page = document.load_page(index)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        ocr_text = pytesseract.image_to_string(image)

        if len(ocr_text.strip()) > len(page_text.strip()):
            pages[index] = ocr_text

    return pages


# ============================================================================
# APPENDIX J ROUTE EXTRACTION
# ============================================================================

def parse_routes_from_appendix_j(page_texts: List[str]) -> pd.DataFrame:
    """
    Parses the Appendix J transmission-time pages.

    Expected examples:
        1 - First route, to Up TCL : 09-01-0-0, ...
        - Anticipated branches to be run : 09-03-0-0, ...
        Entry speed : Ve = 135 km/h
        Initialisation distance at 22 km/h = 160 m
    """
    route_rows: List[Dict[str, Any]] = []

    route_pattern = re.compile(
        r"(?P<number>\d+)\s*-\s*"
        r"(?P<ordinal>First|Second|Third)\s+route"
        r"(?:,\s*(?:to|section branches run)\s*(?P<direction>Up|Dn)\s*TCL)?"
        r"\s*:\s*(?P<branches>.*?)(?="
        r"\n\s*-\s*Anticipated branches"
        r"|\n\s*Entry speed"
        r"|\n\s*\d+\s*-\s*(?:First|Second|Third)\s+route"
        r"|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for page_index, raw_text in enumerate(page_texts, start=1):
        compact = re.sub(r"[ \t]+", " ", raw_text)
        compact = re.sub(r"\n{2,}", "\n", compact)

        sector_match = re.search(
            r"SECTOR\s*(\d{1,2})\s*,?\s*SECTION\s*(\d{1,2})",
            compact,
            flags=re.IGNORECASE,
        )

        if not sector_match:
            continue

        sector = f"{int(sector_match.group(1)):02d}"
        section = f"{int(sector_match.group(2)):02d}"

        for match in route_pattern.finditer(compact):
            block_start = match.start()
            block_end = min(
                len(compact),
                match.end() + 2000,
            )
            route_block = compact[block_start:block_end]

            route_name = (
                f"{sector}-{section} "
                f"{match.group('ordinal').title()} Route"
            )

            branches = ", ".join(branch_tokens(match.group("branches")))

            anticipated_match = re.search(
                r"Anticipated branches to be run\s*:\s*(.*?)(?="
                r"\n\s*Entry speed"
                r"|\n\s*\d+\s*-\s*(?:First|Second|Third)\s+route"
                r"|$)",
                route_block,
                flags=re.IGNORECASE | re.DOTALL,
            )

            anticipated = (
                ", ".join(branch_tokens(anticipated_match.group(1)))
                if anticipated_match
                else ""
            )

            speed_match = re.search(
                r"Entry speed\s*:\s*Ve\s*=\s*(\d+(?:\.\d+)?)",
                route_block,
                flags=re.IGNORECASE,
            )

            init_match = re.search(
                r"Initiali[sz]ation distance at 22 km/h\s*=\s*(\d+(?:\.\d+)?)",
                route_block,
                flags=re.IGNORECASE,
            )

            route_rows.append(
                asdict(
                    RouteDefinition(
                        route_name=route_name,
                        direction=safe_text(match.group("direction")) or "Unknown",
                        sector=sector,
                        section=section,
                        branches_to_run=branches,
                        anticipated_branches=anticipated,
                        entry_speed_kph=(
                            float(speed_match.group(1))
                            if speed_match
                            else None
                        ),
                        initialization_distance_m=(
                            float(init_match.group(1))
                            if init_match
                            else None
                        ),
                        source_page=page_index,
                    )
                )
            )

    if not route_rows:
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

    routes = pd.DataFrame(route_rows)
    routes = routes[routes["branches_to_run"].str.len() > 0].copy()
    return routes.drop_duplicates().reset_index(drop=True)


# ============================================================================
# SINGULARITY / EVENT EXTRACTION
# ============================================================================

def identify_event_type(description: str) -> str:
    """
    Classifies an event based on its singularity/variant description.
    This is a proposal only. The engineer must review the result.
    """
    text = safe_text(description).upper()

    if "MTIB" in text or "MOVING TRAIN INITIALISATION" in text:
        return "LS01"

    if "STIB" in text or "STATIONARY TRAIN INITIALISATION" in text:
        return "LS02"

    if "END OF SHUNT" in text or "EXIT SACEM ROUTE" in text:
        return "LS14"

    if "CHANGE OF END" in text or "CHANGE ENDS" in text:
        return "LS11"

    if "EMERGENCY ZONE" in text or "EMERGENCY AREA" in text or " ESA " in f" {text} ":
        return "LS10"

    if "RETURN CHANNEL" in text:
        return "LS13"

    if "STATION" in text and "PSD" in text:
        return "LS08 / LS09"

    if "STATION" in text or "SSP DISTANCE" in text:
        return "LS08"

    if "POINT" in text or re.search(r"\b(RP|NP)\b", text):
        return "LS06"

    if "OVERLAP" in text or " O/L " in f" {text} ":
        return "LS05"

    if "SIGNAL" in text or "PEP" in text:
        return "LS04"

    if "T/C" in text or "TRACK CIRCUIT" in text:
        return "LS03"

    if "END SACEM" in text:
        return "LS07"

    return ""


def extract_event_candidates(page_texts: List[str]) -> pd.DataFrame:
    """
    Heuristic extraction from embedded/OCR text.

    This cannot guarantee 100% correct extraction from a graphical Appendix J
    table. The extracted information is intentionally presented for engineer
    review in the data editor.
    """
    rows: List[Dict[str, Any]] = []

    abscissa_pattern = re.compile(
        r"(?:Abs(?:cissa)?|K\.?P\.?|Installation\s*k\.?p\.?)"
        r"\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)",
        flags=re.IGNORECASE,
    )

    subsection_pattern = re.compile(
        r"(?:Sub[\s-]*section|Sub\s*Section)\s*[:=]?\s*"
        r"(\d{1,2}-\d{1,2}-\d{1,2})",
        flags=re.IGNORECASE,
    )

    variant_pattern = re.compile(
        r"(?:Associated\s+variant|Variant(?:\s+number)?)"
        r"\s*[:=]?\s*(\d+)",
        flags=re.IGNORECASE,
    )

    for page_no, page_text in enumerate(page_texts, start=1):
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in page_text.splitlines()
            if line.strip()
        ]

        current_sector = ""
        current_subsection = ""
        current_abscissa: Optional[float] = None

        for line_index, line in enumerate(lines):
            sector_match = re.search(
                r"Sector\s*[:=]?\s*(\d{1,2})",
                line,
                flags=re.IGNORECASE,
            )
            if sector_match:
                current_sector = f"{int(sector_match.group(1)):02d}"

            subsection_match = subsection_pattern.search(line)
            if subsection_match:
                current_subsection = subsection_match.group(1)

            abs_match = abscissa_pattern.search(line)
            if abs_match:
                current_abscissa = float(
                    abs_match.group(1).replace(",", ".")
                )

            description_window = " ".join(
                lines[max(0, line_index - 1): min(len(lines), line_index + 2)]
            )

            event_type = identify_event_type(description_window)
            if not event_type:
                continue

            variant_match = variant_pattern.search(description_window)

            branch_number = ""
            branch_match = re.search(
                r"Branch\s*(?:number)?\s*[:=]?\s*(\d+)",
                description_window,
                flags=re.IGNORECASE,
            )
            if branch_match:
                branch_number = branch_match.group(1)

            rows.append(
                {
                    "route_name": "",
                    "route_direction": "",
                    "sector": current_sector,
                    "section": (
                        current_subsection.split("-")[1]
                        if current_subsection.count("-") >= 2
                        else ""
                    ),
                    "branch": "",
                    "subsection": current_subsection,
                    "abscissa_m": current_abscissa,
                    "variant_number": (
                        variant_match.group(1)
                        if variant_match
                        else ""
                    ),
                    "anticipated_variant_rank": "",
                    "event_description": description_window,
                    "event_type": event_type,
                    "signal_name": "",
                    "track_circuit": "",
                    "point_name": "",
                    "station_name": "",
                    "door_side": "",
                    "source_page": page_no,
                    "source_evidence": line,
                    "engineer_review_status": "To be reviewed",
                    "engineer_remarks": "",
                    "branch_number": branch_number,
                }
            )

    if not rows:
        return pd.DataFrame(columns=NORMALISED_COLUMNS + ["branch_number"])

    events = pd.DataFrame(rows)
    events = events.drop_duplicates(
        subset=[
            "source_page",
            "event_type",
            "subsection",
            "event_description",
        ]
    ).reset_index(drop=True)

    return events


# ============================================================================
# ROUTE AND TEST PLANNING
# ============================================================================

def attach_route_to_events(
    routes: pd.DataFrame,
    events: pd.DataFrame,
) -> pd.DataFrame:
    """
    Assigns a route to events by sector/section and branch/subsection matching.
    """
    if events.empty:
        return events.copy()

    result = events.copy()

    for index, event in result.iterrows():
        if safe_text(event.get("route_name")):
            continue

        sector = safe_text(event.get("sector"))
        section = safe_text(event.get("section"))
        subsection = safe_text(event.get("subsection"))

        candidates = routes[
            (routes["sector"].astype(str) == sector)
            & (routes["section"].astype(str) == section)
        ]

        if candidates.empty:
            continue

        selected = candidates.iloc[0]

        for _, route in candidates.iterrows():
            branch_list = branch_tokens(route["branches_to_run"])
            if subsection and any(
                subsection in branch for branch in branch_list
            ):
                selected = route
                break

        result.at[index, "route_name"] = selected["route_name"]
        result.at[index, "route_direction"] = selected["direction"]

    return result


def get_participate_subsection(row: pd.Series) -> str:
    subsection = safe_text(row.get("subsection"))
    if subsection:
        return subsection

    branch = safe_text(row.get("branch"))
    parts = branch.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:3])

    return ""


def get_anticipate_subsections(route: pd.Series) -> str:
    anticipated = branch_tokens(route.get("anticipated_branches", ""))
    subsections: List[str] = []

    for branch in anticipated:
        parts = branch.split("-")
        if len(parts) >= 3:
            subsection = "-".join(parts[:3])
            if subsection not in subsections:
                subsections.append(subsection)

    return ", ".join(subsections)


def calculate_test_plan(
    selected_route: pd.Series,
    events: pd.DataFrame,
    include_tsr: bool,
    include_end_sacem: bool,
) -> pd.DataFrame:
    """
    Produces test-sheet rows applicable to the selected route.
    """
    route_name = selected_route["route_name"]
    sector = selected_route["sector"]
    section = selected_route["section"]

    route_branches = branch_tokens(selected_route["branches_to_run"])
    anticipated_branches = branch_tokens(
        selected_route["anticipated_branches"]
    )

    applicable = events[
        (events["sector"].astype(str) == str(sector))
        & (events["section"].astype(str) == str(section))
    ].copy()

    if applicable.empty:
        applicable = pd.DataFrame(columns=events.columns)

    selected_rows: List[Dict[str, Any]] = []

    for _, event in applicable.iterrows():
        event_branch = normalise_branch(event.get("branch", ""))
        event_subsection = safe_text(event.get("subsection"))

        branch_match = (
            not event_branch
            or event_branch in route_branches
            or event_branch in anticipated_branches
        )

        subsection_match = (
            not event_subsection
            or any(event_subsection in branch for branch in route_branches)
            or any(event_subsection in branch for branch in anticipated_branches)
        )

        if not branch_match and not subsection_match:
            continue

        event_type = safe_text(event.get("event_type"))
        if not event_type:
            continue

        test_codes = [
            code.strip()
            for code in event_type.split("/")
            if code.strip()
        ]

        for test_code in test_codes:
            if test_code not in TEST_TYPES:
                continue

            record = event.to_dict()
            record["test_code"] = test_code
            record["test_id"] = TEST_TYPES[test_code]["id"]
            record["test_name"] = TEST_TYPES[test_code]["name"]
            record["test_purpose"] = TEST_TYPES[test_code]["purpose"]
            record["route_name"] = route_name
            record["route_direction"] = selected_route["direction"]
            record["participate_subsection"] = get_participate_subsection(event)
            record["anticipate_subsections"] = get_anticipate_subsections(
                selected_route
            )
            record["route_branches"] = ", ".join(route_branches)
            record["anticipated_branches"] = ", ".join(anticipated_branches)
            record["initialization_distance_m"] = selected_route.get(
                "initialization_distance_m",
                None,
            )
            record["entry_speed_kph"] = selected_route.get(
                "entry_speed_kph",
                None,
            )
            selected_rows.append(record)

    if include_tsr:
        selected_rows.append(
            {
                "test_code": "LS12",
                "test_id": TEST_TYPES["LS12"]["id"],
                "test_name": TEST_TYPES["LS12"]["name"],
                "test_purpose": TEST_TYPES["LS12"]["purpose"],
                "route_name": route_name,
                "route_direction": selected_route["direction"],
                "sector": sector,
                "section": section,
                "branch": ", ".join(route_branches),
                "subsection": "",
                "abscissa_m": None,
                "variant_number": "",
                "anticipated_variant_rank": "",
                "participate_subsection": "",
                "anticipate_subsections": get_anticipate_subsections(
                    selected_route
                ),
                "event_description": (
                    "Sample TSR test: select one track circuit per ATC section."
                ),
                "source_page": selected_route.get("source_page", ""),
                "engineer_review_status": "To be reviewed",
                "engineer_remarks": (
                    "Engineer to nominate track circuit and TSR value below 22 km/h."
                ),
            }
        )

    if include_end_sacem:
        selected_rows.append(
            {
                "test_code": "LS07",
                "test_id": TEST_TYPES["LS07"]["id"],
                "test_name": TEST_TYPES["LS07"]["name"],
                "test_purpose": TEST_TYPES["LS07"]["purpose"],
                "route_name": route_name,
                "route_direction": selected_route["direction"],
                "sector": sector,
                "section": section,
                "branch": ", ".join(route_branches),
                "subsection": "",
                "abscissa_m": None,
                "variant_number": "",
                "anticipated_variant_rank": "",
                "participate_subsection": "",
                "anticipate_subsections": "",
                "event_description": "End of SACEM test point to be confirmed.",
                "source_page": selected_route.get("source_page", ""),
                "engineer_review_status": "To be reviewed",
                "engineer_remarks": (
                    "Confirm exact End of SACEM singularity from approved subdivision."
                ),
            }
        )

    plan = pd.DataFrame(selected_rows)

    if plan.empty:
        return plan

    plan = plan.drop_duplicates(
        subset=[
            "test_code",
            "route_name",
            "subsection",
            "abscissa_m",
            "event_description",
        ]
    ).reset_index(drop=True)

    return plan


# ============================================================================
# EXPORT FUNCTIONS
# ============================================================================

def dataframe_to_excel_bytes(
    test_plan: pd.DataFrame,
    route: pd.Series,
    project_info: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary = pd.DataFrame(
            [
                ["Procedure Reference", PROCEDURE_REFERENCE],
                ["Procedure Title", PROCEDURE_TITLE],
                ["Generated Date", str(date.today())],
                ["Project", project_info.get("project", "")],
                ["Trackside Version", project_info.get("trackside_version", "")],
                ["ATP Version", project_info.get("atp_version", "")],
                ["ATO Version", project_info.get("ato_version", "")],
                ["Selected Route", route.get("route_name", "")],
                ["Direction", route.get("direction", "")],
                ["Sector", route.get("sector", "")],
                ["Section", route.get("section", "")],
                ["Branches to Run", route.get("branches_to_run", "")],
                ["Anticipated Branches", route.get("anticipated_branches", "")],
            ],
            columns=["Field", "Value"],
        )
        summary.to_excel(writer, sheet_name="Cover", index=False)

        test_plan.to_excel(writer, sheet_name="Test Register", index=False)

        workbook = writer.book

        for sheet_name in workbook.sheetnames:
            worksheet = workbook[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                max_length = max(
                    len(safe_text(cell.value))
                    for cell in column_cells
                )
                width = min(max(max_length + 2, 12), 55)
                worksheet.column_dimensions[
                    column_cells[0].column_letter
                ].width = width

    return output.getvalue()


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
) -> bytes:
    """
    Produces one professional Word test sheet per selected test item.
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: python-docx. "
            "Install it with: pip install python-docx"
        ) from exc

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.45)
    section.bottom_margin = Inches(0.45)
    section.left_margin = Inches(0.5)
    section.right_margin = Inches(0.5)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("ALSTOM\n")
    run.bold = True
    run.font.size = Pt(16)

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(PROCEDURE_TITLE)
    run.bold = True
    run.font.size = Pt(12)

    document.add_paragraph()

    info_table = document.add_table(rows=0, cols=2)
    info_table.style = "Table Grid"

    header_fields = [
        ("Procedure Reference", PROCEDURE_REFERENCE),
        ("Test ID", safe_text(row.get("test_id"))),
        ("Test Name", safe_text(row.get("test_name"))),
        ("Test Sheet No.", project_info.get("test_sheet_no", "")),
        ("Project", project_info.get("project", "")),
        ("Trackside Version", project_info.get("trackside_version", "")),
        ("ATP Version", project_info.get("atp_version", "")),
        ("ATO Version", project_info.get("ato_version", "")),
        ("Route", safe_text(row.get("route_name"))),
        ("Direction", safe_text(row.get("route_direction"))),
        ("Sector", safe_text(row.get("sector"))),
        ("Section", safe_text(row.get("section"))),
        ("Branch Number", safe_text(row.get("branch"))),
        ("Sub-section", safe_text(row.get("subsection"))),
        ("Abscissa", f"{safe_text(row.get('abscissa_m'))} m"),
        ("Variant Number", safe_text(row.get("variant_number"))),
        (
            "Anticipated Variant Rank",
            safe_text(row.get("anticipated_variant_rank")),
        ),
        (
            "Participate Sub-section",
            safe_text(row.get("participate_subsection")),
        ),
        (
            "Anticipate Sub-section(s)",
            safe_text(row.get("anticipate_subsections")),
        ),
    ]

    for key, value in header_fields:
        cells = info_table.add_row().cells
        cells[0].text = key
        cells[1].text = value

    document.add_paragraph()
    purpose_heading = document.add_paragraph()
    purpose_heading.add_run("Purpose of Test").bold = True
    document.add_paragraph(safe_text(row.get("test_purpose")))

    document.add_paragraph()
    test_heading = document.add_paragraph()
    test_heading.add_run("Test Record").bold = True

    test_table = document.add_table(rows=1, cols=4)
    test_table.style = "Table Grid"

    headers = [
        "Check / Record",
        "Expected Value / Requirement",
        "Actual Result",
        "Pass / Fail",
    ]

    for index, header in enumerate(headers):
        test_table.rows[0].cells[index].text = header

    standard_rows = [
        (
            "Localization status",
            "Total Loc / as required by procedure",
            "",
            "",
        ),
        (
            "Available mode",
            "CM/AM available where required",
            "",
            "",
        ),
        (
            "OMAP target speed",
            "Record actual observed value",
            "",
            "",
        ),
        (
            "Speedometer target speed",
            "Record actual observed value",
            "",
            "",
        ),
        (
            "Variant status",
            "Restrictive/permissive as required by procedure",
            "",
            "",
        ),
        (
            "Abscissa / target-distance check",
            "Apply procedure tolerance: ±0.75 m, or ±8.25 m for ESZ",
            "",
            "",
        ),
        (
            "OMAP / log evidence",
            "Attach or reference supporting evidence",
            "",
            "",
        ),
    ]

    for values in standard_rows:
        cells = test_table.add_row().cells
        for index, value in enumerate(values):
            cells[index].text = value

    document.add_paragraph()
    remarks_heading = document.add_paragraph()
    remarks_heading.add_run("Engineering Remarks / Deviations").bold = True
    document.add_paragraph(
        safe_text(row.get("engineer_remarks"))
        or "No remarks recorded."
    )

    document.add_paragraph()
    acceptance = document.add_paragraph()
    acceptance.add_run("Acceptance Statement: ").bold = True
    acceptance.add_run(
        "The test covered by this Test Sheet is considered: "
        "☐ Passed    ☐ Failed    ☐ Punchlist Raised"
    )

    document.add_paragraph()
    sign_table = document.add_table(rows=3, cols=3)
    sign_table.style = "Table Grid"

    sign_headers = ["Role", "Name / Signature", "Date"]
    for index, header in enumerate(sign_headers):
        sign_table.rows[0].cells[index].text = header

    for role in ["Alstom Test Engineer", "MTR Witness"]:
        cells = sign_table.add_row().cells
        cells[0].text = role
        cells[1].text = ""
        cells[2].text = ""

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def create_docx_zip(
    test_plan: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (_, row) in enumerate(test_plan.iterrows(), start=1):
            test_code = safe_text(row.get("test_code"))
            subsection = safe_text(row.get("subsection")) or "General"
            abscissa = safe_text(row.get("abscissa_m")) or "NA"

            filename = (
                f"{index:03d}_{test_code}_{subsection}_{abscissa}m.docx"
            )
            filename = re.sub(r'[\\/:*?"<>|]', "_", filename)

            archive.writestr(
                filename,
                create_docx_test_sheet(row, project_info),
            )

    return zip_buffer.getvalue()


def normalised_template_bytes() -> bytes:
    example = pd.DataFrame(
        [
            {
                "route_name": "09-01 First Route",
                "route_direction": "Up",
                "sector": "09",
                "section": "01",
                "branch": "09-01-1-0",
                "subsection": "09-01-1",
                "abscissa_m": 1565.5,
                "variant_number": "10",
                "anticipated_variant_rank": "0",
                "event_description": "TUC KG T/C",
                "event_type": "LS03",
                "signal_name": "",
                "track_circuit": "TUC KG T/C",
                "point_name": "",
                "station_name": "",
                "door_side": "",
                "source_page": 1,
                "source_evidence": "Approved Appendix J source evidence",
                "engineer_review_status": "Reviewed",
                "engineer_remarks": "",
            }
        ],
        columns=NORMALISED_COLUMNS,
    )

    return example.to_csv(index=False).encode("utf-8")


# ============================================================================
# STREAMLIT PAGE
# ============================================================================

def render_test_sheet_generator_app() -> None:
    render_test_sheet_generator_styles()

    st.markdown(
        """
        <div class="test-sheet-header">
            <h1>ATC Low-Speed Test Sheet Generator</h1>
            <p>
                Route-based generation of C1252-TAC-0274 Trackside ATC
                Low-Speed Dynamic Test Sheets using Appendix J subdivision data.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="test-sheet-warning">
            <b>Engineering control requirement:</b> generated tests are proposed
            from Appendix J extraction and must be reviewed by the responsible
            ATC engineer before issue or execution. OCR-derived values must not
            be treated as approved design data without verification against the
            approved controlled document.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ----------------------------------------------------------------------
    # PROJECT CONFIGURATION
    # ----------------------------------------------------------------------

    with st.expander("Project and document configuration", expanded=True):
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            project = st.text_input(
                "Project / Contract",
                value="Contract 1252A – Signalling Works",
            )

        with col2:
            trackside_version = st.text_input(
                "Trackside Version",
                value="",
            )

        with col3:
            atp_version = st.text_input(
                "ATP Version",
                value="",
            )

        with col4:
            ato_version = st.text_input(
                "ATO Version",
                value="",
            )

        test_sheet_no = st.text_input(
            "Test Sheet Number Prefix",
            value="C1252-TAC-0274",
        )

    project_info = {
        "project": project,
        "trackside_version": trackside_version,
        "atp_version": atp_version,
        "ato_version": ato_version,
        "test_sheet_no": test_sheet_no,
    }

    # ----------------------------------------------------------------------
    # INPUTS
    # ----------------------------------------------------------------------

    st.markdown(
        """
        <div class="test-sheet-card">
            <div class="test-sheet-card-title">1. Appendix J and reviewed data input</div>
            <div class="test-sheet-card-description">
                Upload Appendix J PDF. The generator extracts transmission-route
                definitions automatically. For graphical singularity sheets,
                upload a reviewed CSV/XLSX normalised register if required.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns(2)

    with left:
        appendix_j_file = st.file_uploader(
            "Upload Appendix J PDF",
            type=["pdf"],
            key="appendix_j_pdf",
        )

        use_ocr = st.checkbox(
            "Use OCR for image-only PDF pages",
            value=False,
            help=(
                "Requires pymupdf, pytesseract and a system installation "
                "of Tesseract OCR."
            ),
        )

    with right:
        reviewed_register_file = st.file_uploader(
            "Upload reviewed singularity/event register (recommended)",
            type=["csv", "xlsx", "xls"],
            key="reviewed_event_register",
            help=(
                "Use this when Appendix J tables are image-only or when "
                "engineers have completed the singularity extraction."
            ),
        )

        st.download_button(
            "Download Normalised Event Register Template",
            data=normalised_template_bytes(),
            file_name="appendix_j_normalised_event_register_template.csv",
            mime="text/csv",
        )

    routes = pd.DataFrame()
    automatic_events = pd.DataFrame()
    reviewed_events = pd.DataFrame()

    if appendix_j_file is not None:
        try:
            pdf_bytes = appendix_j_file.getvalue()

            with st.spinner("Extracting Appendix J text and route definitions..."):
                page_texts = extract_pdf_text(pdf_bytes, use_ocr=use_ocr)
                routes = parse_routes_from_appendix_j(page_texts)
                automatic_events = extract_event_candidates(page_texts)

            automatic_events = attach_route_to_events(
                routes,
                automatic_events,
            )

            st.success(
                f"Appendix J processed: {len(page_texts)} page(s), "
                f"{len(routes)} route definition(s), "
                f"{len(automatic_events)} proposed event(s)."
            )

        except Exception as exc:
            st.error(f"Unable to process Appendix J PDF: {exc}")

    if reviewed_register_file is not None:
        try:
            reviewed_events = read_uploaded_tabular_file(reviewed_register_file)

            for column in NORMALISED_COLUMNS:
                if column not in reviewed_events.columns:
                    reviewed_events[column] = ""

            reviewed_events = reviewed_events[
                NORMALISED_COLUMNS
            ].copy()

            st.success(
                f"Reviewed event register loaded: {len(reviewed_events)} row(s)."
            )

        except Exception as exc:
            st.error(f"Unable to read reviewed register: {exc}")

    # ----------------------------------------------------------------------
    # SELECT SOURCE
    # ----------------------------------------------------------------------

    if not reviewed_events.empty:
        event_source = reviewed_events.copy()
        event_source_name = "Reviewed register"
    else:
        event_source = automatic_events.copy()
        event_source_name = "Appendix J automatic extraction"

    if routes.empty:
        st.info(
            "Upload Appendix J PDF to extract routes. "
            "The supported Appendix J transmission pages include headings such as "
            "'First route, to Up TCL' and 'Anticipated branches to be run'."
        )
        return

    # ----------------------------------------------------------------------
    # EXTRACTION REVIEW
    # ----------------------------------------------------------------------

    st.markdown(
        """
        <div class="test-sheet-card">
            <div class="test-sheet-card-title">2. Engineering extraction review</div>
            <div class="test-sheet-card-description">
                Confirm or correct the singularity information before generating
                test sheets. The reviewed register overrides OCR/automatic
                extraction when uploaded.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("Routes extracted", len(routes))
    metric_2.metric("Events available", len(event_source))
    metric_3.metric("Current source", event_source_name)

    with st.expander("View extracted route definitions", expanded=False):
        st.dataframe(
            routes,
            use_container_width=True,
            hide_index=True,
        )

    if event_source.empty:
        st.warning(
            "No singularity events were extracted automatically. "
            "Use the downloadable normalised event-register template, complete "
            "it from the approved Appendix J data, and upload it."
        )
    else:
        with st.expander(
            "Review extracted singularity/event information",
            expanded=False,
        ):
            editable_events = st.data_editor(
                event_source,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="event_register_editor",
            )
            event_source = editable_events.copy()

    # ----------------------------------------------------------------------
    # ROUTE SELECTION
    # ----------------------------------------------------------------------

    st.markdown(
        """
        <div class="test-sheet-card">
            <div class="test-sheet-card-title">3. Select route and test scope</div>
            <div class="test-sheet-card-description">
                Select the operational route. The generator identifies events in
                the route branches and anticipated branches, then proposes the
                applicable low-speed tests.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    route_display_options = [
        (
            f"{row['route_name']} | {row['direction']} | "
            f"Branches: {row['branches_to_run']}"
        )
        for _, row in routes.iterrows()
    ]

    selected_route_display = st.selectbox(
        "Select route",
        options=route_display_options,
        key="selected_test_route",
    )

    selected_route_index = route_display_options.index(
        selected_route_display
    )
    selected_route = routes.iloc[selected_route_index]

    route_col1, route_col2, route_col3, route_col4 = st.columns(4)
    route_col1.metric("Sector", selected_route["sector"])
    route_col2.metric("Section", selected_route["section"])
    route_col3.metric(
        "Entry Speed",
        f"{selected_route['entry_speed_kph']} km/h"
        if pd.notna(selected_route["entry_speed_kph"])
        else "Not extracted",
    )
    route_col4.metric(
        "Initialisation Distance",
        f"{selected_route['initialization_distance_m']} m"
        if pd.notna(selected_route["initialization_distance_m"])
        else "Not extracted",
    )

    with st.expander("Selected route design information", expanded=True):
        st.write(
            "**Branches to run:**",
            selected_route["branches_to_run"],
        )
        st.write(
            "**Anticipated branches to run:**",
            selected_route["anticipated_branches"],
        )
        st.write(
            "**Participate logic:** the singularity sub-section/branch is used "
            "as the participate location."
        )
        st.write(
            "**Anticipate logic:** the route's Appendix J anticipated branches "
            "are converted to anticipated sub-sections."
        )

    scope_col1, scope_col2, scope_col3 = st.columns(3)

    with scope_col1:
        include_tsr = st.checkbox(
            "Include LS12 TSR sample test",
            value=True,
        )

    with scope_col2:
        include_end_sacem = st.checkbox(
            "Include LS07 End of SACEM placeholder",
            value=False,
        )

    with scope_col3:
        include_manual = st.checkbox(
            "Add manual test rows after generation",
            value=True,
        )

    # ----------------------------------------------------------------------
    # PLAN CREATION
    # ----------------------------------------------------------------------

    if st.button("Generate Proposed Test Plan", key="generate_test_plan"):
        test_plan = calculate_test_plan(
            selected_route,
            event_source,
            include_tsr=include_tsr,
            include_end_sacem=include_end_sacem,
        )

        if include_manual:
            manual_row = {
                "test_code": "",
                "test_id": "",
                "test_name": "Manual engineering test item",
                "test_purpose": "",
                "route_name": selected_route["route_name"],
                "route_direction": selected_route["direction"],
                "sector": selected_route["sector"],
                "section": selected_route["section"],
                "branch": "",
                "subsection": "",
                "abscissa_m": None,
                "variant_number": "",
                "anticipated_variant_rank": "",
                "participate_subsection": "",
                "anticipate_subsections": get_anticipate_subsections(
                    selected_route
                ),
                "event_description": "",
                "engineer_review_status": "To be reviewed",
                "engineer_remarks": "",
            }
            test_plan = pd.concat(
                [test_plan, pd.DataFrame([manual_row])],
                ignore_index=True,
            )

        st.session_state["generated_test_plan"] = test_plan
        st.session_state["generated_route"] = selected_route.to_dict()

    # ----------------------------------------------------------------------
    # PLAN REVIEW AND EXPORT
    # ----------------------------------------------------------------------

    generated_test_plan = st.session_state.get(
        "generated_test_plan",
        pd.DataFrame(),
    )

    if generated_test_plan.empty:
        return

    st.markdown(
        """
        <div class="test-sheet-card">
            <div class="test-sheet-card-title">4. Test-sheet review and generation</div>
            <div class="test-sheet-card-description">
                Review the proposed test sheet register. Correct branch numbers,
                abscissae, variant numbers and anticipated ranks before export.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    reviewed_plan = st.data_editor(
        generated_test_plan,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="generated_test_plan_editor",
    )

    test_count = len(reviewed_plan)
    review_count = (
        reviewed_plan["engineer_review_status"]
        .astype(str)
        .str.contains("reviewed", case=False, na=False)
        .sum()
        if "engineer_review_status" in reviewed_plan.columns
        else 0
    )

    result_col1, result_col2, result_col3 = st.columns(3)
    result_col1.metric("Proposed test sheets", test_count)
    result_col2.metric("Marked reviewed", int(review_count))
    result_col3.metric(
        "Route",
        safe_text(selected_route["route_name"]),
    )

    st.markdown(
        """
        <div class="test-sheet-warning">
            Before issue, verify the approved Appendix J source, selected route,
            participation/anticipation sub-sections, singularity abscissa,
            branch number, variant number and expected restrictive/permissive
            behaviour.
        </div>
        """,
        unsafe_allow_html=True,
    )

    export_col1, export_col2, export_col3 = st.columns(3)

    with export_col1:
        excel_bytes = dataframe_to_excel_bytes(
            reviewed_plan,
            selected_route,
            project_info,
        )

        st.download_button(
            "Download Excel Test Register",
            data=excel_bytes,
            file_name=(
                f"{test_sheet_no}_"
                f"{selected_route['sector']}-{selected_route['section']}_"
                f"Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )

    with export_col2:
        st.download_button(
            "Download Reviewed Test Plan CSV",
            data=reviewed_plan.to_csv(index=False).encode("utf-8"),
            file_name=(
                f"{test_sheet_no}_"
                f"{selected_route['sector']}-{selected_route['section']}_"
                f"Test_Plan.csv"
            ),
            mime="text/csv",
        )

    with export_col3:
        try:
            docx_zip = create_docx_zip(
                reviewed_plan,
                project_info,
            )

            st.download_button(
                "Download Individual DOCX Test Sheets",
                data=docx_zip,
                file_name=(
                    f"{test_sheet_no}_"
                    f"{selected_route['sector']}-{selected_route['section']}_"
                    f"Test_Sheets.zip"
                ),
                mime="application/zip",
            )
        except Exception as exc:
            st.warning(
                "DOCX generation unavailable. "
                f"Install python-docx. Details: {exc}"
            )
