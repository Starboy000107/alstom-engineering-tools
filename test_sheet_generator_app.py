from __future__ import annotations
import streamlit.components.v1 as components

import io
import re
import zipfile
from collections import defaultdict, deque
from datetime import date
from typing import Any, Dict, List, Set, Tuple

import pandas as pd
import streamlit as st


# =============================================================================
# FIXED LOW-SPEED PROCEDURE DEFINITIONS
# =============================================================================
# These are based on C1252-TAC-0274.
# Project routes, branches, abscissae, variants and stations are NOT hardcoded.

PROCEDURE_REFERENCE = "C1252-TAC-0274"
PROCEDURE_TITLE = "Trackside ATC Low-Speed Dynamic Test Procedure"

TEST_TYPES: Dict[str, Dict[str, Any]] = {
    "LS01": {
        "id": "ATC-TNC-TRA-LS01",
        "name": "MTIB Initialization Test",
        "icon": "📡",
        "purpose": (
            "Verify that ATC on-board can be localized and calibrated "
            "on an MTIB beacon."
        ),
    },
    "LS02": {
        "id": "ATC-TNC-TRA-LS02",
        "name": "STIB Initialization Test",
        "icon": "📍",
        "purpose": (
            "Verify that ATC provides accurate localization upon STIB."
        ),
    },
    "LS03": {
        "id": "ATC-TNC-TRA-LS03",
        "name": "Spacing Stopping Point Test – Track Circuit",
        "icon": "🚦",
        "purpose": (
            "Verify correspondence between variants and invariants "
            "for a track-circuit stopping point."
        ),
    },
    "LS04": {
        "id": "ATC-TNC-TRA-LS04",
        "name": "Special Stopping Point Test – Signal Without Overlap",
        "icon": "🔴",
        "purpose": (
            "Verify correspondence between variants and invariants "
            "for a signal stopping point without overlap."
        ),
    },
    "LS05": {
        "id": "ATC-TNC-TRA-LS05",
        "name": "Complex Stopping Point Test – Signal With Overlap",
        "icon": "🔴",
        "purpose": (
            "Verify correspondence between variants and invariants "
            "for a signal stopping point with overlap."
        ),
    },
    "LS06": {
        "id": "ATC-TNC-TRA-LS06",
        "name": "Stopping Point Test – Point",
        "icon": "🔀",
        "purpose": (
            "Verify correspondence between variants and invariants "
            "for convergence or divergence points."
        ),
    },
    "LS07": {
        "id": "ATC-TNC-TRA-LS07",
        "name": "Stopping Point Test – End of SACEM",
        "icon": "⛔",
        "purpose": (
            "Verify no further CM/AM movement is available beyond "
            "the End of SACEM."
        ),
    },
    "LS08": {
        "id": "ATC-TNC-TRA-LS08",
        "name": "Station Stopping Point and Door Side Verification",
        "icon": "🚉",
        "purpose": (
            "Verify station stopping position, door enable window "
            "and correct door side."
        ),
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "name": "PSD Operation Pre-Test",
        "icon": "🚪",
        "purpose": (
            "Verify ATC / PSD door opening and closing interface behaviour."
        ),
    },
    "LS10": {
        "id": "ATC-TNC-TRA-LS10",
        "name": "Emergency Stop Zone Test",
        "icon": "⚠️",
        "purpose": (
            "Verify CM/AUTO is not available inside an active "
            "Emergency Stop Zone."
        ),
    },
    "LS11": {
        "id": "ATC-TNC-TRA-LS11",
        "name": "Change of End Test",
        "icon": "🔄",
        "purpose": (
            "Verify Change of End can be completed without "
            "delocalization or Emergency Brake."
        ),
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "name": "Temporary Speed Restriction Test",
        "icon": "🚧",
        "purpose": (
            "Verify correct train behaviour after a Temporary "
            "Speed Restriction is applied."
        ),
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "icon": "📶",
        "purpose": (
            "Verify train information is received through the "
            "return-channel loop."
        ),
    },
    "LS14": {
        "id": "ATC-TNC-TRA-LS14",
        "name": "End of Shunt Test",
        "icon": "🚂",
        "purpose": (
            "Verify End of Shunt declaration and CM/AM recovery position."
        ),
    },
}


# =============================================================================
# UI STYLES
# =============================================================================

def render_test_sheet_generator_styles() -> None:
    st.markdown(
        """
        <style>
        .tsg-hero {
            background: linear-gradient(
                135deg,
                #102A43 0%,
                #164E72 70%,
                #E52B1E 150%
            );
            padding: 28px 34px;
            border-radius: 20px;
            margin-bottom: 18px;
            color: white;
            box-shadow: 0 10px 28px rgba(16,42,67,.22);
        }

        .tsg-hero h1 {
            color: white !important;
            margin: 0 !important;
            font-size: 30px !important;
        }

        .tsg-hero p {
            color: #E6F1F7 !important;
            margin: 10px 0 0 !important;
            font-size: 15px;
        }

        .tsg-card {
            background: var(--surface, #FFFFFF);
            border: 1px solid #D9E2EC;
            border-radius: 16px;
            padding: 18px 20px;
            margin: 12px 0;
        }

        .tsg-warning {
            background: #FFF7E8;
            border: 1px solid #F5C16C;
            border-left: 5px solid #E37400;
            border-radius: 12px;
            padding: 14px 16px;
            margin: 12px 0;
        }

        .tsg-info {
            background: #EDF7FF;
            border: 1px solid #B8DCF4;
            border-left: 5px solid #0064A6;
            border-radius: 12px;
            padding: 14px 16px;
            margin: 12px 0;
        }

        div.stButton > button {
            border-radius: 12px;
            min-height: 48px;
            font-weight: 650;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return str(value).strip()


def normalise_branch(value: Any) -> str:
    """
    Examples:
        0-1-1-0  -> 00-01-1-0
        8-4-3-0  -> 08-04-3-0
        09-01-2-0 -> 09-01-2-0
    """
    value = safe_text(value)

    match = re.search(
        r"(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})",
        value,
    )

    if not match:
        return value

    sector, section, subsection, branch = match.groups()

    return (
        f"{int(sector):02d}-"
        f"{int(section):02d}-"
        f"{int(subsection)}-"
        f"{int(branch)}"
    )


def branch_tokens(value: Any) -> List[str]:
    items = re.findall(
        r"\b\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2}\b",
        safe_text(value),
    )

    output: List[str] = []

    for item in items:
        branch = normalise_branch(item)

        if branch not in output:
            output.append(branch)

    return output


def branch_sector(branch: str) -> str:
    values = safe_text(branch).split("-")
    return values[0] if len(values) >= 1 else ""


def branch_section(branch: str) -> str:
    values = safe_text(branch).split("-")
    return values[1] if len(values) >= 2 else ""


def branch_subsection(branch: str) -> str:
    values = safe_text(branch).split("-")

    if len(values) >= 3:
        return "-".join(values[:3])

    return ""


# =============================================================================
# PROCEDURE EVENT IDENTIFICATION
# =============================================================================

def identify_event_type(text: str) -> str:
    value = safe_text(text).upper()

    if "MTIB" in value or "MOVING TRAIN INITIAL" in value:
        return "LS01"

    if "STIB" in value or "STATIONARY TRAIN INITIAL" in value:
        return "LS02"

    if "END OF SHUNT" in value or "EXIT SACEM ROUTE" in value:
        return "LS14"

    if "CHANGE OF END" in value or "CHANGE ENDS" in value:
        return "LS11"

    if (
        "EMERGENCY STOP ZONE" in value
        or "EMERGENCY ZONE" in value
        or " ESZ " in f" {value} "
    ):
        return "LS10"

    if "RETURN CHANNEL" in value or "R/C LOOP" in value:
        return "LS13"

    if "TEMPORARY SPEED RESTRICTION" in value or " TSR " in f" {value} ":
        return "LS12"

    if "PSD" in value:
        return "LS09"

    if (
        "STATION STOP" in value
        or "DOOR SIDE" in value
        or "PLATFORM STOP" in value
    ):
        return "LS08"

    if "END OF SACEM" in value:
        return "LS07"

    if "OVERLAP" in value or " O/L " in f" {value} ":
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
            "Missing package: pypdf. "
            "Install it with: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))

    return [
        page.extract_text() or ""
        for page in reader.pages
    ]


# =============================================================================
# GRAPH EXTRACTION
# =============================================================================

def add_edge(
    graph: Dict[str, Set[str]],
    reverse_graph: Dict[str, Set[str]],
    source: str,
    destination: str,
) -> None:
    """
    Adds a directed route connection:
        source -> destination
    """
    if not source or not destination:
        return

    if source == destination:
        return

    graph[source].add(destination)
    reverse_graph[destination].add(source)

    # Ensure both exist in graph dictionaries.
    graph[destination]
    reverse_graph[source]


def extract_route_graph(
    page_texts: List[str],
) -> Tuple[
    Dict[str, Set[str]],
    Dict[str, Set[str]],
    List[Dict[str, Any]],
]:
    """
    Builds a route graph from the uploaded PDF.

    The PDF should ideally contain route sequences such as:

        Route A: 0-1-1-0, 0-1-2-0, 0-1-3-0

    Every ordered sequence becomes:

        00-01-1-0 -> 00-01-2-0 -> 00-01-3-0

    It also detects branch sequences found in a single PDF line.
    """

    graph: Dict[str, Set[str]] = defaultdict(set)
    reverse_graph: Dict[str, Set[str]] = defaultdict(set)

    evidence_rows: List[Dict[str, Any]] = []

    for page_number, page_text in enumerate(page_texts, start=1):
        cleaned = re.sub(r"[ \t]+", " ", page_text)
        cleaned = re.sub(r"\n{2,}", "\n", cleaned)

        lines = [
            line.strip()
            for line in cleaned.splitlines()
            if line.strip()
        ]

        # -------------------------------------------------------------
        # METHOD 1:
        # Any line containing two or more branch references is treated
        # as a route sequence.
        # -------------------------------------------------------------
        for line in lines:
            branches = branch_tokens(line)

            if len(branches) < 2:
                continue

            for source, destination in zip(
                branches,
                branches[1:],
            ):
                add_edge(
                    graph,
                    reverse_graph,
                    source,
                    destination,
                )

                evidence_rows.append(
                    {
                        "source_branch": source,
                        "destination_branch": destination,
                        "page": page_number,
                        "evidence": line,
                    }
                )

        # -------------------------------------------------------------
        # METHOD 2:
        # Detect route blocks that mention "route" and contain multiple
        # branch IDs across multiple lines.
        # -------------------------------------------------------------
        route_blocks = re.split(
            r"(?i)(?=\b(?:first|second|third|fourth)?\s*route\b)",
            cleaned,
        )

        for block in route_blocks:
            if "route" not in block.lower():
                continue

            branches = branch_tokens(block)

            if len(branches) < 2:
                continue

            for source, destination in zip(
                branches,
                branches[1:],
            ):
                add_edge(
                    graph,
                    reverse_graph,
                    source,
                    destination,
                )

                evidence_rows.append(
                    {
                        "source_branch": source,
                        "destination_branch": destination,
                        "page": page_number,
                        "evidence": block[:500],
                    }
                )

    return graph, reverse_graph, evidence_rows


# =============================================================================
# EVENT EXTRACTION
# =============================================================================

def extract_test_events(page_texts: List[str]) -> pd.DataFrame:
    """
    Finds possible low-speed test points in the same uploaded PDF.

    The extraction uses:
    - nearby LS keywords;
    - nearest branch number;
    - nearby abscissa;
    - nearby variant number.

    All extracted data must be reviewed by an engineer.
    """

    rows: List[Dict[str, Any]] = []

    abscissa_pattern = re.compile(
        r"(?:Abs(?:cissa)?|K\.?P\.?|Installation\s*K\.?P\.?)"
        r"\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)",
        flags=re.IGNORECASE,
    )

    variant_pattern = re.compile(
        r"(?:Associated\s+Variant|Variant(?:\s+Number)?)"
        r"\s*[:=]?\s*(\d+)",
        flags=re.IGNORECASE,
    )

    for page_number, page_text in enumerate(page_texts, start=1):
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in page_text.splitlines()
            if line.strip()
        ]

        for line_index, line in enumerate(lines):
            context_lines = lines[
                max(0, line_index - 2):
                min(len(lines), line_index + 3)
            ]

            context = " ".join(context_lines)

            test_code = identify_event_type(context)

            if not test_code:
                continue

            nearby_branch = ""

            # Search closest lines first.
            for offset in [0, -1, 1, -2, 2]:
                target_index = line_index + offset

                if (
                    target_index < 0
                    or target_index >= len(lines)
                ):
                    continue

                possible_branches = branch_tokens(
                    lines[target_index]
                )

                if possible_branches:
                    nearby_branch = possible_branches[0]
                    break

            abscissa_match = abscissa_pattern.search(context)
            variant_match = variant_pattern.search(context)

            rows.append(
                {
                    "test_code": test_code,
                    "test_id": TEST_TYPES[test_code]["id"],
                    "test_name": TEST_TYPES[test_code]["name"],
                    "branch": nearby_branch,
                    "sector": branch_sector(nearby_branch),
                    "section": branch_section(nearby_branch),
                    "subsection": branch_subsection(nearby_branch),
                    "abscissa_m": (
                        float(
                            abscissa_match.group(1).replace(",", ".")
                        )
                        if abscissa_match
                        else None
                    ),
                    "variant_number": (
                        variant_match.group(1)
                        if variant_match
                        else ""
                    ),
                    "event_description": context,
                    "source_page": page_number,
                    "review_status": "To be reviewed",
                    "remarks": "",
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "test_code",
                "test_id",
                "test_name",
                "branch",
                "sector",
                "section",
                "subsection",
                "abscissa_m",
                "variant_number",
                "event_description",
                "source_page",
                "review_status",
                "remarks",
            ]
        )

    events = pd.DataFrame(rows)

    return (
        events.drop_duplicates(
            subset=[
                "test_code",
                "branch",
                "event_description",
                "source_page",
            ]
        )
        .reset_index(drop=True)
    )


# =============================================================================
# GRAPH PATH FINDING
# =============================================================================

def reachable_nodes(
    graph: Dict[str, Set[str]],
    start_branch: str,
) -> List[str]:
    """
    Returns every branch that can be reached from the selected start.
    """

    visited: Set[str] = set()
    queue: deque = deque([start_branch])

    while queue:
        current = queue.popleft()

        if current in visited:
            continue

        visited.add(current)

        for next_node in graph.get(current, set()):
            if next_node not in visited:
                queue.append(next_node)

    visited.discard(start_branch)

    return sorted(visited)


def find_paths(
    graph: Dict[str, Set[str]],
    start_branch: str,
    end_branch: str,
    max_paths: int = 20,
    max_depth: int = 100,
) -> List[List[str]]:
    """
    Finds valid directed paths from start to end.

    max_paths prevents an extremely complex PDF network from creating
    thousands of possible route combinations.
    """

    paths: List[List[str]] = []

    def walk(
        current: str,
        current_path: List[str],
        visited: Set[str],
    ) -> None:
        if len(paths) >= max_paths:
            return

        if len(current_path) > max_depth:
            return

        if current == end_branch:
            paths.append(current_path.copy())
            return

        for next_branch in sorted(graph.get(current, set())):
            if next_branch in visited:
                continue

            walk(
                next_branch,
                current_path + [next_branch],
                visited | {next_branch},
            )

    walk(
        current=start_branch,
        current_path=[start_branch],
        visited={start_branch},
    )

    return paths


# =============================================================================
# ROUTE DIAGRAM
# =============================================================================

def build_path_diagram(
    path: List[str],
    start_branch: str,
    end_branch: str,
) -> str:
    if not path:
        return """
        <html>
        <body style="font-family:Arial; padding:20px;">
            No route path available.
        </body>
        </html>
        """

    node_spacing = 180
    width = max(950, len(path) * node_spacing + 120)
    nodes_html = []

    for index, branch in enumerate(path):
        x = 90 + (index * node_spacing)

        if branch == start_branch:
            fill = "#D9F2E6"
            stroke = "#16803C"
            label = "START"
        elif branch == end_branch:
            fill = "#FFE2E2"
            stroke = "#D64545"
            label = "END"
        else:
            fill = "#E6F3FC"
            stroke = "#0064A6"
            label = "ROUTE"

        safe_branch = branch.replace("&", "&amp;")

        nodes_html.append(
            f"""
            <line
                x1="{x - 72}"
                y1="130"
                x2="{x + 72}"
                y2="130"
                stroke="{stroke}"
                stroke-width="7"
                stroke-linecap="round"
            />

            <circle
                cx="{x}"
                cy="130"
                r="28"
                fill="{fill}"
                stroke="{stroke}"
                stroke-width="5"
            />

            <text
                x="{x}"
                y="185"
                text-anchor="middle"
                font-family="Arial, sans-serif"
                font-size="13"
                fill="#102A43"
                font-weight="700"
            >
                {safe_branch}
            </text>

            <text
                x="{x}"
                y="208"
                text-anchor="middle"
                font-family="Arial, sans-serif"
                font-size="11"
                fill="{stroke}"
                font-weight="700"
            >
                {label}
            </text>
            """
        )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                margin: 0;
                padding: 0;
                font-family: Arial, sans-serif;
                background: transparent;
            }}

            .diagram-wrapper {{
                overflow-x: auto;
                border: 1px solid #D9E2EC;
                border-radius: 16px;
                padding: 12px;
                background: #FFFFFF;
            }}
        </style>
    </head>

    <body>
        <div class="diagram-wrapper">
            <svg
                width="{width}"
                height="245"
                viewBox="0 0 {width} 245"
                xmlns="http://www.w3.org/2000/svg"
            >
                <rect
                    width="{width}"
                    height="245"
                    rx="14"
                    fill="#F8FBFD"
                />

                <text
                    x="28"
                    y="38"
                    font-family="Arial, sans-serif"
                    font-size="20"
                    font-weight="700"
                    fill="#102A43"
                >
                    Selected Train Route
                </text>

                <text
                    x="28"
                    y="64"
                    font-family="Arial, sans-serif"
                    font-size="13"
                    fill="#52606D"
                >
                    Green = Start Point | Blue = Route Branch | Red = End Point
                </text>

                <line
                    x1="25"
                    y1="130"
                    x2="{width - 35}"
                    y2="130"
                    stroke="#B0BEC5"
                    stroke-width="4"
                />

                <polygon
                    points="{width - 25},130 {width - 48},118 {width - 48},142"
                    fill="#102A43"
                />

                {''.join(nodes_html)}
            </svg>
        </div>
    </body>
    </html>
    """


# =============================================================================
# TEST PLAN
# =============================================================================

def create_test_plan(
    selected_path: List[str],
    reviewed_events: pd.DataFrame,
) -> pd.DataFrame:
    """
    Keeps only test events located on the selected route path.
    """

    if reviewed_events.empty:
        return pd.DataFrame()

    selected_branches = set(selected_path)

    plan = reviewed_events[
        reviewed_events["branch"].astype(str).isin(selected_branches)
    ].copy()

    plan = plan.reset_index(drop=True)

    return plan


def add_manual_test_row(
    plan: pd.DataFrame,
    selected_path: List[str],
) -> pd.DataFrame:
    """
    Optional manual row. This is useful when a PDF is graphical and
    an engineer needs to add an approved test point manually.
    """

    manual_branch = selected_path[0] if selected_path else ""

    row = {
        "test_code": "",
        "test_id": "",
        "test_name": "Manual engineering test item",
        "branch": manual_branch,
        "sector": branch_sector(manual_branch),
        "section": branch_section(manual_branch),
        "subsection": branch_subsection(manual_branch),
        "abscissa_m": None,
        "variant_number": "",
        "event_description": "",
        "source_page": "",
        "review_status": "To be reviewed",
        "remarks": "",
    }

    return pd.concat(
        [plan, pd.DataFrame([row])],
        ignore_index=True,
    )


# =============================================================================
# DOCX EXPORT
# =============================================================================

def procedure_checks(test_code: str) -> List[Tuple[str, str]]:
    """
    Basic generic test-record checks based on your procedure.
    The test code determines the sheet title and expected checks.
    """

    common_checks = [
        (
            "Source / Location",
            "Confirm approved branch, abscissa and route.",
        ),
        (
            "OMAP / Log Evidence",
            "Record or reference supporting evidence.",
        ),
        (
            "Engineering Result",
            "Pass / Fail / Punchlist.",
        ),
    ]

    special_checks: Dict[str, List[Tuple[str, str]]] = {
        "LS01": [
            ("Selected Mode", "RM"),
            ("Localisation Status", "Out Loc → Wait Loc → Total Loc"),
            ("Beacon Codes", "Record and compare with expected code."),
            ("Calibration", "Record calibration value."),
        ],
        "LS02": [
            ("Selected Mode", "RM"),
            ("Localisation Status", "Out Loc → Wait Inv → Total Loc"),
            ("Mode Available", "CM/AM available when applicable."),
            ("Emergency Brake", "No EB after first RB."),
        ],
        "LS03": [
            ("Track Circuit Occupied", "Variant restrictive."),
            ("Track Circuit Free", "Variant permissive."),
            ("Target Distance", "Permissive distance ≥ restrictive distance."),
        ],
        "LS04": [
            ("Signal Restrictive", "Variant restrictive."),
            ("Signal Permissive", "Variant permissive."),
            ("Target Distance", "Permissive distance ≥ restrictive distance."),
        ],
        "LS05": [
            ("Signal / Overlap Restrictive", "Variants restrictive."),
            ("Signal / Overlap Permissive", "Variants permissive."),
        ],
        "LS06": [
            ("Point Uncontrolled", "Variant restrictive."),
            ("Point Controlled", "Variant permissive."),
        ],
        "LS07": [
            ("Before End of SACEM", "CM/AM available where applicable."),
            ("Beyond End of SACEM", "CM/AM not available."),
        ],
        "LS08": [
            ("Localisation", "Total Loc."),
            ("Door Side", "Correct side enabled."),
            ("Stopping Window", "Within ±0.35 m."),
        ],
        "LS09": [
            ("Incorrect Door Side", "PSD / relay not activated."),
            ("Correct Door Side", "PSD / relay activated."),
            ("All Doors Closed", "Expected variant and mode behaviour."),
        ],
        "LS10": [
            ("ESZ Active", "Variant restrictive."),
            ("Inside ESZ", "CM/AM unavailable."),
            ("End of ESZ", "CM/AM becomes available."),
        ],
        "LS11": [
            ("Cab 1", "Total Loc and CM/AM available."),
            ("Cab 2", "CM/AM available within 20 seconds."),
            ("Post CoE", "No delocalization / no EB."),
        ],
        "LS12": [
            ("Track Circuit", "Record selected track circuit."),
            ("TSR Value", "Record TSR value."),
            ("Train Behaviour", "Target speed follows TSR and no EB."),
        ],
        "LS13": [
            ("Return Channel Loop", "Record loop location."),
            ("Trackside Reception", "Train information received."),
        ],
        "LS14": [
            ("Begin of Shunt", "Record branch, Loc Max and target distance."),
            ("End of Shunt", "Record CM/AM recovery position."),
        ],
    }

    return special_checks.get(test_code, []) + common_checks


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
            "Missing package: python-docx. "
            "Install it with: pip install python-docx"
        ) from exc

    test_code = safe_text(row.get("test_code"))
    test_data = TEST_TYPES.get(
        test_code,
        {
            "id": "",
            "name": "Manual Engineering Test",
            "purpose": "",
            "icon": "📝",
        },
    )

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.4)
    section.bottom_margin = Inches(0.4)
    section.left_margin = Inches(0.45)
    section.right_margin = Inches(0.45)

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    heading_run = heading.add_run("ALSTOM\n")
    heading_run.bold = True
    heading_run.font.size = Pt(16)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{test_data['id']} – {test_data['name']}"
    )
    title_run.bold = True
    title_run.font.size = Pt(12)

    document.add_paragraph()

    info_table = document.add_table(rows=0, cols=2)
    info_table.style = "Table Grid"

    fields = [
        ("Procedure Reference", project_info["procedure_reference"]),
        ("Procedure Title", project_info["procedure_title"]),
        ("Test Sheet Number", sheet_number),
        ("Project", project_info["project"]),
        ("Trackside Version", project_info["trackside_version"]),
        ("ATP Version", project_info["atp_version"]),
        ("ATO Version", project_info["ato_version"]),
        ("Branch Number", safe_text(row.get("branch"))),
        ("Sector", safe_text(row.get("sector"))),
        ("Section", safe_text(row.get("section"))),
        ("Sub-section", safe_text(row.get("subsection"))),
        (
            "Abscissa",
            f"{safe_text(row.get('abscissa_m'))} m",
        ),
        ("Variant Number", safe_text(row.get("variant_number"))),
        ("Source Page", safe_text(row.get("source_page"))),
    ]

    for label, value in fields:
        cells = info_table.add_row().cells
        cells[0].text = label
        cells[1].text = safe_text(value)

    document.add_paragraph()

    purpose_heading = document.add_paragraph()
    purpose_heading.add_run("Purpose of Test").bold = True

    document.add_paragraph(
        safe_text(test_data.get("purpose"))
    )

    document.add_paragraph()

    record_heading = document.add_paragraph()
    record_heading.add_run("Test Record").bold = True

    test_table = document.add_table(rows=1, cols=4)
    test_table.style = "Table Grid"

    headers = [
        "Check / Record",
        "Expected Requirement",
        "Actual Result",
        "Pass / Fail",
    ]

    for index, header in enumerate(headers):
        test_table.rows[0].cells[index].text = header

    for check, expected in procedure_checks(test_code):
        cells = test_table.add_row().cells

        cells[0].text = check
        cells[1].text = expected
        cells[2].text = ""
        cells[3].text = "☐ Pass   ☐ Fail   ☐ N/A"

    document.add_paragraph()

    remarks_heading = document.add_paragraph()
    remarks_heading.add_run(
        "Engineering Remarks / Evidence"
    ).bold = True

    document.add_paragraph(
        safe_text(row.get("remarks"))
        or safe_text(row.get("event_description"))
        or "No remarks recorded."
    )

    document.add_paragraph()

    result_table = document.add_table(rows=3, cols=3)
    result_table.style = "Table Grid"

    result_table.rows[0].cells[0].text = "Test Result"
    result_table.rows[0].cells[1].text = (
        "☐ Passed    ☐ Failed    ☐ Punchlist Raised"
    )

    result_table.rows[1].cells[0].text = "Role"
    result_table.rows[1].cells[1].text = "Name / Signature"
    result_table.rows[1].cells[2].text = "Date"

    result_table.rows[2].cells[0].text = "Alstom Test Engineer"
    result_table.rows[2].cells[1].text = ""
    result_table.rows[2].cells[2].text = ""

    witness_row = result_table.add_row().cells
    witness_row[0].text = "MTR Witness"
    witness_row[1].text = ""
    witness_row[2].text = ""

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
    test_plan: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
    zip_output = io.BytesIO()

    with zipfile.ZipFile(
        zip_output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for index, (_, row) in enumerate(
            test_plan.iterrows(),
            start=1,
        ):
            test_code = safe_text(row.get("test_code")) or "MANUAL"
            branch = safe_text(row.get("branch")) or "GENERAL"

            sheet_number = (
                f"{project_info['sheet_prefix']}-{index:03d}"
            )

            filename = (
                f"{sheet_number}_{test_code}_{branch}.docx"
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
                    project_info=project_info,
                    sheet_number=sheet_number,
                ),
            )

    return zip_output.getvalue()


def create_excel_bytes(
    test_plan: pd.DataFrame,
    selected_path: List[str],
    project_info: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        summary = pd.DataFrame(
            [
                ["Procedure Reference", project_info["procedure_reference"]],
                ["Procedure Title", project_info["procedure_title"]],
                ["Project", project_info["project"]],
                ["Trackside Version", project_info["trackside_version"]],
                ["ATP Version", project_info["atp_version"]],
                ["ATO Version", project_info["ato_version"]],
                ["Generated Date", str(date.today())],
                ["Start Branch", selected_path[0] if selected_path else ""],
                ["End Branch", selected_path[-1] if selected_path else ""],
                ["Route Branches", ", ".join(selected_path)],
            ],
            columns=["Field", "Value"],
        )

        summary.to_excel(
            writer,
            sheet_name="Cover",
            index=False,
        )

        pd.DataFrame(
            {"Selected Route Branches": selected_path}
        ).to_excel(
            writer,
            sheet_name="Selected Route",
            index=False,
        )

        test_plan.to_excel(
            writer,
            sheet_name="Test Register",
            index=False,
        )

        workbook = writer.book

        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for cells in worksheet.columns:
                max_length = max(
                    len(safe_text(cell.value))
                    for cell in cells
                )

                worksheet.column_dimensions[
                    cells[0].column_letter
                ].width = min(max(max_length + 2, 12), 55)

    return output.getvalue()


# =============================================================================
# VISUAL START / END SELECTION
# =============================================================================

def route_button_grid(
    branches: List[str],
    button_prefix: str,
    selected_key: str,
    columns_count: int = 4,
) -> None:
    """
    Displays visual branch buttons instead of a dropdown list.
    """

    columns = st.columns(columns_count)

    for index, branch in enumerate(branches):
        with columns[index % columns_count]:
            is_selected = (
                st.session_state.get(selected_key) == branch
            )

            label = (
                f"✅ {branch}"
                if is_selected
                else f"🛤️ {branch}"
            )

            if st.button(
                label,
                key=f"{button_prefix}_{branch}",
                use_container_width=True,
            ):
                st.session_state[selected_key] = branch


# =============================================================================
# MAIN STREAMLIT APP
# =============================================================================

def render_test_sheet_generator_app() -> None:
    render_test_sheet_generator_styles()

    st.markdown(
        """
        <div class="tsg-hero">
            <h1>ATC Route-to-Test Sheet Generator</h1>
            <p>
                Upload one ATC route / sector subdivision PDF. Choose a start
                point, choose a reachable end point, review all detected tests,
                then generate your low-speed test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="tsg-warning">
            <b>Engineering control requirement:</b>
            This tool builds a route graph from branch sequences detected in the
            uploaded PDF. The responsible ATC engineer must verify all route
            connections, singularities, abscissae, variants and final test sheets
            against the approved controlled design document.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # PROJECT CONFIGURATION
    # -------------------------------------------------------------------------

    with st.expander(
        "Project and document information",
        expanded=True,
    ):
        col1, col2, col3 = st.columns(3)

        with col1:
            project_name = st.text_input(
                "Project / Contract",
                value="",
                key="route_project_name",
            )

            trackside_version = st.text_input(
                "Trackside Version",
                value="",
                key="route_trackside_version",
            )

        with col2:
            atp_version = st.text_input(
                "ATP Version",
                value="",
                key="route_atp_version",
            )

            ato_version = st.text_input(
                "ATO Version",
                value="",
                key="route_ato_version",
            )

        with col3:
            procedure_reference = st.text_input(
                "Procedure Reference",
                value=PROCEDURE_REFERENCE,
                key="route_procedure_reference",
            )

            sheet_prefix = st.text_input(
                "Test Sheet Prefix",
                value=PROCEDURE_REFERENCE,
                key="route_sheet_prefix",
            )

    project_info = {
        "project": project_name,
        "trackside_version": trackside_version,
        "atp_version": atp_version,
        "ato_version": ato_version,
        "procedure_reference": procedure_reference,
        "procedure_title": PROCEDURE_TITLE,
        "sheet_prefix": sheet_prefix,
    }

    # -------------------------------------------------------------------------
    # ONE PDF UPLOAD ONLY
    # -------------------------------------------------------------------------

    st.markdown("## 1. Upload the route / subdivision PDF")

    st.markdown(
        """
        <div class="tsg-info">
            <b>Only one input file is needed.</b><br>
            Upload the approved ATC Sector Subdivision, Appendix J,
            route transmission document, route table or route diagram PDF.
            <br><br>
            The PDF should contain branch sequences such as:
            <br>
            <code>0-1-1-0 → 0-1-2-0 → 0-1-3-0</code>
            <br>
            The application uses these sequences to understand which branches
            are connected.
        </div>
        """,
        unsafe_allow_html=True,
    )

    route_pdf = st.file_uploader(
        "Upload Route / Sector Subdivision PDF",
        type=["pdf"],
        key="route_subdivision_pdf",
    )

    if route_pdf is None:
        st.info(
            "Upload one route or sector subdivision PDF to start."
        )
        return

    try:
        with st.spinner(
            "Reading PDF and building route connections..."
        ):
            page_texts = extract_pdf_text(
                route_pdf.getvalue()
            )

            graph, reverse_graph, graph_evidence = extract_route_graph(
                page_texts
            )

            events = extract_test_events(page_texts)

    except Exception as exc:
        st.error(
            f"Could not process PDF: {exc}"
        )
        return

    all_branches = sorted(
        set(graph.keys())
        | set(reverse_graph.keys())
    )

    if not all_branches:
        st.error(
            "No connected branch sequences were found in this PDF. "
            "The PDF must contain selectable text or OCR text with branch "
            "numbers in ordered route sequences, for example: "
            "'0-1-1-0, 0-1-2-0, 0-1-3-0'."
        )

        with st.expander(
            "Show extracted PDF text for troubleshooting"
        ):
            for page_number, page_text in enumerate(
                page_texts[:10],
                start=1,
            ):
                st.text_area(
                    f"PDF Page {page_number}",
                    value=page_text[:5000],
                    height=220,
                    disabled=True,
                    key=f"route_pdf_debug_{page_number}",
                )

        return

    # -------------------------------------------------------------------------
    # EXTRACTION SUMMARY
    # -------------------------------------------------------------------------

    st.markdown("## 2. PDF route network found")

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric(
        "Connected Branches",
        len(all_branches),
    )

    metric2.metric(
        "Route Connections",
        len(graph_evidence),
    )

    metric3.metric(
        "Possible Test Events",
        len(events),
    )

    with st.expander(
        "Show detected route connections",
        expanded=False,
    ):
        if graph_evidence:
            evidence_df = pd.DataFrame(graph_evidence)

            st.dataframe(
                evidence_df,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "No individual route-connection evidence rows were stored."
            )

    # -------------------------------------------------------------------------
    # START POINT
    # -------------------------------------------------------------------------

    st.markdown("## 3. Choose your starting point")

    st.markdown(
        """
        <div class="tsg-info">
            Choose where the train starts. After you click one branch,
            the app will automatically show only the branches that can be
            reached from that start point.
        </div>
        """,
        unsafe_allow_html=True,
    )

    start_filter = st.text_input(
        "Search starting point",
        placeholder="Example: 00-01 or 08-04",
        key="start_branch_filter",
    )

    filtered_starts = [
        branch
        for branch in all_branches
        if start_filter.lower() in branch.lower()
    ]

    if not filtered_starts:
        st.warning(
            "No starting branches match your search."
        )
        return

    route_button_grid(
        branches=filtered_starts[:120],
        button_prefix="start_branch",
        selected_key="selected_start_branch",
    )

    selected_start = st.session_state.get(
        "selected_start_branch",
        "",
    )

    if not selected_start:
        st.info(
            "Click one branch above to select the train starting point."
        )
        return

    st.success(
        f"Selected starting point: {selected_start}"
    )

    # -------------------------------------------------------------------------
    # END POINT
    # -------------------------------------------------------------------------

    possible_ends = reachable_nodes(
        graph=graph,
        start_branch=selected_start,
    )

    st.markdown("## 4. Choose your end point")

    if not possible_ends:
        st.warning(
            "No reachable end point was found from this starting branch. "
            "This may mean that the PDF does not contain a complete route "
            "sequence after this branch."
        )
        return

    st.markdown(
        """
        <div class="tsg-info">
            These are the valid destinations that can be reached from the
            selected starting point. Choose where the train should finish.
        </div>
        """,
        unsafe_allow_html=True,
    )

    end_filter = st.text_input(
        "Search reachable end point",
        placeholder="Example: 08-04 or 09-05",
        key="end_branch_filter",
    )

    filtered_ends = [
        branch
        for branch in possible_ends
        if end_filter.lower() in branch.lower()
    ]

    route_button_grid(
        branches=filtered_ends[:120],
        button_prefix="end_branch",
        selected_key="selected_end_branch",
    )

    selected_end = st.session_state.get(
        "selected_end_branch",
        "",
    )

    if not selected_end:
        st.info(
            "Click one reachable branch above to select the end point."
        )
        return

    if selected_end not in possible_ends:
        st.warning(
            "The selected end point is not reachable from the current start "
            "point. Please choose another end point."
        )
        return

    st.success(
        f"Selected route: {selected_start} → {selected_end}"
    )

    # -------------------------------------------------------------------------
    # PATH SELECTION
    # -------------------------------------------------------------------------

    available_paths = find_paths(
        graph=graph,
        start_branch=selected_start,
        end_branch=selected_end,
        max_paths=20,
        max_depth=100,
    )

    if not available_paths:
        st.error(
            "No valid path could be found between the selected start and end "
            "points. Please select another end point."
        )
        return

    st.markdown("## 5. Choose the route path")

    st.caption(
        f"The PDF contains {len(available_paths)} possible route path(s) "
        f"between {selected_start} and {selected_end}."
    )

    if len(available_paths) == 1:
        selected_path = available_paths[0]

        st.success(
            "One valid route path was found automatically."
        )

    else:
        path_labels = []

        for index, path in enumerate(available_paths, start=1):
            path_labels.append(
                f"Route Option {index} "
                f"({len(path)} branches)"
            )

        selected_path_label = st.radio(
            "Choose the correct physical route",
            options=path_labels,
            key="route_path_selector",
        )

        selected_path_index = path_labels.index(
            selected_path_label
        )

        selected_path = available_paths[selected_path_index]

        st.info(
            f"""
            **Selected route:** {selected_path[0]} → {selected_path[-1]}  
            **Number of route branches:** {len(selected_path)}
            """
        )

        with st.expander(
            "Show technical branch sequence",
            expanded=False,
        ):
            st.code(
                " → ".join(selected_path),
                language=None,
            )

    components.html(
        build_path_diagram(
            path=selected_path,
            start_branch=selected_start,
            end_branch=selected_end,
        ),
        height=280,
        scrolling=True,
    )

    st.caption(
        f"Selected path contains {len(selected_path)} branch(es)."
    )

    with st.expander(
        "Show branches in selected path",
        expanded=False,
    ):
        st.dataframe(
            pd.DataFrame(
                {
                    "Sequence": list(
                        range(1, len(selected_path) + 1)
                    ),
                    "Branch": selected_path,
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    # -------------------------------------------------------------------------
    # TEST EVENTS ON PATH
    # -------------------------------------------------------------------------

    st.markdown("## 6. Review tests found along this route")

    test_plan = create_test_plan(
        selected_path=selected_path,
        reviewed_events=events,
    )

    if test_plan.empty:
        st.warning(
            "No low-speed test events were automatically found on this route. "
            "This can happen if the PDF uses image-only tables or if the "
            "singularity descriptions do not contain searchable text."
        )

        test_plan = pd.DataFrame(
            columns=[
                "test_code",
                "test_id",
                "test_name",
                "branch",
                "sector",
                "section",
                "subsection",
                "abscissa_m",
                "variant_number",
                "event_description",
                "source_page",
                "review_status",
                "remarks",
            ]
        )

    add_manual_test = st.checkbox(
        "Add one manual engineering test row",
        value=False,
        help=(
            "Use this if the approved PDF contains a graphical test point "
            "that could not be read automatically."
        ),
    )

    if add_manual_test:
        test_plan = add_manual_test_row(
            plan=test_plan,
            selected_path=selected_path,
        )

    reviewed_plan = st.data_editor(
        test_plan,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="route_test_plan_editor",
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

    # Automatically update test ID and test name after user edits code.
    for row_index in reviewed_plan.index:
        test_code = safe_text(
            reviewed_plan.at[row_index, "test_code"]
        )

        if test_code in TEST_TYPES:
            reviewed_plan.at[
                row_index,
                "test_id",
            ] = TEST_TYPES[test_code]["id"]

            reviewed_plan.at[
                row_index,
                "test_name",
            ] = TEST_TYPES[test_code]["name"]

    st.markdown("## 7. Generate and download test sheets")

    total_tests = len(reviewed_plan)

    reviewed_count = (
        reviewed_plan["review_status"]
        .astype(str)
        .str.contains(
            "reviewed|approved",
            case=False,
            regex=True,
            na=False,
        )
        .sum()
        if not reviewed_plan.empty
        else 0
    )

    result_col1, result_col2, result_col3 = st.columns(3)

    result_col1.metric(
        "Test Sheets",
        total_tests,
    )

    result_col2.metric(
        "Reviewed / Approved",
        int(reviewed_count),
    )

    result_col3.metric(
        "Route Length",
        f"{len(selected_path)} branches",
    )

    st.markdown(
        """
        <div class="tsg-warning">
            <b>Before issuing:</b>
            verify branch numbers, route direction, abscissae, variant numbers,
            anticipated variants, station names, point names, signal names and
            the applicable approved design source.
        </div>
        """,
        unsafe_allow_html=True,
    )

    export_col1, export_col2, export_col3 = st.columns(3)

    with export_col1:
        excel_bytes = create_excel_bytes(
            test_plan=reviewed_plan,
            selected_path=selected_path,
            project_info=project_info,
        )

        st.download_button(
            "⬇️ Download Excel Test Register",
            data=excel_bytes,
            file_name=(
                f"{sheet_prefix}_"
                f"{selected_start}_TO_{selected_end}_"
                f"Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
        )

    with export_col2:
        st.download_button(
            "⬇️ Download Reviewed Test Plan CSV",
            data=reviewed_plan.to_csv(
                index=False
            ).encode("utf-8"),
            file_name=(
                f"{sheet_prefix}_"
                f"{selected_start}_TO_{selected_end}_"
                f"Test_Plan.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with export_col3:
        try:
            docx_zip = create_docx_zip(
                test_plan=reviewed_plan,
                project_info=project_info,
            )

            st.download_button(
                "⬇️ Download DOCX Test Sheets",
                data=docx_zip,
                file_name=(
                    f"{sheet_prefix}_"
                    f"{selected_start}_TO_{selected_end}_"
                    f"Test_Sheets.zip"
                ),
                mime="application/zip",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(
                f"DOCX generation failed: {exc}"
            )
