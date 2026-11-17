from __future__ import annotations

import html
import io
import re
import zipfile
from collections import defaultdict, deque
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# =============================================================================
# FIXED TEST PROCEDURE CONFIGURATION
# =============================================================================
# These definitions come from C1252-TAC-0274.
# Route data, branches, paths, stations and event positions come only from
# the user-uploaded PDF.

PROCEDURE_REFERENCE = "C1252-TAC-0274"
PROCEDURE_TITLE = "Trackside ATC Low-Speed Dynamic Test Procedure"


TEST_TYPES: Dict[str, Dict[str, str]] = {
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
            "Verify that CM/AM is not available beyond End of SACEM."
        ),
    },
    "LS08": {
        "id": "ATC-TNC-TRA-LS08",
        "name": "Station Stopping Point and Door Side Verification",
        "icon": "🚉",
        "purpose": (
            "Verify station stopping position and correct enabled door side."
        ),
    },
    "LS09": {
        "id": "ATC-TNC-TRA-LS09",
        "name": "PSD Operation Pre-Test",
        "icon": "🚪",
        "purpose": (
            "Verify ATC / PSD door command interface behaviour."
        ),
    },
    "LS10": {
        "id": "ATC-TNC-TRA-LS10",
        "name": "Emergency Stop Zone Test",
        "icon": "⚠️",
        "purpose": (
            "Verify CM/AUTO is unavailable inside an active Emergency Stop Zone."
        ),
    },
    "LS11": {
        "id": "ATC-TNC-TRA-LS11",
        "name": "Change of End Test",
        "icon": "🔄",
        "purpose": (
            "Verify Change of End can be completed without delocalization or EB."
        ),
    },
    "LS12": {
        "id": "ATC-TNC-TRA-LS12",
        "name": "Temporary Speed Restriction Test",
        "icon": "🚧",
        "purpose": (
            "Verify correct train behaviour after a TSR is applied."
        ),
    },
    "LS13": {
        "id": "ATC-TNC-TRA-LS13",
        "name": "Return Channel Test",
        "icon": "📶",
        "purpose": (
            "Verify train information is received through the return-channel loop."
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


GENERIC_ROUTE_REVIEW_CODE = "ROUTE-REVIEW"


# =============================================================================
# PAGE UI
# =============================================================================

def render_test_sheet_generator_styles() -> None:
    st.markdown(
        """
        <style>
        .tsg-hero {
            background: linear-gradient(
                135deg,
                #102A43 0%,
                #164E72 65%,
                #E52B1E 160%
            );
            padding: 28px 32px;
            border-radius: 18px;
            margin-bottom: 18px;
            box-shadow: 0 10px 25px rgba(16,42,67,.22);
        }

        .tsg-hero h1 {
            margin: 0 !important;
            color: white !important;
            font-size: 30px !important;
        }

        .tsg-hero p {
            margin: 10px 0 0 0 !important;
            color: #E1EEF6 !important;
            font-size: 15px;
        }

        .tsg-info {
            background: #EDF7FF;
            border: 1px solid #B8DCF4;
            border-left: 5px solid #0064A6;
            border-radius: 12px;
            padding: 14px 16px;
            margin: 10px 0;
        }

        .tsg-warning {
            background: #FFF7E8;
            border: 1px solid #F5C16C;
            border-left: 5px solid #E37400;
            border-radius: 12px;
            padding: 14px 16px;
            margin: 10px 0;
        }

        .tsg-success {
            background: #EDFFF3;
            border: 1px solid #A3D9B1;
            border-left: 5px solid #16803C;
            border-radius: 12px;
            padding: 14px 16px;
            margin: 10px 0;
        }

        div.stButton > button {
            min-height: 46px;
            border-radius: 10px;
            font-weight: 650;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# GENERIC UTILITIES
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


def normalise_branch(value: Any) -> str:
    """
    Converts:
        9-1-1-0   -> 09-01-1-0
        09-01-1-0 -> 09-01-1-0
        0-1-1-0   -> 00-01-1-0
    """

    text = safe_text(value)

    match = re.search(
        r"(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})",
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


def branch_tokens(value: Any) -> List[str]:
    matches = re.findall(
        r"\b\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2}\b",
        safe_text(value),
    )

    output: List[str] = []

    for match in matches:
        branch = normalise_branch(match)

        if branch not in output:
            output.append(branch)

    return output


def branch_sector(branch: str) -> str:
    parts = safe_text(branch).split("-")
    return parts[0] if len(parts) >= 1 else ""


def branch_section(branch: str) -> str:
    parts = safe_text(branch).split("-")
    return parts[1] if len(parts) >= 2 else ""


def branch_subsection(branch: str) -> str:
    parts = safe_text(branch).split("-")

    if len(parts) >= 3:
        return "-".join(parts[:3])

    return ""


# =============================================================================
# PDF TEXT EXTRACTION
# =============================================================================

def extract_pdf_text(pdf_bytes: bytes) -> List[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: pypdf. "
            "Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(pdf_bytes))

    return [
        page.extract_text() or ""
        for page in reader.pages
    ]


# =============================================================================
# EVENT / TEST TYPE IDENTIFICATION
# =============================================================================

def identify_event_type(text: str) -> str:
    value = safe_text(text).upper()

    # Explicit procedure IDs are the most reliable.
    id_match = re.search(
        r"ATC[- ]?TNC[- ]?TRA[- ]?LS(0[1-9]|1[0-4])",
        value,
    )

    if id_match:
        return f"LS{id_match.group(1)}"

    if "MTIB" in value or "MOVING TRAIN INITIAL" in value:
        return "LS01"

    if "STIB" in value or "STATIONARY TRAIN INITIAL" in value:
        return "LS02"

    if "END OF SHUNT" in value or "EXIT SACEM" in value:
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

    if "TEMPORARY SPEED RESTRICTION" in value:
        return "LS12"

    if re.search(r"\bTSR\b", value):
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
# ROUTE GRAPH EXTRACTION
# =============================================================================

def add_connection(
    graph: Dict[str, Set[str]],
    reverse_graph: Dict[str, Set[str]],
    source: str,
    destination: str,
) -> None:
    """Creates one directed connection: source -> destination."""

    if not source or not destination:
        return

    if source == destination:
        return

    graph[source].add(destination)
    reverse_graph[destination].add(source)

    # Make sure all branches exist as graph nodes.
    graph[destination]
    reverse_graph[source]


def looks_like_route_data(text: str) -> bool:
    """
    Avoid treating arbitrary tables as route paths.

    A line or paragraph is accepted only if it contains route-related wording.
    """

    value = safe_text(text).lower()

    keywords = [
        "route",
        "branches to run",
        "branch sequence",
        "transmission",
        "path",
        "first route",
        "second route",
        "third route",
        "fourth route",
        "anticipated branches",
        "from ",
        " to ",
    ]

    return any(keyword in value for keyword in keywords)


def extract_route_graph(
    page_texts: List[str],
) -> Tuple[
    Dict[str, Set[str]],
    Dict[str, Set[str]],
    pd.DataFrame,
]:
    """
    Extracts ordered route branch sequences.

    Example PDF text:

        First route:
        09-01-1-0, 09-01-2-0, 09-01-3-0, 14-04-1-1

    Creates:

        09-01-1-0 -> 09-01-2-0
        09-01-2-0 -> 09-01-3-0
        09-01-3-0 -> 14-04-1-1
    """

    graph: Dict[str, Set[str]] = defaultdict(set)
    reverse_graph: Dict[str, Set[str]] = defaultdict(set)
    evidence_rows: List[Dict[str, str]] = []

    route_start_pattern = re.compile(
        r"(?i)(?="
        r"\b(?:first|second|third|fourth)?\s*route\b"
        r"|\broute\s*[A-Z0-9]"
        r"|\bbranches?\s+to\s+run\b"
        r")"
    )

    for page_number, page_text in enumerate(page_texts, start=1):
        compact = re.sub(r"[ \t]+", " ", page_text)
        compact = re.sub(r"\n{2,}", "\n", compact)

        # -------------------------------------------------------------
        # Strategy 1:
        # Route blocks. This is the preferred extraction method.
        # -------------------------------------------------------------
        blocks = route_start_pattern.split(compact)

        for block in blocks:
            if not looks_like_route_data(block):
                continue

            branches = branch_tokens(block)

            if len(branches) < 2:
                continue

            for source, destination in zip(
                branches,
                branches[1:],
            ):
                add_connection(
                    graph,
                    reverse_graph,
                    source,
                    destination,
                )

                evidence_rows.append(
                    {
                        "source_branch": source,
                        "destination_branch": destination,
                        "page": str(page_number),
                        "evidence": block[:400],
                    }
                )

        # -------------------------------------------------------------
        # Strategy 2:
        # Individual lines which contain route wording and branch sequence.
        # -------------------------------------------------------------
        for line in compact.splitlines():
            if not looks_like_route_data(line):
                continue

            branches = branch_tokens(line)

            if len(branches) < 2:
                continue

            for source, destination in zip(
                branches,
                branches[1:],
            ):
                add_connection(
                    graph,
                    reverse_graph,
                    source,
                    destination,
                )

                evidence_rows.append(
                    {
                        "source_branch": source,
                        "destination_branch": destination,
                        "page": str(page_number),
                        "evidence": line[:400],
                    }
                )

    evidence = pd.DataFrame(
        evidence_rows,
        columns=[
            "source_branch",
            "destination_branch",
            "page",
            "evidence",
        ],
    )

    if not evidence.empty:
        evidence = evidence.drop_duplicates().reset_index(drop=True)

    return graph, reverse_graph, evidence


# =============================================================================
# EVENT EXTRACTION
# =============================================================================

def nearest_branch_from_lines(
    lines: List[str],
    line_index: int,
) -> str:
    """
    Looks around an event line for the nearest branch reference.
    """

    search_offsets = [
        0,
        -1,
        1,
        -2,
        2,
        -3,
        3,
        -4,
        4,
    ]

    for offset in search_offsets:
        target_index = line_index + offset

        if target_index < 0 or target_index >= len(lines):
            continue

        found = branch_tokens(lines[target_index])

        if found:
            return found[0]

    return ""


def extract_test_events(
    page_texts: List[str],
) -> pd.DataFrame:
    """
    Extracts possible LS01-LS14 events from the same uploaded PDF.

    The function attempts to find:
    - test type;
    - nearest branch;
    - abscissa;
    - variant number;
    - source evidence.

    If the source PDF is graphic/image-only, the user can still add or correct
    test rows in the Streamlit table.
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

        for index, line in enumerate(lines):
            context_lines = lines[
                max(index - 3, 0): min(index + 4, len(lines))
            ]

            context = " ".join(context_lines)
            test_code = identify_event_type(context)

            if not test_code:
                continue

            branch = nearest_branch_from_lines(
                lines=lines,
                line_index=index,
            )

            abscissa_match = abscissa_pattern.search(context)
            variant_match = variant_pattern.search(context)

            rows.append(
                {
                    "test_code": test_code,
                    "test_id": TEST_TYPES[test_code]["id"],
                    "test_name": TEST_TYPES[test_code]["name"],
                    "branch": branch,
                    "sector": branch_sector(branch),
                    "section": branch_section(branch),
                    "subsection": branch_subsection(branch),
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
                    "event_description": context[:1000],
                    "source_page": page_number,
                    "review_status": "To be reviewed",
                    "remarks": "",
                }
            )

    columns = [
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

    if not rows:
        return pd.DataFrame(columns=columns)

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
# START / END POINT LOGIC
# =============================================================================

def starting_points(
    graph: Dict[str, Set[str]],
) -> List[str]:
    """
    A starting point is a branch which has an outgoing connection.

    We do not include terminal nodes because a train cannot begin a route there
    and continue to another branch.
    """

    return sorted(
        branch
        for branch, next_branches in graph.items()
        if next_branches
    )


def reachable_endpoints(
    graph: Dict[str, Set[str]],
    start_branch: str,
) -> List[str]:
    """
    Returns ONLY final reachable endpoints.

    Intermediate route branches are intentionally excluded.

    Example:
        A -> B -> C -> D
        A -> B -> E

    User chooses start A.

    Returned endpoints:
        D
        E

    Not returned:
        B
        C
    """

    visited: Set[str] = set()
    queue: deque = deque([start_branch])
    endpoints: Set[str] = set()

    while queue:
        current = queue.popleft()

        if current in visited:
            continue

        visited.add(current)

        next_branches = graph.get(current, set())

        # A branch with no outgoing connection is a final end point.
        if not next_branches:
            if current != start_branch:
                endpoints.add(current)
            continue

        for next_branch in next_branches:
            if next_branch not in visited:
                queue.append(next_branch)

    return sorted(endpoints)


def find_paths(
    graph: Dict[str, Set[str]],
    start_branch: str,
    end_branch: str,
    max_paths: int = 20,
    max_depth: int = 150,
) -> List[List[str]]:
    """
    Finds directed paths from selected Start Point to selected End Point.
    """

    paths: List[List[str]] = []

    def visit(
        current_branch: str,
        current_path: List[str],
        visited: Set[str],
    ) -> None:
        if len(paths) >= max_paths:
            return

        if len(current_path) > max_depth:
            return

        if current_branch == end_branch:
            paths.append(current_path.copy())
            return

        for next_branch in sorted(
            graph.get(current_branch, set())
        ):
            if next_branch in visited:
                continue

            visit(
                current_branch=next_branch,
                current_path=current_path + [next_branch],
                visited=visited | {next_branch},
            )

    visit(
        current_branch=start_branch,
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
    """
    SVG is returned inside a complete HTML document.
    It must be rendered with components.html(), not st.markdown().
    """

    if not path:
        return """
        <html>
            <body>
                <p>No selected route path.</p>
            </body>
        </html>
        """

    node_spacing = 170
    width = max(900, len(path) * node_spacing + 130)

    svg_nodes: List[str] = []

    for index, branch in enumerate(path):
        x = 90 + (index * node_spacing)

        if branch == start_branch:
            fill = "#DCF7E7"
            stroke = "#16803C"
            label = "START"
        elif branch == end_branch:
            fill = "#FFE3E3"
            stroke = "#D64545"
            label = "END"
        else:
            fill = "#E7F3FC"
            stroke = "#0064A6"
            label = "ROUTE"

        safe_branch = html.escape(branch)

        svg_nodes.append(
            f"""
            <line
                x1="{x - 70}"
                y1="128"
                x2="{x + 70}"
                y2="128"
                stroke="{stroke}"
                stroke-width="7"
                stroke-linecap="round"
            />

            <circle
                cx="{x}"
                cy="128"
                r="27"
                fill="{fill}"
                stroke="{stroke}"
                stroke-width="5"
            />

            <text
                x="{x}"
                y="184"
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
                y="207"
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
                background: transparent;
                font-family: Arial, sans-serif;
            }}

            .route-box {{
                overflow-x: auto;
                background: #FFFFFF;
                border: 1px solid #D9E2EC;
                border-radius: 16px;
                padding: 12px;
            }}
        </style>
    </head>

    <body>
        <div class="route-box">
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
                    Green = Starting Point | Blue = Route Branch | Red = End Point
                </text>

                <line
                    x1="25"
                    y1="128"
                    x2="{width - 35}"
                    y2="128"
                    stroke="#B0BEC5"
                    stroke-width="4"
                />

                <polygon
                    points="{width - 22},128 {width - 47},115 {width - 47},141"
                    fill="#102A43"
                />

                {''.join(svg_nodes)}
            </svg>
        </div>
    </body>
    </html>
    """


# =============================================================================
# TEST PLAN CREATION
# =============================================================================

def event_matches_path(
    event_branch: str,
    path: List[str],
) -> bool:
    """
    Event belongs to the selected path if:
    - its exact branch belongs to the path, OR
    - its sub-section belongs to a path sub-section.

    The second rule handles PDFs that list event data as 09-01-2-x but route
    data as another branch in the same subsection.
    """

    branch = normalise_branch(event_branch)

    if not branch:
        return False

    if branch in path:
        return True

    event_subsection = branch_subsection(branch)

    return any(
        branch_subsection(path_branch) == event_subsection
        for path_branch in path
    )


def create_fallback_route_review_rows(
    path: List[str],
) -> pd.DataFrame:
    """
    This prevents the application from generating zero sheets.

    If the uploaded PDF has route connections but no searchable event labels
    such as MTIB, STIB, Signal, T/C, PSD, ESZ, etc., the app creates a Route
    Review Test Sheet for each branch in the selected path.

    These are clearly marked as 'Engineering Review Required' and DO NOT claim
    that LS03, LS04 or any other test applies automatically.
    """

    rows: List[Dict[str, Any]] = []

    for sequence, branch in enumerate(path, start=1):
        rows.append(
            {
                "test_code": GENERIC_ROUTE_REVIEW_CODE,
                "test_id": "ROUTE-REVIEW",
                "test_name": "Route Branch Engineering Review",
                "branch": branch,
                "sector": branch_sector(branch),
                "section": branch_section(branch),
                "subsection": branch_subsection(branch),
                "abscissa_m": None,
                "variant_number": "",
                "event_description": (
                    f"Route branch {sequence} of {len(path)}. "
                    "No searchable singularity/test event was detected "
                    "automatically in the uploaded PDF."
                ),
                "source_page": "",
                "review_status": "Engineering review required",
                "remarks": (
                    "Confirm applicable LS01–LS14 test type using approved "
                    "subdivision, invariant and signalling design data."
                ),
            }
        )

    return pd.DataFrame(rows)


def create_test_plan(
    path: List[str],
    extracted_events: pd.DataFrame,
) -> pd.DataFrame:
    """
    Creates sheets for detected events on the selected path.

    If none are detected, creates Route Review sheets instead of returning
    an empty plan.
    """

    if extracted_events.empty:
        return create_fallback_route_review_rows(path)

    matches = extracted_events[
        extracted_events["branch"]
        .astype(str)
        .apply(lambda value: event_matches_path(value, path))
    ].copy()

    # If test text was found but branch could not be extracted, include those
    # event rows as engineering-review items rather than returning zero sheets.
    branchless_events = extracted_events[
        extracted_events["branch"].astype(str).str.strip().eq("")
    ].copy()

    if not branchless_events.empty:
        branchless_events["branch"] = path[0]
        branchless_events["sector"] = branch_sector(path[0])
        branchless_events["section"] = branch_section(path[0])
        branchless_events["subsection"] = branch_subsection(path[0])
        branchless_events["review_status"] = (
            "Engineering review required"
        )

        matches = pd.concat(
            [matches, branchless_events],
            ignore_index=True,
        )

    if matches.empty:
        return create_fallback_route_review_rows(path)

    matches = matches.drop_duplicates(
        subset=[
            "test_code",
            "branch",
            "abscissa_m",
            "event_description",
        ]
    ).reset_index(drop=True)

    return matches


# =============================================================================
# TEST SHEET CONTENT
# =============================================================================

def test_sheet_checks(
    test_code: str,
) -> List[Tuple[str, str]]:
    """
    Defines basic checks shown in generated DOCX test sheets.
    """

    common_checks = [
        (
            "Approved Design Confirmation",
            "Confirm branch, abscissa, singularity and route against approved design.",
        ),
        (
            "OMAP / Log Evidence",
            "Record actual evidence reference.",
        ),
        (
            "Engineering Result",
            "Pass / Fail / Punchlist.",
        ),
    ]

    check_map: Dict[str, List[Tuple[str, str]]] = {
        "LS01": [
            ("Selected Mode", "RM"),
            ("Localisation Status", "Out Loc → Wait Loc → Total Loc"),
            ("Beacon Code", "Record and compare with expected code."),
            ("Calibration", "Record calibration value."),
        ],
        "LS02": [
            ("Selected Mode", "RM"),
            ("Localisation Status", "Out Loc → Wait Inv → Total Loc"),
            ("Mode Availability", "CM/AM available where required."),
            ("Emergency Brake", "No EB after first RB."),
        ],
        "LS03": [
            ("Track Circuit Occupied", "Associated variant restrictive."),
            ("Track Circuit Free", "Associated variant permissive."),
            ("Target Distance", "Permissive distance ≥ restrictive distance."),
        ],
        "LS04": [
            ("Signal Restrictive", "Associated variant restrictive."),
            ("Signal Permissive", "Associated variant permissive."),
            ("Target Distance", "Permissive distance ≥ restrictive distance."),
        ],
        "LS05": [
            ("Signal / Overlap Occupied", "Associated variants restrictive."),
            ("Signal / Overlap Free", "Associated variants permissive."),
        ],
        "LS06": [
            ("Point Uncontrolled", "Associated variant restrictive."),
            ("Point Controlled", "Associated variant permissive."),
        ],
        "LS07": [
            ("Before End of SACEM", "CM/AM available where applicable."),
            ("Beyond End of SACEM", "CM/AM unavailable."),
        ],
        "LS08": [
            ("Localisation", "Total Loc."),
            ("Door Enable Side", "Correct door side enabled."),
            ("Door Enable Window", "Within ±0.35 m."),
        ],
        "LS09": [
            ("Incorrect Door Side", "PSD/relay does not open."),
            ("Correct Door Side", "PSD/relay opens."),
            ("Door Closed Condition", "Expected mode and variant behaviour."),
        ],
        "LS10": [
            ("ESZ Active", "Associated variant restrictive."),
            ("Inside ESZ", "CM/AM unavailable."),
            ("End of ESZ", "CM/AM becomes available."),
        ],
        "LS11": [
            ("Cab 1", "Total Loc and CM/AM available."),
            ("Cab 2", "CM/AM available within 20 seconds."),
            ("Post Change of End", "No delocalization / no EB."),
        ],
        "LS12": [
            ("Track Circuit", "Record selected track circuit."),
            ("TSR Value", "Record TSR value."),
            ("Train Behaviour", "Target speed follows TSR and no EB."),
        ],
        "LS13": [
            ("Return Channel Loop", "Record location."),
            ("Trackside Reception", "Train information received."),
        ],
        "LS14": [
            ("Start of Shunt", "Record branch, Loc Max and target distance."),
            ("End of Shunt", "Record recovery branch / Loc Max."),
        ],
        GENERIC_ROUTE_REVIEW_CODE: [
            (
                "Route Branch",
                "Confirm this branch is included in selected route.",
            ),
            (
                "Singularity Identification",
                "Identify MTIB, STIB, T/C, signal, point, station, ESZ, CoE, TSR, return channel or End of Shunt if applicable.",
            ),
            (
                "Applicable Procedure Test",
                "Select applicable LS01–LS14 test sheet after engineering review.",
            ),
            (
                "Approved Design Source",
                "Record source drawing, Appendix J, invariant or route table reference.",
            ),
        ],
    }

    return check_map.get(
        test_code,
        [],
    ) + common_checks


# =============================================================================
# DOCX EXPORT
# =============================================================================

def add_table_row(
    table: Any,
    left: str,
    right: str,
) -> None:
    cells = table.add_row().cells
    cells[0].text = left
    cells[1].text = right


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
    start_branch: str,
    end_branch: str,
) -> bytes:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: python-docx. "
            "Install with: pip install python-docx"
        ) from exc

    test_code = safe_text(row.get("test_code"))

    if test_code in TEST_TYPES:
        test_details = TEST_TYPES[test_code]
        test_id = test_details["id"]
        test_name = test_details["name"]
        purpose = test_details["purpose"]
    else:
        test_id = "ROUTE-REVIEW"
        test_name = "Route Branch Engineering Review"
        purpose = (
            "Review the route branch and identify the applicable low-speed "
            "test(s) using the approved project design data."
        )

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.45)
    section.right_margin = Inches(0.45)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run("ALSTOM\n")
    title_run.bold = True
    title_run.font.size = Pt(16)

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    heading_run = heading.add_run(
        f"{test_id} – {test_name}"
    )
    heading_run.bold = True
    heading_run.font.size = Pt(12)

    document.add_paragraph()

    info_table = document.add_table(rows=0, cols=2)
    info_table.style = "Table Grid"

    details = [
        ("Procedure Reference", project_info["procedure_reference"]),
        ("Procedure Title", PROCEDURE_TITLE),
        ("Test Sheet Number", sheet_number),
        ("Project / Contract", project_info["project"]),
        ("Trackside Version", project_info["trackside_version"]),
        ("ATP Version", project_info["atp_version"]),
        ("ATO Version", project_info["ato_version"]),
        ("Selected Start Point", start_branch),
        ("Selected End Point", end_branch),
        ("Branch Number", safe_text(row.get("branch"))),
        ("Sector", safe_text(row.get("sector"))),
        ("Section", safe_text(row.get("section"))),
        ("Sub-section", safe_text(row.get("subsection"))),
        ("Abscissa", f"{safe_text(row.get('abscissa_m'))} m"),
        ("Variant Number", safe_text(row.get("variant_number"))),
        ("Source Page", safe_text(row.get("source_page"))),
    ]

    for label, value in details:
        add_table_row(
            table=info_table,
            left=label,
            right=safe_text(value),
        )

    document.add_paragraph()

    purpose_heading = document.add_paragraph()
    purpose_heading.add_run("Purpose of Test").bold = True

    document.add_paragraph(purpose)

    document.add_paragraph()

    record_heading = document.add_paragraph()
    record_heading.add_run("Test Record").bold = True

    record_table = document.add_table(rows=1, cols=4)
    record_table.style = "Table Grid"

    headers = [
        "Check / Record",
        "Expected Requirement",
        "Actual Result",
        "Pass / Fail",
    ]

    for index, header in enumerate(headers):
        record_table.rows[0].cells[index].text = header

    for check, expected in test_sheet_checks(test_code):
        cells = record_table.add_row().cells
        cells[0].text = check
        cells[1].text = expected
        cells[2].text = ""
        cells[3].text = "☐ Pass  ☐ Fail  ☐ N/A"

    document.add_paragraph()

    notes_heading = document.add_paragraph()
    notes_heading.add_run(
        "Engineering Remarks / Source Evidence"
    ).bold = True

    notes = (
        safe_text(row.get("remarks"))
        or safe_text(row.get("event_description"))
        or "No source evidence extracted."
    )

    document.add_paragraph(notes)

    document.add_paragraph()

    signature_table = document.add_table(rows=3, cols=3)
    signature_table.style = "Table Grid"

    signature_table.rows[0].cells[0].text = "Result"
    signature_table.rows[0].cells[1].text = (
        "☐ Passed    ☐ Failed    ☐ Punchlist Raised"
    )
    signature_table.rows[0].cells[2].text = ""

    signature_table.rows[1].cells[0].text = "Role"
    signature_table.rows[1].cells[1].text = "Name / Signature"
    signature_table.rows[1].cells[2].text = "Date"

    signature_table.rows[2].cells[0].text = "Alstom Test Engineer"
    signature_table.rows[2].cells[1].text = ""
    signature_table.rows[2].cells[2].text = ""

    witness_cells = signature_table.add_row().cells
    witness_cells[0].text = "MTR Witness"
    witness_cells[1].text = ""
    witness_cells[2].text = ""

    document.add_paragraph()

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – Verify approved document revision before use."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(8)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_docx_zip(
    test_plan: pd.DataFrame,
    project_info: Dict[str, str],
    start_branch: str,
    end_branch: str,
) -> bytes:
    output = io.BytesIO()

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for index, (_, row) in enumerate(
            test_plan.iterrows(),
            start=1,
        ):
            test_code = (
                safe_text(row.get("test_code"))
                or GENERIC_ROUTE_REVIEW_CODE
            )

            branch = (
                safe_text(row.get("branch"))
                or "GENERAL"
            )

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
                    start_branch=start_branch,
                    end_branch=end_branch,
                ),
            )

    return output.getvalue()


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
        cover = pd.DataFrame(
            [
                ["Procedure Reference", project_info["procedure_reference"]],
                ["Procedure Title", PROCEDURE_TITLE],
                ["Project / Contract", project_info["project"]],
                ["Trackside Version", project_info["trackside_version"]],
                ["ATP Version", project_info["atp_version"]],
                ["ATO Version", project_info["ato_version"]],
                ["Generated Date", str(date.today())],
                ["Selected Start Point", selected_path[0]],
                ["Selected End Point", selected_path[-1]],
                ["Selected Route", " → ".join(selected_path)],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(
            writer,
            sheet_name="Cover",
            index=False,
        )

        pd.DataFrame(
            {
                "Route Sequence": list(
                    range(1, len(selected_path) + 1)
                ),
                "Branch": selected_path,
            }
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

            for column_cells in worksheet.columns:
                maximum_length = max(
                    len(safe_text(cell.value))
                    for cell in column_cells
                )

                worksheet.column_dimensions[
                    column_cells[0].column_letter
                ].width = min(
                    max(maximum_length + 2, 12),
                    60,
                )

    return output.getvalue()


# =============================================================================
# BUTTON GRID
# =============================================================================

def render_branch_buttons(
    branches: List[str],
    selected_key: str,
    button_prefix: str,
    clear_keys_on_change: Optional[List[str]] = None,
    columns_count: int = 4,
) -> None:
    """
    Renders friendly selectable branch buttons.

    clear_keys_on_change is important:
    when Start Point changes, old End Point and old Path selection are removed.
    """

    columns = st.columns(columns_count)

    for index, branch in enumerate(branches):
        with columns[index % columns_count]:
            selected = (
                st.session_state.get(selected_key) == branch
            )

            label = (
                f"✅ {branch}"
                if selected
                else f"🛤️ {branch}"
            )

            if st.button(
                label,
                key=f"{button_prefix}_{branch}",
                use_container_width=True,
            ):
                previous = st.session_state.get(selected_key)

                st.session_state[selected_key] = branch

                if previous != branch and clear_keys_on_change:
                    for key in clear_keys_on_change:
                        st.session_state.pop(key, None)


# =============================================================================
# MAIN APP
# =============================================================================

def render_test_sheet_generator_app() -> None:
    render_test_sheet_generator_styles()

    st.markdown(
        """
        <div class="tsg-hero">
            <h1>ATC Route-to-Test Sheet Generator</h1>
            <p>
                Upload one route PDF, choose a starting point, choose an
                available final destination, then generate low-speed test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="tsg-warning">
            <b>Engineering control:</b>
            the route path and generated test sheets must be checked against
            the approved sector subdivision, Appendix J, invariant data and
            signalling design before issue or execution.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # PROJECT INFORMATION
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
                key="tsg_project_name",
            )

            trackside_version = st.text_input(
                "Trackside Version",
                value="",
                key="tsg_trackside_version",
            )

        with col2:
            atp_version = st.text_input(
                "ATP Version",
                value="",
                key="tsg_atp_version",
            )

            ato_version = st.text_input(
                "ATO Version",
                value="",
                key="tsg_ato_version",
            )

        with col3:
            procedure_reference = st.text_input(
                "Procedure Reference",
                value=PROCEDURE_REFERENCE,
                key="tsg_procedure_reference",
            )

            sheet_prefix = st.text_input(
                "Test Sheet Prefix",
                value=PROCEDURE_REFERENCE,
                key="tsg_sheet_prefix",
            )

    project_info = {
        "project": project_name,
        "trackside_version": trackside_version,
        "atp_version": atp_version,
        "ato_version": ato_version,
        "procedure_reference": procedure_reference,
        "sheet_prefix": sheet_prefix,
    }

    # -------------------------------------------------------------------------
    # SINGLE PDF UPLOAD
    # -------------------------------------------------------------------------

    st.markdown("## 1. Upload Route / Sector Subdivision PDF")

    st.markdown(
        """
        <div class="tsg-info">
            <b>One PDF only.</b><br>
            Upload the approved Appendix J, ATC Sector Subdivision, route
            transmission list, route diagram, or route-definition PDF.
            <br><br>
            The PDF should include ordered branch sequences, for example:
            <br>
            <code>09-01-1-0, 09-01-2-0, 09-01-3-0, 14-04-1-1</code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded_pdf = st.file_uploader(
        "Upload route-definition PDF",
        type=["pdf"],
        key="tsg_uploaded_route_pdf",
    )

    if uploaded_pdf is None:
        st.info(
            "Upload the route-definition PDF to begin."
        )
        return

    try:
        with st.spinner(
            "Reading PDF, extracting branch connections and finding test events..."
        ):
            page_texts = extract_pdf_text(
                uploaded_pdf.getvalue()
            )

            graph, reverse_graph, evidence = extract_route_graph(
                page_texts
            )

            extracted_events = extract_test_events(
                page_texts
            )

    except Exception as exc:
        st.error(
            f"Unable to process the uploaded PDF: {exc}"
        )
        return

    all_branches = sorted(
        set(graph.keys())
        | set(reverse_graph.keys())
    )

    if not all_branches:
        st.error(
            "No connected branch routes were found in the PDF. "
            "The PDF must contain selectable/OCR text showing ordered branch "
            "numbers in route sequences."
        )

        with st.expander(
            "Show extracted PDF text for troubleshooting"
        ):
            for page_number, page_text in enumerate(
                page_texts[:10],
                start=1,
            ):
                st.text_area(
                    f"Extracted text from PDF page {page_number}",
                    value=page_text[:5000],
                    height=220,
                    disabled=True,
                    key=f"debug_pdf_text_{page_number}",
                )

        return

    # -------------------------------------------------------------------------
    # NETWORK SUMMARY
    # -------------------------------------------------------------------------

    st.markdown("## 2. Route Network Found")

    metric_col1, metric_col2, metric_col3 = st.columns(3)

    metric_col1.metric(
        "Connected Branches",
        len(all_branches),
    )

    metric_col2.metric(
        "Detected Connections",
        len(evidence),
    )

    metric_col3.metric(
        "Detected Test Events",
        len(extracted_events),
    )

    with st.expander(
        "Show extracted branch connections",
        expanded=False,
    ):
        if evidence.empty:
            st.info(
                "No evidence rows are available."
            )
        else:
            st.dataframe(
                evidence,
                use_container_width=True,
                hide_index=True,
            )

    # -------------------------------------------------------------------------
    # START POINT SELECTION
    # -------------------------------------------------------------------------

    st.markdown("## 3. Choose Starting Point")

    st.markdown(
        """
        <div class="tsg-info">
            Click the branch where the train begins.
            <br><br>
            After choosing a start point, the app will calculate only the
            possible <b>final destinations</b>. Intermediate branches will not
            be shown as End Point options.
        </div>
        """,
        unsafe_allow_html=True,
    )

    available_starts = starting_points(graph)

    start_search = st.text_input(
        "Search starting point",
        placeholder="Example: 09-01",
        key="tsg_start_search",
    )

    visible_starts = [
        branch
        for branch in available_starts
        if start_search.lower() in branch.lower()
    ]

    if not visible_starts:
        st.warning(
            "No starting point matches this search."
        )
        return

    render_branch_buttons(
        branches=visible_starts[:150],
        selected_key="tsg_selected_start",
        button_prefix="start",
        clear_keys_on_change=[
            "tsg_selected_end",
            "tsg_path_choice",
            "tsg_generated_plan",
            "tsg_generated_path",
        ],
    )

    selected_start = st.session_state.get(
        "tsg_selected_start",
        "",
    )

    if not selected_start:
        st.info(
            "Please choose one Starting Point."
        )
        return

    st.markdown(
        f"""
        <div class="tsg-success">
            <b>Selected Starting Point:</b> {selected_start}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # END POINT SELECTION
    # -------------------------------------------------------------------------

    st.markdown("## 4. Choose Available End Point")

    available_ends = reachable_endpoints(
        graph=graph,
        start_branch=selected_start,
    )

    if not available_ends:
        st.warning(
            "No final End Point could be found from this Starting Point. "
            "The route data may stop at an intermediate branch, or the PDF "
            "does not include the full route sequence."
        )

        with st.expander(
            "Technical route information"
        ):
            st.write(
                "Direct next branches from selected start:"
            )
            st.write(
                sorted(graph.get(selected_start, set()))
            )

        return

    st.markdown(
        """
        <div class="tsg-info">
            <b>Only final destinations are shown below.</b><br>
            The app hides middle/intermediate branches automatically.
            Choose where the train finishes.
        </div>
        """,
        unsafe_allow_html=True,
    )

    end_search = st.text_input(
        "Search available final end point",
        placeholder="Example: 14-04",
        key="tsg_end_search",
    )

    visible_ends = [
        branch
        for branch in available_ends
        if end_search.lower() in branch.lower()
    ]

    if not visible_ends:
        st.warning(
            "No final End Point matches this search."
        )
        return

    render_branch_buttons(
        branches=visible_ends[:150],
        selected_key="tsg_selected_end",
        button_prefix="end",
        clear_keys_on_change=[
            "tsg_path_choice",
            "tsg_generated_plan",
            "tsg_generated_path",
        ],
    )

    selected_end = st.session_state.get(
        "tsg_selected_end",
        "",
    )

    if not selected_end:
        st.info(
            "Please choose one available End Point."
        )
        return

    if selected_end not in available_ends:
        st.error(
            "Selected End Point is not reachable from selected Starting Point."
        )
        return

    st.markdown(
        f"""
        <div class="tsg-success">
            <b>Selected Route:</b> {selected_start} → {selected_end}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -------------------------------------------------------------------------
    # PATH IDENTIFICATION
    # -------------------------------------------------------------------------

    st.markdown("## 5. Confirm Route Path")

    paths = find_paths(
        graph=graph,
        start_branch=selected_start,
        end_branch=selected_end,
    )

    if not paths:
        st.error(
            "No complete path could be found between the selected start and "
            "end point."
        )
        return

    if len(paths) == 1:
        selected_path = paths[0]

        st.success(
            "One valid route path was found automatically."
        )

    else:
        path_labels = [
            f"Route Option {index} — {len(path)} branches"
            for index, path in enumerate(paths, start=1)
        ]

        selected_path_label = st.radio(
            "More than one physical path is possible. Choose the correct route.",
            options=path_labels,
            key="tsg_path_choice",
        )

        selected_path = paths[
            path_labels.index(selected_path_label)
        ]

    # IMPORTANT:
    # components.html() renders SVG properly.
    # st.markdown() may show raw SVG code instead.
    components.html(
        build_path_diagram(
            path=selected_path,
            start_branch=selected_start,
            end_branch=selected_end,
        ),
        height=285,
        scrolling=True,
    )

    st.caption(
        f"Selected route contains {len(selected_path)} branch(es)."
    )

    with st.expander(
        "Show technical branch sequence",
        expanded=False,
    ):
        st.dataframe(
            pd.DataFrame(
                {
                    "Route Sequence": list(
                        range(1, len(selected_path) + 1)
                    ),
                    "Branch": selected_path,
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    # -------------------------------------------------------------------------
    # TEST SHEET GENERATION
    # -------------------------------------------------------------------------

    st.markdown("## 6. Generate Test Sheets")

    st.markdown(
        """
        <div class="tsg-info">
            Click the button below to generate the test plan.
            <br><br>
            If the PDF contains searchable test labels such as MTIB, STIB,
            signal, track circuit, point, station, PSD, ESZ, CoE, TSR,
            return channel or End of Shunt, the app generates matching
            LS01–LS14 test sheets.
            <br><br>
            If no test label can be found, the app generates Route Branch
            Engineering Review sheets instead of showing zero test sheets.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "✨ Generate Test Sheets for Selected Route",
        type="primary",
        use_container_width=True,
        key="tsg_generate_sheets",
    ):
        generated_plan = create_test_plan(
            path=selected_path,
            extracted_events=extracted_events,
        )

        st.session_state["tsg_generated_plan"] = generated_plan
        st.session_state["tsg_generated_path"] = selected_path
        st.session_state["tsg_generated_start"] = selected_start
        st.session_state["tsg_generated_end"] = selected_end

    generated_plan = st.session_state.get(
        "tsg_generated_plan",
        pd.DataFrame(),
    )

    generated_path = st.session_state.get(
        "tsg_generated_path",
        [],
    )

    generated_start = st.session_state.get(
        "tsg_generated_start",
        "",
    )

    generated_end = st.session_state.get(
        "tsg_generated_end",
        "",
    )

    if generated_plan.empty or not generated_path:
        st.info(
            "Click 'Generate Test Sheets for Selected Route' to continue."
        )
        return

    # -------------------------------------------------------------------------
    # PLAN REVIEW
    # -------------------------------------------------------------------------

    st.markdown("## 7. Review Generated Test Sheets")

    if (
        generated_plan["test_code"]
        .astype(str)
        .eq(GENERIC_ROUTE_REVIEW_CODE)
        .all()
    ):
        st.warning(
            "No searchable LS01–LS14 event labels were found in the selected "
            "route PDF. Route Review sheets were created instead. "
            "Please update the Test Code column after engineering review."
        )

    reviewed_plan = st.data_editor(
        generated_plan,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="tsg_plan_editor",
        column_config={
            "test_code": st.column_config.SelectboxColumn(
                "Test Code",
                options=[
                    GENERIC_ROUTE_REVIEW_CODE,
                ] + list(TEST_TYPES.keys()),
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
            "abscissa_m": st.column_config.NumberColumn(
                "Abscissa (m)",
                format="%.3f",
            ),
        },
    )

    # Update test names after user changes the Test Code.
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

        elif test_code == GENERIC_ROUTE_REVIEW_CODE:
            reviewed_plan.at[
                row_index,
                "test_id",
            ] = GENERIC_ROUTE_REVIEW_CODE

            reviewed_plan.at[
                row_index,
                "test_name",
            ] = "Route Branch Engineering Review"

    total_sheets = len(reviewed_plan)

    reviewed_sheets = (
        reviewed_plan["review_status"]
        .astype(str)
        .str.contains(
            "reviewed|approved",
            case=False,
            regex=True,
            na=False,
        )
        .sum()
    )

    summary_col1, summary_col2, summary_col3 = st.columns(3)

    summary_col1.metric(
        "Generated Test Sheets",
        total_sheets,
    )

    summary_col2.metric(
        "Reviewed / Approved",
        int(reviewed_sheets),
    )

    summary_col3.metric(
        "Selected Route Length",
        f"{len(generated_path)} branches",
    )

    # -------------------------------------------------------------------------
    # DOWNLOADS
    # -------------------------------------------------------------------------

    st.markdown("## 8. Download Test Sheets")

    download_col1, download_col2, download_col3 = st.columns(3)

    with download_col1:
        excel_bytes = create_excel_bytes(
            test_plan=reviewed_plan,
            selected_path=generated_path,
            project_info=project_info,
        )

        st.download_button(
            "⬇️ Download Excel Register",
            data=excel_bytes,
            file_name=(
                f"{sheet_prefix}_"
                f"{generated_start}_TO_{generated_end}_"
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
            data=reviewed_plan.to_csv(
                index=False
            ).encode("utf-8"),
            file_name=(
                f"{sheet_prefix}_"
                f"{generated_start}_TO_{generated_end}_"
                f"Test_Plan.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with download_col3:
        try:
            docx_zip = create_docx_zip(
                test_plan=reviewed_plan,
                project_info=project_info,
                start_branch=generated_start,
                end_branch=generated_end,
            )

            st.download_button(
                "⬇️ Download DOCX Test Sheets",
                data=docx_zip,
                file_name=(
                    f"{sheet_prefix}_"
                    f"{generated_start}_TO_{generated_end}_"
                    f"Test_Sheets.zip"
                ),
                mime="application/zip",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(
                f"DOCX generation failed: {exc}"
            )
