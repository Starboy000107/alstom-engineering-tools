from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


# =============================================================================
# CONSTANTS
# =============================================================================

REGISTER_COLUMNS = [
    "test_code",
    "test_name",
    "needs_abscissa",
    "sector",
    "section",
    "subsection",
    "branch",
    "direction",
    "participate_abscissa_m",
    "participate_variant",
    "anticipate_variant",
    "anticipate_abscissa_m",
    "location_name",
    "event_name",
    "remarks",
    "source_row",
    "review_status",
    "checks",
]


# =============================================================================
# BASIC HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    """Safely convert a value to stripped text."""
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def normalise_spaces(value: Any) -> str:
    """Replace repeated spaces/newlines with one space."""
    return re.sub(r"\s+", " ", safe_text(value)).strip()


def clean_filename(value: str) -> str:
    """Create a safe filename."""
    value = safe_text(value)
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", "_", value)
    return value[:150] or "ATC_Test_Sheet"


def to_number(value: Any) -> Optional[float]:
    """Convert a KP/abscissa value to float where possible."""
    text = safe_text(value)

    if not text:
        return None

    text = text.replace(",", "").replace(" ", "")

    try:
        return float(text)
    except ValueError:
        return None


def to_bool(value: Any) -> bool:
    """Convert Excel-style Yes/No, True/False values to bool."""
    return safe_text(value).upper() in {
        "YES",
        "Y",
        "TRUE",
        "T",
        "1",
        "REQUIRED",
    }


def normalise_branch(value: Any) -> str:
    """
    Normalise branch numbers.

    Examples:
        9-1-1-0       -> 09-01-1-0
        09-01-1-0     -> 09-01-1-0
        9 - 1 - 1 - 0 -> 09-01-1-0
    """
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
    """Return Sector, Section and Subsection from a branch string."""
    parts = safe_text(branch).split("-")

    sector = parts[0] if len(parts) >= 1 else ""
    section = parts[1] if len(parts) >= 2 else ""
    subsection = "-".join(parts[:3]) if len(parts) >= 3 else ""

    return sector, section, subsection


# =============================================================================
# INPUT WORKBOOK COLUMN ALIASES
# =============================================================================

# The uploaded Subdivision worksheet may have different column names.
# These aliases allow the application to recognise common variations.
COLUMN_ALIASES: Dict[str, List[str]] = {
    "kp": [
        "kp",
        "k.p.",
        "k.p",
        "k p",
        "abscissa",
        "abscissa_m",
        "abscissa (m)",
        "chainage",
        "chainage_m",
        "chainage (m)",
    ],
    "branch": [
        "branch",
        "branch number",
        "branch_number",
        "branch no",
        "branch_no",
    ],
    "sector": [
        "sector",
    ],
    "section": [
        "section",
    ],
    "subsection": [
        "subsection",
        "sub section",
        "sub-section",
        "sub_section",
    ],
    "event_name": [
        "event_name",
        "event name",
        "name",
        "singularity",
        "singularity name",
        "singularity_name",
        "event",
        "type",
    ],
    "remarks": [
        "remarks",
        "remark",
        "description",
        "details",
        "singularities",
        "comment",
        "comments",
    ],
    "variant_number": [
        "variant number",
        "variant_number",
        "variant",
        "variant no",
        "variant_no",
        "variant rank",
        "rank",
    ],
    "participate_variant": [
        "participate variant",
        "participate_variant",
        "participating variant",
        "participating_variant",
        "associated variant",
        "associated_variant",
    ],
    "anticipate_variant": [
        "anticipate variant",
        "anticipate_variant",
        "anticipated variant",
        "anticipated_variant",
    ],
    "anticipate_abscissa_m": [
        "anticipate abscissa",
        "anticipate_abscissa",
        "anticipate abscissa m",
        "anticipate_abscissa_m",
        "anticipated abscissa",
        "anticipated_abscissa",
        "anticipated abscissa m",
        "anticipated_abscissa_m",
    ],
    "location_name": [
        "location",
        "location name",
        "location_name",
        "asset",
        "asset name",
        "asset_name",
        "signal",
        "track circuit",
        "track_circuit",
    ],
    "direction": [
        "direction",
        "track direction",
        "track_direction",
        "normal direction",
        "normal_direction",
    ],
}


# =============================================================================
# WORKBOOK READING AND NORMALISATION
# =============================================================================

def find_column(
    dataframe: pd.DataFrame,
    aliases: List[str],
) -> Optional[str]:
    """Find an actual dataframe column using a list of aliases."""
    lookup = {
        normalise_spaces(column).lower(): column
        for column in dataframe.columns
    }

    for alias in aliases:
        if alias.lower() in lookup:
            return lookup[alias.lower()]

    return None


def standardise_subdivision_dataframe(
    raw_dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert uploaded Subdivision data to the internal standard structure.

    Required practical information:
        - KP / Abscissa
        - Event Name, Name or Remarks
        - Branch is strongly recommended

    The application will populate Sector, Section and Subsection from the
    Branch Number if they are not separately provided.
    """
    output = pd.DataFrame(index=raw_dataframe.index)

    for standard_column, aliases in COLUMN_ALIASES.items():
        actual_column = find_column(raw_dataframe, aliases)

        if actual_column:
            output[standard_column] = raw_dataframe[actual_column]
        else:
            output[standard_column] = ""

    output["kp"] = output["kp"].apply(to_number)
    output["anticipate_abscissa_m"] = (
        output["anticipate_abscissa_m"].apply(to_number)
    )

    output["branch"] = output["branch"].apply(normalise_branch)

    for index in output.index:
        branch = safe_text(output.at[index, "branch"])

        branch_sector, branch_section, branch_subsection = (
            branch_parts(branch)
        )

        if not safe_text(output.at[index, "sector"]):
            output.at[index, "sector"] = branch_sector

        if not safe_text(output.at[index, "section"]):
            output.at[index, "section"] = branch_section

        if not safe_text(output.at[index, "subsection"]):
            output.at[index, "subsection"] = branch_subsection

    for column in output.columns:
        if column not in {"kp", "anticipate_abscissa_m"}:
            output[column] = output[column].apply(safe_text)

    # Excel header is normally row 1; dataframe row zero represents Excel row 2.
    output["source_row"] = raw_dataframe.index + 2

    return output


def standardise_mapping_dataframe(
    raw_dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Standardise TestMapping worksheet.

    Required TestMapping columns:
        event_pattern
        test_code
        test_name
        needs_abscissa
        include_anticipate

    Optional:
        checks
    """
    required_columns = [
        "event_pattern",
        "test_code",
        "test_name",
        "needs_abscissa",
        "include_anticipate",
    ]

    lookup = {
        normalise_spaces(column).lower(): column
        for column in raw_dataframe.columns
    }

    missing = [
        required
        for required in required_columns
        if required.lower() not in lookup
    ]

    if missing:
        raise ValueError(
            "The TestMapping worksheet is missing required columns: "
            + ", ".join(missing)
        )

    mapping = pd.DataFrame()

    for column in required_columns + ["checks"]:
        actual_column = lookup.get(column.lower())

        if actual_column:
            mapping[column] = raw_dataframe[actual_column]
        else:
            mapping[column] = ""

    mapping["event_pattern"] = mapping["event_pattern"].apply(safe_text)
    mapping["test_code"] = mapping["test_code"].apply(safe_text)
    mapping["test_name"] = mapping["test_name"].apply(safe_text)
    mapping["checks"] = mapping["checks"].apply(safe_text)

    mapping["needs_abscissa"] = (
        mapping["needs_abscissa"].apply(to_bool)
    )

    mapping["include_anticipate"] = (
        mapping["include_anticipate"].apply(to_bool)
    )

    mapping = mapping[
        mapping["event_pattern"].astype(str).str.strip().ne("")
    ].copy()

    return mapping.reset_index(drop=True)


def read_project_information(
    workbook_sheets: Dict[str, pd.DataFrame],
) -> Dict[str, str]:
    """
    Read optional Project worksheet.

    Expected structure:
        Field | Value
    """
    project_info = {
        "procedure_reference": "C1252-TAC-XXXX",
        "procedure_title": "ATC Dynamic Test Procedure",
        "project": "",
        "trackside_version": "",
        "atp_version": "",
        "ato_version": "",
        "sheet_prefix": "ATC-TEST",
    }

    project_sheet_name = None

    for sheet_name in workbook_sheets:
        if sheet_name.strip().lower() == "project":
            project_sheet_name = sheet_name
            break

    if not project_sheet_name:
        return project_info

    project_dataframe = workbook_sheets[project_sheet_name].copy()

    if (
        "Field" not in project_dataframe.columns
        or "Value" not in project_dataframe.columns
    ):
        return project_info

    project_key_map = {
        "procedure reference": "procedure_reference",
        "procedure title": "procedure_title",
        "project": "project",
        "project / contract": "project",
        "trackside version": "trackside_version",
        "atp version": "atp_version",
        "ato version": "ato_version",
        "test sheet prefix": "sheet_prefix",
    }

    for _, row in project_dataframe.iterrows():
        field = safe_text(row.get("Field")).lower()
        value = safe_text(row.get("Value"))

        if field in project_key_map:
            project_info[project_key_map[field]] = value

    if not project_info["sheet_prefix"]:
        project_info["sheet_prefix"] = (
            project_info["procedure_reference"]
        )

    return project_info


def find_sheet_name(
    workbook_sheets: Dict[str, pd.DataFrame],
    expected_name: str,
) -> Optional[str]:
    """Find a worksheet name without case sensitivity."""
    for sheet_name in workbook_sheets:
        if sheet_name.strip().lower() == expected_name.lower():
            return sheet_name

    return None


# =============================================================================
# PARTICIPATING / ANTICIPATED VARIANT RESOLUTION
# =============================================================================

def extract_variant_from_remarks(
    remarks: str,
    labels: List[str],
) -> str:
    """
    Extract a variant reference from free text.

    Examples supported:
        Associated variant: (TUC KP T/C)
        Participate variant: 7
        Participating Variant = TC 2450
        Anticipate variant: 14-07-1
        Anticipated Variant: 5

    This is a helper only. Dedicated Excel columns are always preferred.
    """
    text = safe_text(remarks)

    if not text:
        return ""

    for label in labels:
        pattern = (
            rf"(?:{label})"
            r"(?:\s+(?:variant|variants|number))?"
            r"\s*[:=]?\s*"
            r"\(?([^()\n;|]+?)\)?"
            r"(?=$|;|\||\n|"
            r",\s*(?:associated|participate|participating|"
            r"anticipate|anticipated|number of|rank of|sub section))"
        )

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return normalise_spaces(match.group(1))

    return ""


def build_variant_index(
    subdivision: pd.DataFrame,
) -> Dict[str, List[pd.Series]]:
    """
    Build an index of variant number -> relevant subdivision rows.

    This lets the generator estimate anticipated abscissa where:
        - an anticipated variant is provided; and
        - another subdivision row contains that variant number and a KP.
    """
    variant_index: Dict[str, List[pd.Series]] = {}

    for _, row in subdivision.iterrows():
        variant = safe_text(row.get("variant_number"))

        if not variant:
            continue

        key = variant.upper()
        variant_index.setdefault(key, []).append(row)

    return variant_index


def variant_candidates(value: str) -> List[str]:
    """
    Produce possible index keys for a variant value.

    Example:
        'Variant 7' -> ['VARIANT 7', '7']
        '7'         -> ['7']
    """
    text = safe_text(value).upper()

    if not text:
        return []

    candidates = [text]

    numeric_match = re.search(r"\b(\d+)\b", text)

    if numeric_match:
        candidates.append(numeric_match.group(1))

    return list(dict.fromkeys(candidates))


def resolve_variant_abscissa(
    variant_value: str,
    variant_index: Dict[str, List[pd.Series]],
    reference_kp: Optional[float],
) -> Optional[float]:
    """
    Locate the closest known abscissa/KP for a variant.

    If more than one source row has the same variant number, the row closest
    to the participating event abscissa is selected.
    """
    candidate_rows: List[pd.Series] = []

    for candidate in variant_candidates(variant_value):
        candidate_rows.extend(
            variant_index.get(candidate, [])
        )

    candidate_rows = [
        row
        for row in candidate_rows
        if pd.notna(row.get("kp"))
    ]

    if not candidate_rows:
        return None

    if reference_kp is None:
        return float(candidate_rows[0]["kp"])

    closest_row = min(
        candidate_rows,
        key=lambda row: abs(
            float(row["kp"]) - float(reference_kp)
        ),
    )

    return float(closest_row["kp"])


# =============================================================================
# TEST MATCHING
# =============================================================================

def build_searchable_event_text(row: pd.Series) -> str:
    """Build text used to match TestMapping regular-expression rules."""
    return " ".join(
        [
            safe_text(row.get("event_name")),
            safe_text(row.get("remarks")),
            safe_text(row.get("location_name")),
            safe_text(row.get("participate_variant")),
            safe_text(row.get("anticipate_variant")),
        ]
    )


def create_test_records_for_event(
    row: pd.Series,
    mapping: pd.DataFrame,
    variant_index: Dict[str, List[pd.Series]],
) -> List[Dict[str, Any]]:
    """
    Create zero, one or multiple test records for a subdivision event.

    Test generation depends entirely on TestMapping worksheet rules.
    There is no Python hard-coding of LS01, LS02, LS03 etc.
    """
    output: List[Dict[str, Any]] = []

    searchable_text = build_searchable_event_text(row)

    source_kp = row.get("kp")

    participate_variant = safe_text(
        row.get("participate_variant")
    )

    anticipate_variant = safe_text(
        row.get("anticipate_variant")
    )

    remarks = safe_text(row.get("remarks"))

    if not participate_variant:
        participate_variant = extract_variant_from_remarks(
            remarks,
            labels=[
                "associated",
                "participate",
                "participating",
            ],
        )

    if not anticipate_variant:
        anticipate_variant = extract_variant_from_remarks(
            remarks,
            labels=[
                "anticipate",
                "anticipated",
            ],
        )

    explicit_anticipate_abs = row.get("anticipate_abscissa_m")

    for _, rule in mapping.iterrows():
        event_pattern = safe_text(rule.get("event_pattern"))

        try:
            pattern_match = re.search(
                event_pattern,
                searchable_text,
                flags=re.IGNORECASE,
            )
        except re.error as exc:
            raise ValueError(
                f"Invalid regular expression in TestMapping: "
                f"'{event_pattern}'. Error: {exc}"
            ) from exc

        if not pattern_match:
            continue

        needs_abscissa = bool(rule.get("needs_abscissa"))
        include_anticipate = bool(rule.get("include_anticipate"))

        # Participating abscissa is only populated where the mapping says
        # the test requires abscissa.
        if needs_abscissa and pd.notna(source_kp):
            participate_abscissa = float(source_kp)
        else:
            participate_abscissa = None

        anticipate_abscissa: Optional[float] = None

        if include_anticipate and anticipate_variant:
            if pd.notna(explicit_anticipate_abs):
                anticipate_abscissa = float(
                    explicit_anticipate_abs
                )
            else:
                anticipate_abscissa = resolve_variant_abscissa(
                    variant_value=anticipate_variant,
                    variant_index=variant_index,
                    reference_kp=participate_abscissa,
                )

        output.append(
            {
                "test_code": safe_text(rule.get("test_code")),
                "test_name": safe_text(rule.get("test_name")),
                "needs_abscissa": needs_abscissa,
                "sector": safe_text(row.get("sector")),
                "section": safe_text(row.get("section")),
                "subsection": safe_text(row.get("subsection")),
                "branch": safe_text(row.get("branch")),
                "direction": safe_text(row.get("direction")),
                "participate_abscissa_m": participate_abscissa,
                "participate_variant": participate_variant,
                "anticipate_variant": (
                    anticipate_variant
                    if include_anticipate
                    else ""
                ),
                "anticipate_abscissa_m": anticipate_abscissa,
                "location_name": safe_text(
                    row.get("location_name")
                ),
                "event_name": safe_text(row.get("event_name")),
                "remarks": remarks,
                "source_row": safe_text(row.get("source_row")),
                "review_status": "To be reviewed",
                "checks": safe_text(rule.get("checks")),
            }
        )

    return output


def create_test_register(
    subdivision: pd.DataFrame,
    mapping: pd.DataFrame,
    start_kp: float,
    end_kp: float,
    selected_branch: str = "",
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Select all source events between start and end KP and create test sheets.

    Returns:
        test_register
        selected_subdivision_events
    """
    lower_kp = min(start_kp, end_kp)
    upper_kp = max(start_kp, end_kp)

    selected_events = subdivision[
        subdivision["kp"].notna()
        & subdivision["kp"].between(
            lower_kp,
            upper_kp,
            inclusive="both",
        )
    ].copy()

    if selected_branch:
        selected_events = selected_events[
            selected_events["branch"].eq(selected_branch)
        ].copy()

    selected_events = selected_events.sort_values(
        by=["branch", "kp"],
        na_position="last",
    ).reset_index(drop=True)

    variant_index = build_variant_index(subdivision)

    records: List[Dict[str, Any]] = []

    for _, event_row in selected_events.iterrows():
        records.extend(
            create_test_records_for_event(
                row=event_row,
                mapping=mapping,
                variant_index=variant_index,
            )
        )

    if not records:
        return (
            pd.DataFrame(columns=REGISTER_COLUMNS),
            selected_events,
        )

    register = pd.DataFrame(records)

    register = register.drop_duplicates(
        subset=[
            "test_code",
            "branch",
            "participate_abscissa_m",
            "participate_variant",
            "anticipate_variant",
            "event_name",
        ]
    ).reset_index(drop=True)

    register = register.sort_values(
        by=[
            "branch",
            "participate_abscissa_m",
            "test_code",
        ],
        na_position="last",
    ).reset_index(drop=True)

    return register, selected_events


# =============================================================================
# DOCX TEST SHEET GENERATION
# =============================================================================

def set_cell_text(
    cell: Any,
    text: str,
    bold: bool = False,
) -> None:
    """Set Word table cell content."""
    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(text))
    run.bold = bold


def add_table_row(
    table: Any,
    values: List[str],
    bold: bool = False,
) -> None:
    """Add a row to a python-docx table."""
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(
            cells[index],
            value,
            bold=bold,
        )


def checks_from_row(row: pd.Series) -> List[str]:
    """
    Obtain checks from the TestMapping worksheet.

    Checks must use pipe separators in Excel:

        Track circuit occupied|Record Loc Max|Confirm target distance
    """
    configured_checks = safe_text(row.get("checks"))

    if configured_checks:
        return [
            normalise_spaces(item)
            for item in configured_checks.split("|")
            if normalise_spaces(item)
        ]

    return [
        "Confirm applicable approved engineering source.",
        "Confirm participating variant condition.",
        "Record OMAP, wayside log, screenshot or test evidence.",
    ]


def format_abscissa(value: Any) -> str:
    """Format abscissa for display."""
    numeric = to_number(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> bytes:
    """
    Generate one generic configurable DOCX test sheet.

    The generated test title, abscissa requirement, anticipated information
    and test actions all originate from the workbook data and mapping rules.
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: python-docx. "
            "Install using: pip install python-docx"
        ) from exc

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

    header_run = header.add_run(
        safe_text(project_info["procedure_title"])
    )
    header_run.bold = True
    header_run.font.size = Pt(13)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{safe_text(row.get('test_code'))} – "
        f"{safe_text(row.get('test_name'))}"
    )
    title_run.bold = True
    title_run.font.size = Pt(11)

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Project information
    # -------------------------------------------------------------------------
    info_table = document.add_table(rows=0, cols=4)
    info_table.style = "Table Grid"

    add_table_row(
        info_table,
        [
            "Procedure Reference",
            project_info["procedure_reference"],
            "Test Sheet Number",
            sheet_number,
        ],
    )

    add_table_row(
        info_table,
        [
            "Project / Contract",
            project_info["project"],
            "Trackside Version",
            project_info["trackside_version"],
        ],
    )

    add_table_row(
        info_table,
        [
            "ATP Version",
            project_info["atp_version"],
            "ATO Version",
            project_info["ato_version"],
        ],
    )

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Location and variant information
    # -------------------------------------------------------------------------
    location_table = document.add_table(rows=0, cols=4)
    location_table.style = "Table Grid"

    add_table_row(
        location_table,
        [
            "Sector",
            safe_text(row.get("sector")),
            "Branch Number",
            safe_text(row.get("branch")),
        ],
    )

    add_table_row(
        location_table,
        [
            "Section / Subsection",
            (
                f"{safe_text(row.get('section'))} / "
                f"{safe_text(row.get('subsection'))}"
            ),
            "Direction",
            safe_text(row.get("direction")),
        ],
    )

    if bool(row.get("needs_abscissa")):
        add_table_row(
            location_table,
            [
                "Participating Abscissa",
                format_abscissa(
                    row.get("participate_abscissa_m")
                ),
                "Participating Variant",
                safe_text(row.get("participate_variant")),
            ],
        )
    else:
        add_table_row(
            location_table,
            [
                "Participating Variant",
                safe_text(row.get("participate_variant")),
                "Abscissa",
                "Not required for this test",
            ],
        )

    if safe_text(row.get("anticipate_variant")):
        add_table_row(
            location_table,
            [
                "Anticipated Variant",
                safe_text(row.get("anticipate_variant")),
                "Anticipated Abscissa",
                (
                    format_abscissa(
                        row.get("anticipate_abscissa_m")
                    )
                    or "To be verified"
                ),
            ],
        )

    add_table_row(
        location_table,
        [
            "Location / Asset",
            safe_text(row.get("location_name")),
            "Source Event",
            safe_text(row.get("event_name")),
        ],
    )

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Configurable test checks
    # -------------------------------------------------------------------------
    test_table = document.add_table(rows=1, cols=4)
    test_table.style = "Table Grid"

    headers = [
        "Test Action / Check",
        "Expected Result",
        "Actual Result / Evidence",
        "OK?",
    ]

    for index, header_text in enumerate(headers):
        set_cell_text(
            test_table.rows[0].cells[index],
            header_text,
            bold=True,
        )

    for check in checks_from_row(row):
        add_table_row(
            test_table,
            [
                check,
                "Compliant with approved design and procedure.",
                "",
                "☐ Y   ☐ N   ☐ N/A",
            ],
        )

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Notes
    # -------------------------------------------------------------------------
    notes_title = document.add_paragraph()
    notes_title.add_run(
        "Engineering Notes / Source Evidence"
    ).bold = True

    notes_table = document.add_table(rows=1, cols=1)
    notes_table.style = "Table Grid"

    notes = (
        f"Source row: {safe_text(row.get('source_row'))}\n"
        f"Source event: {safe_text(row.get('event_name'))}\n"
        f"Subdivision remarks: {safe_text(row.get('remarks'))}\n\n"
        "Important: this test sheet is automatically generated. "
        "Participating variant, anticipated variant, abscissa, "
        "test applicability and acceptance criteria must be checked "
        "against approved project design, PAT, invariant, signalling "
        "and test procedure documents before issue."
    )

    set_cell_text(
        notes_table.rows[0].cells[0],
        notes,
    )

    document.add_paragraph()

    # -------------------------------------------------------------------------
    # Approval
    # -------------------------------------------------------------------------
    approval_table = document.add_table(rows=0, cols=3)
    approval_table.style = "Table Grid"

    add_table_row(
        approval_table,
        [
            "Test Result",
            "☐ Passed",
            "☐ Failed",
        ],
    )

    add_table_row(
        approval_table,
        [
            "Name",
            "Alstom / Contractor",
            "MTR / Client",
        ],
    )

    add_table_row(
        approval_table,
        [
            "Signature",
            "",
            "",
        ],
    )

    add_table_row(
        approval_table,
        [
            "Date",
            "",
            "",
        ],
    )

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run(
        "UNCONTROLLED WHEN PRINTED – "
        "Verify approved engineering data before use."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(7)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_docx_zip(
    register: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
    """Create a ZIP archive containing all generated DOCX test sheets."""
    output = io.BytesIO()

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for sequence, (_, row) in enumerate(
            register.iterrows(),
            start=1,
        ):
            sheet_number = (
                f"{project_info['sheet_prefix']}-{sequence:03d}"
            )

            test_code = safe_text(row.get("test_code")) or "NO_CODE"
            branch = safe_text(row.get("branch")) or "NO_BRANCH"

            abscissa = safe_text(
                row.get("participate_abscissa_m")
            ) or "NO_ABSCISSA"

            filename = clean_filename(
                f"{sheet_number}_{test_code}_{branch}_KP_{abscissa}.docx"
            )

            docx_bytes = create_docx_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            archive.writestr(filename, docx_bytes)

    return output.getvalue()


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    selected_events: pd.DataFrame,
    project_info: Dict[str, str],
) -> bytes:
    """Create Excel register export."""
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

        selected_events.to_excel(
            writer,
            sheet_name="Selected Source Events",
            index=False,
        )

        workbook = writer.book

        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                max_length = max(
                    len(safe_text(cell.value))
                    for cell in column_cells
                )

                worksheet.column_dimensions[
                    column_cells[0].column_letter
                ].width = min(
                    max(max_length + 2, 12),
                    65,
                )

    return output.getvalue()


# =============================================================================
# INPUT TEMPLATE
# =============================================================================

def create_input_template() -> bytes:
    """
    Create a downloadable workbook template.

    The TestMapping sheet defines all test rules. You can add new test
    codes, names, event patterns and checks without editing generator.py.
    """
    output = io.BytesIO()

    subdivision_example = pd.DataFrame(
        [
            {
                "kp": 1175.5,
                "branch": "09-01-1-0",
                "sector": "09",
                "section": "01",
                "subsection": "09-01-1",
                "event_name": "Sub Section Heading",
                "remarks": (
                    "Associated variant: TUC KP T/C; "
                    "Anticipate variant: 14-07-1"
                ),
                "variant_number": "1",
                "participate_variant": "TUC KP T/C",
                "anticipate_variant": "14-07-1",
                "anticipate_abscissa_m": "",
                "location_name": "",
                "direction": "Up",
            },
            {
                "kp": 1198.0,
                "branch": "09-01-1-0",
                "sector": "09",
                "section": "01",
                "subsection": "09-01-1",
                "event_name": "Spacing Stopping Point",
                "remarks": "Associated variant: TUC KP T/C",
                "variant_number": "5",
                "participate_variant": "TUC KP T/C",
                "anticipate_variant": "14-07-1",
                "anticipate_abscissa_m": 1437.1,
                "location_name": "TC 2450",
                "direction": "Up",
            },
            {
                "kp": 1531.5,
                "branch": "09-01-1-0",
                "sector": "09",
                "section": "01",
                "subsection": "09-01-1",
                "event_name": "Return Channel",
                "remarks": "Return channel number: 1",
                "variant_number": "15-3",
                "participate_variant": "",
                "anticipate_variant": "",
                "anticipate_abscissa_m": "",
                "location_name": "Return Channel 1",
                "direction": "Up",
            },
        ]
    )

    mapping_example = pd.DataFrame(
        [
            {
                "event_pattern": "SPACING STOPPING POINT",
                "test_code": "LS03",
                "test_name": (
                    "Spacing Stopping Point Test – Track Circuit"
                ),
                "needs_abscissa": "Yes",
                "include_anticipate": "Yes",
                "checks": (
                    "Track circuit occupied|"
                    "Available mode CM/AUTO|"
                    "Record Loc Max|"
                    "Record target distance|"
                    "Confirm OMAP target speed|"
                    "Track circuit free|"
                    "Confirm permissive target distance"
                ),
            },
            {
                "event_pattern": "SPECIAL STOPPING POINT",
                "test_code": "LS04",
                "test_name": (
                    "Special Stopping Point Test – Signal Without Overlap"
                ),
                "needs_abscissa": "Yes",
                "include_anticipate": "Yes",
                "checks": (
                    "Set restrictive signal condition|"
                    "Confirm CM/AUTO availability|"
                    "Record Loc Max and target distance|"
                    "Set permissive signal condition|"
                    "Confirm target-distance comparison"
                ),
            },
            {
                "event_pattern": "STATION",
                "test_code": "LS08",
                "test_name": (
                    "Station Stopping Point and Door Side Verification"
                ),
                "needs_abscissa": "Yes",
                "include_anticipate": "No",
                "checks": (
                    "Confirm Total Localisation|"
                    "Confirm CM mode|"
                    "Record precise stopping distance|"
                    "Confirm correct platform door side|"
                    "Confirm door enable tolerance"
                ),
            },
            {
                "event_pattern": "RETURN CHANNEL",
                "test_code": "LS13",
                "test_name": "Return Channel Test",
                "needs_abscissa": "No",
                "include_anticipate": "No",
                "checks": (
                    "Record return channel location|"
                    "Record loop or equipment reference|"
                    "Confirm trackside reception"
                ),
            },
            {
                "event_pattern": "END OF SHUNT",
                "test_code": "LS14",
                "test_name": "End of Shunt Test",
                "needs_abscissa": "No",
                "include_anticipate": "No",
                "checks": (
                    "Record shunt route|"
                    "Confirm train in RM|"
                    "Record CM resume location|"
                    "Confirm CM/AM resumes at approved location"
                ),
            },
            {
                "event_pattern": "MOVING TRAIN INITIALISATION|MTIB",
                "test_code": "LS01",
                "test_name": "MTIB Initialization Test",
                "needs_abscissa": "No",
                "include_anticipate": "No",
                "checks": (
                    "Confirm RM mode|"
                    "Confirm Out Localisation|"
                    "Cross MTIB|"
                    "Record beacon code|"
                    "Confirm Total Localisation"
                ),
            },
        ]
    )

    project_example = pd.DataFrame(
        [
            ["Procedure Reference", "C1252-TAC-XXXX"],
            ["Procedure Title", "ATC Dynamic Test Procedure"],
            ["Project / Contract", "Example Project"],
            ["Trackside Version", ""],
            ["ATP Version", ""],
            ["ATO Version", ""],
            ["Test Sheet Prefix", "ATC-TEST"],
        ],
        columns=["Field", "Value"],
    )

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        subdivision_example.to_excel(
            writer,
            sheet_name="Subdivision",
            index=False,
        )

        mapping_example.to_excel(
            writer,
            sheet_name="TestMapping",
            index=False,
        )

        project_example.to_excel(
            writer,
            sheet_name="Project",
            index=False,
        )

    return output.getvalue()


# =============================================================================
# UI
# =============================================================================

def render_styles() -> None:
    """Local page CSS."""
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
    Render the Test Sheet Generator inside the main engineering platform.

    This function is imported and called from app.py.
    Do not call st.set_page_config() in this module.
    """
    render_styles()

    st.markdown(
        """
        <div class="generator-hero">
            <h1>ATC Test Sheet Generator</h1>
            <p>
                Upload one approved project workbook, select start and end KP,
                identify configurable ATC tests, and generate reviewable
                DOCX test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="generator-warning">
            <b>Engineering limitation:</b><br>
            This tool assists test planning only. It does not approve
            abscissae, variants, anticipated variants, stopping points,
            safety conditions, design logic or test applicability.
            All generated records must be independently reviewed against
            approved subdivision, PAT, invariant, signalling and procedure
            documents before issue.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.download_button(
        "⬇️ Download Input Workbook Template",
        data=create_input_template(),
        file_name="ATC_Test_Sheet_Generator_Template.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=False,
    )

    st.markdown("## 1. Upload One Project Workbook")

    uploaded_file = st.file_uploader(
        (
            "Upload one Excel workbook containing "
            "'Subdivision' and 'TestMapping' worksheets"
        ),
        type=["xlsx", "xlsm"],
        key="test_sheet_generator_input_file",
    )

    if uploaded_file is None:
        st.info(
            "Upload one Excel workbook. Download the template above if you "
            "need the required workbook structure."
        )
        return

    try:
        workbook_sheets = pd.read_excel(
            uploaded_file,
            sheet_name=None,
        )

        subdivision_sheet = find_sheet_name(
            workbook_sheets,
            "Subdivision",
        )

        mapping_sheet = find_sheet_name(
            workbook_sheets,
            "TestMapping",
        )

        if not subdivision_sheet:
            raise ValueError(
                "Workbook does not contain a worksheet named 'Subdivision'."
            )

        if not mapping_sheet:
            raise ValueError(
                "Workbook does not contain a worksheet named 'TestMapping'."
            )

        subdivision = standardise_subdivision_dataframe(
            workbook_sheets[subdivision_sheet]
        )

        test_mapping = standardise_mapping_dataframe(
            workbook_sheets[mapping_sheet]
        )

        project_info = read_project_information(
            workbook_sheets
        )

    except Exception as exc:
        st.error(f"Unable to read uploaded workbook: {exc}")
        return

    if subdivision.empty:
        st.error("The Subdivision worksheet contains no data.")
        return

    if subdivision["kp"].notna().sum() == 0:
        st.error(
            "No usable KP / Abscissa values were found. "
            "Add a column called KP, K.P., Abscissa or Chainage."
        )
        return

    if test_mapping.empty:
        st.error(
            "The TestMapping worksheet contains no valid mapping rules."
        )
        return

    st.success(
        f"Loaded {len(subdivision)} subdivision row(s) and "
        f"{len(test_mapping)} configurable test-mapping rule(s)."
    )

    with st.expander("Project Information", expanded=True):
        project_col1, project_col2, project_col3 = st.columns(3)

        with project_col1:
            project_info["project"] = st.text_input(
                "Project / Contract",
                value=project_info["project"],
                key="generator_project",
            )

            project_info["trackside_version"] = st.text_input(
                "Trackside Version",
                value=project_info["trackside_version"],
                key="generator_trackside",
            )

        with project_col2:
            project_info["atp_version"] = st.text_input(
                "ATP Version",
                value=project_info["atp_version"],
                key="generator_atp",
            )

            project_info["ato_version"] = st.text_input(
                "ATO Version",
                value=project_info["ato_version"],
                key="generator_ato",
            )

        with project_col3:
            project_info["procedure_reference"] = st.text_input(
                "Procedure Reference",
                value=project_info["procedure_reference"],
                key="generator_procedure_reference",
            )

            project_info["sheet_prefix"] = st.text_input(
                "Test Sheet Prefix",
                value=project_info["sheet_prefix"],
                key="generator_sheet_prefix",
            )

            project_info["procedure_title"] = st.text_input(
                "Procedure Title",
                value=project_info["procedure_title"],
                key="generator_procedure_title",
            )

    st.markdown("## 2. Select Test Range")

    available_branches = sorted(
        branch
        for branch in subdivision["branch"].dropna().unique()
        if safe_text(branch)
    )

    branch_options = ["All branches"] + available_branches

    selected_branch_display = st.selectbox(
        "Branch Filter",
        options=branch_options,
        key="generator_branch_filter",
    )

    selected_branch = (
        ""
        if selected_branch_display == "All branches"
        else selected_branch_display
    )

    range_dataframe = subdivision.copy()

    if selected_branch:
        range_dataframe = range_dataframe[
            range_dataframe["branch"].eq(selected_branch)
        ].copy()

    minimum_kp = float(range_dataframe["kp"].min())
    maximum_kp = float(range_dataframe["kp"].max())

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
        "🔎 Find Applicable Tests",
        type="primary",
        use_container_width=True,
        key="generator_find_tests",
    ):
        try:
            register, selected_events = create_test_register(
                subdivision=subdivision,
                mapping=test_mapping,
                start_kp=float(start_kp),
                end_kp=float(end_kp),
                selected_branch=selected_branch,
            )

            st.session_state["generator_register"] = register
            st.session_state["generator_selected_events"] = (
                selected_events
            )

        except Exception as exc:
            st.error(f"Test generation failed: {exc}")

    register = st.session_state.get(
        "generator_register",
        pd.DataFrame(columns=REGISTER_COLUMNS),
    )

    selected_events = st.session_state.get(
        "generator_selected_events",
        pd.DataFrame(),
    )

    if register.empty:
        st.warning(
            "No tests were generated. Check the selected KP range and "
            "confirm that TestMapping event patterns match your "
            "Subdivision event names or remarks."
        )

        with st.expander("View TestMapping Rules"):
            st.dataframe(
                test_mapping,
                use_container_width=True,
                hide_index=True,
            )

        return

    st.markdown("## 3. Review Generated Test Register")

    st.caption(
        "Review and correct the generated data against approved engineering "
        "documents before downloading DOCX test sheets."
    )

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
            "participate_abscissa_m": st.column_config.NumberColumn(
                "Participating Abscissa (m)",
                format="%.3f",
            ),
            "anticipate_abscissa_m": st.column_config.NumberColumn(
                "Anticipated Abscissa (m)",
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
            "remarks": st.column_config.TextColumn(
                "Remarks",
                width="large",
            ),
            "checks": st.column_config.TextColumn(
                "Checks - separate each item using |",
                width="large",
            ),
        },
    )

    st.session_state["generator_register"] = reviewed_register

    metric_col1, metric_col2, metric_col3 = st.columns(3)

    metric_col1.metric(
        "Generated Test Sheets",
        len(reviewed_register),
    )

    metric_col2.metric(
        "Tests Requiring Abscissa",
        int(
            reviewed_register["needs_abscissa"]
            .fillna(False)
            .astype(bool)
            .sum()
        ),
    )

    metric_col3.metric(
        "Rows With Anticipated Variant",
        int(
            reviewed_register["anticipate_variant"]
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ),
    )

    st.markdown("## 4. Download Outputs")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        selected_events=selected_events,
        project_info=project_info,
    )

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
            data=reviewed_register.to_csv(
                index=False
            ).encode("utf-8"),
            file_name=clean_filename(
                f"{project_info['sheet_prefix']}_Test_Register.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with download_col3:
        try:
            docx_zip = create_docx_zip(
                register=reviewed_register,
                project_info=project_info,
            )

            st.download_button(
                "⬇️ Download DOCX Test Sheets",
                data=docx_zip,
                file_name=clean_filename(
                    f"{project_info['sheet_prefix']}_Test_Sheets.zip"
                ),
                mime="application/zip",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(f"DOCX generation failed: {exc}")

    with st.expander("Selected Source Events"):
        st.dataframe(
            selected_events,
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Configured Test Mapping Rules"):
        st.dataframe(
            test_mapping,
            use_container_width=True,
            hide_index=True,
        )
