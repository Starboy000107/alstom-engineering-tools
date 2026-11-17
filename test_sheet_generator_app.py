from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


# =============================================================================
# BASIC HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    """Convert values safely to stripped text."""
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


def normalise_branch(value: Any) -> str:
    """
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


def branch_parts(branch: str) -> tuple[str, str, str]:
    parts = safe_text(branch).split("-")

    sector = parts[0] if len(parts) >= 1 else ""
    section = parts[1] if len(parts) >= 2 else ""
    subsection = "-".join(parts[:3]) if len(parts) >= 3 else ""

    return sector, section, subsection


def clean_filename(value: str) -> str:
    value = safe_text(value)
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", "_", value)
    return value[:150] or "Test_Sheet"


def to_number(value: Any) -> Optional[float]:
    text = safe_text(value).replace(",", "").replace(" ", "")

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def yes_no(value: Any) -> bool:
    return safe_text(value).upper() in {
        "YES",
        "Y",
        "TRUE",
        "1",
        "T",
    }


# =============================================================================
# INPUT COLUMN NORMALISATION
# =============================================================================

COLUMN_ALIASES: Dict[str, List[str]] = {
    "kp": [
        "kp",
        "k.p.",
        "k.p",
        "abscissa",
        "abscissa_m",
        "abscissa (m)",
        "chainage",
        "chainage_m",
    ],
    "branch": [
        "branch",
        "branch_number",
        "branch number",
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
        "singularity_name",
        "type",
    ],
    "remarks": [
        "remarks",
        "remark",
        "description",
        "details",
        "singularities",
    ],
    "variant_number": [
        "variant_number",
        "variant number",
        "variant",
        "variant_no",
        "variant no",
        "rank",
    ],
    "participate_variant": [
        "participate_variant",
        "participating_variant",
        "participate variant",
        "associated_variant",
        "associated variant",
    ],
    "anticipate_variant": [
        "anticipate_variant",
        "anticipated_variant",
        "anticipate variant",
        "anticipated variant",
    ],
    "location_name": [
        "location_name",
        "location",
        "asset",
        "asset_name",
        "signal",
        "track_circuit",
    ],
    "direction": [
        "direction",
        "track_direction",
        "normal_direction",
    ],
}


def find_column(dataframe: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    lookup = {
        normalise_spaces(column).lower(): column
        for column in dataframe.columns
    }

    for alias in aliases:
        if alias.lower() in lookup:
            return lookup[alias.lower()]

    return None


def standardise_subdivision_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Convert user workbook columns into standard internal columns.

    The source workbook can use different headings such as:
    - K.P.
    - Abscissa
    - Name
    - Remarks
    - Associated Variant
    """
    output = pd.DataFrame(index=raw.index)

    for standard_name, aliases in COLUMN_ALIASES.items():
        source_column = find_column(raw, aliases)

        if source_column:
            output[standard_name] = raw[source_column]
        else:
            output[standard_name] = ""

    output["kp"] = output["kp"].apply(to_number)

    output["branch"] = output["branch"].apply(normalise_branch)

    for index in output.index:
        branch = safe_text(output.at[index, "branch"])
        branch_sector, branch_section, branch_subsection = branch_parts(branch)

        if not safe_text(output.at[index, "sector"]):
            output.at[index, "sector"] = branch_sector

        if not safe_text(output.at[index, "section"]):
            output.at[index, "section"] = branch_section

        if not safe_text(output.at[index, "subsection"]):
            output.at[index, "subsection"] = branch_subsection

    for column in output.columns:
        if column != "kp":
            output[column] = output[column].apply(safe_text)

    output["source_row"] = raw.index + 2

    return output


def standardise_mapping_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate and standardise the TestMapping sheet."""
    required_columns = [
        "event_pattern",
        "test_code",
        "test_name",
        "needs_abscissa",
        "include_anticipate",
    ]

    lookup = {
        normalise_spaces(column).lower(): column
        for column in raw.columns
    }

    missing = [
        column
        for column in required_columns
        if column.lower() not in lookup
    ]

    if missing:
        raise ValueError(
            "TestMapping sheet is missing required column(s): "
            + ", ".join(missing)
        )

    mapping = pd.DataFrame()

    for column in required_columns + ["checks"]:
        source_column = lookup.get(column.lower())

        if source_column:
            mapping[column] = raw[source_column]
        else:
            mapping[column] = ""

    mapping["event_pattern"] = mapping["event_pattern"].apply(safe_text)
    mapping["test_code"] = mapping["test_code"].apply(safe_text)
    mapping["test_name"] = mapping["test_name"].apply(safe_text)
    mapping["needs_abscissa"] = mapping["needs_abscissa"].apply(yes_no)
    mapping["include_anticipate"] = mapping["include_anticipate"].apply(yes_no)
    mapping["checks"] = mapping["checks"].apply(safe_text)

    mapping = mapping[
        mapping["event_pattern"].astype(str).str.strip().ne("")
    ].copy()

    return mapping.reset_index(drop=True)


# =============================================================================
# PARTICIPATE / ANTICIPATE VARIANT RESOLUTION
# =============================================================================

def extract_variant_from_text(text: Any, label: str) -> str:
    """
    Extract values such as:

        Associated variant: (TUC KP T/C)
        Anticipate variant: 14-07-1
        Participating variant = 3

    This supports source data where variants have not yet been separated
    into dedicated workbook columns.
    """
    value = safe_text(text)

    pattern = (
        rf"{label}"
        r"(?:\s+(?:variant|variants|number))?"
        r"\s*[:=]?\s*"
        r"\(?([^()\n]+?)\)?"
        r"(?=$|;|\||\n|,?\s+(?:anticipate|associated|participate|"
        r"number of|rank of|sub section))"
    )

    match = re.search(pattern, value, flags=re.IGNORECASE)

    if match:
        return normalise_spaces(match.group(1))

    return ""


def build_variant_index(subdivision: pd.DataFrame) -> Dict[str, List[pd.Series]]:
    """
    Builds an index which can be used to locate a variant and obtain
    its related event abscissa.
    """
    index: Dict[str, List[pd.Series]] = {}

    for _, row in subdivision.iterrows():
        variant = safe_text(row.get("variant_number"))

        if not variant:
            continue

        key = variant.upper()
        index.setdefault(key, []).append(row)

    return index


def resolve_variant_abscissa(
    variant_value: str,
    variant_index: Dict[str, List[pd.Series]],
    reference_kp: Optional[float],
) -> Optional[float]:
    """
    Find the closest matching variant row and return its KP/abscissa.

    If there are several rows with the same variant number, the nearest
    row to the participating event KP is used.
    """
    value = safe_text(variant_value).upper()

    if not value:
        return None

    candidate_keys = [value]

    numeric_match = re.search(r"\b(\d+)\b", value)

    if numeric_match:
        candidate_keys.append(numeric_match.group(1))

    candidates: List[pd.Series] = []

    for key in candidate_keys:
        candidates.extend(variant_index.get(key, []))

    if not candidates:
        return None

    candidates_with_kp = [
        row for row in candidates
        if pd.notna(row.get("kp"))
    ]

    if not candidates_with_kp:
        return None

    if reference_kp is None:
        return float(candidates_with_kp[0]["kp"])

    closest = min(
        candidates_with_kp,
        key=lambda row: abs(float(row["kp"]) - reference_kp),
    )

    return float(closest["kp"])


# =============================================================================
# TEST-POINT BUILDING
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
]


def event_text(row: pd.Series) -> str:
    return " ".join(
        [
            safe_text(row.get("event_name")),
            safe_text(row.get("remarks")),
            safe_text(row.get("location_name")),
            safe_text(row.get("participate_variant")),
            safe_text(row.get("anticipate_variant")),
        ]
    )


def match_tests_for_event(
    row: pd.Series,
    mapping: pd.DataFrame,
    variant_index: Dict[str, List[pd.Series]],
) -> List[Dict[str, Any]]:
    """
    Match an event row against every TestMapping regex rule.

    Multiple tests can be generated from one singularity if multiple
    mapping rules match.
    """
    output: List[Dict[str, Any]] = []
    searchable_text = event_text(row)
    source_kp = row.get("kp")

    participate_variant = safe_text(row.get("participate_variant"))
    anticipate_variant = safe_text(row.get("anticipate_variant"))
    remarks = safe_text(row.get("remarks"))

    if not participate_variant:
        participate_variant = extract_variant_from_text(
            remarks,
            r"(?:associated|participate|participating)",
        )

    if not anticipate_variant:
        anticipate_variant = extract_variant_from_text(
            remarks,
            r"anticipate(?:d)?",
        )

    for _, rule in mapping.iterrows():
        pattern = safe_text(rule["event_pattern"])

        try:
            matched = re.search(
                pattern,
                searchable_text,
                flags=re.IGNORECASE,
            )
        except re.error as exc:
            raise ValueError(
                f"Invalid TestMapping regex '{pattern}': {exc}"
            ) from exc

        if not matched:
            continue

        needs_abscissa = bool(rule["needs_abscissa"])
        include_anticipate = bool(rule["include_anticipate"])

        participate_abscissa: Optional[float]
        anticipate_abscissa: Optional[float]

        if needs_abscissa:
            participate_abscissa = (
                float(source_kp)
                if pd.notna(source_kp)
                else None
            )
        else:
            participate_abscissa = None

        if include_anticipate:
            anticipate_abscissa = resolve_variant_abscissa(
                variant_value=anticipate_variant,
                variant_index=variant_index,
                reference_kp=participate_abscissa,
            )
        else:
            anticipate_abscissa = None

        output.append(
            {
                "test_code": safe_text(rule["test_code"]),
                "test_name": safe_text(rule["test_name"]),
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
                "location_name": safe_text(row.get("location_name")),
                "event_name": safe_text(row.get("event_name")),
                "remarks": safe_text(row.get("remarks")),
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
) -> pd.DataFrame:
    """
    Find all events between selected start/end KPs and create test records.
    """
    lower_kp = min(start_kp, end_kp)
    upper_kp = max(start_kp, end_kp)

    selected = subdivision[
        subdivision["kp"].notna()
        & subdivision["kp"].between(lower_kp, upper_kp, inclusive="both")
    ].copy()

    if selected_branch:
        selected = selected[
            selected["branch"].astype(str).eq(selected_branch)
        ].copy()

    variant_index = build_variant_index(subdivision)

    records: List[Dict[str, Any]] = []

    for _, row in selected.iterrows():
        records.extend(
            match_tests_for_event(
                row=row,
                mapping=mapping,
                variant_index=variant_index,
            )
        )

    if not records:
        return pd.DataFrame(columns=REGISTER_COLUMNS + ["checks"])

    result = pd.DataFrame(records)

    result = result.drop_duplicates(
        subset=[
            "test_code",
            "branch",
            "participate_abscissa_m",
            "participate_variant",
            "anticipate_variant",
            "event_name",
        ]
    ).reset_index(drop=True)

    result = result.sort_values(
        by=["branch", "participate_abscissa_m", "test_code"],
        na_position="last",
    ).reset_index(drop=True)

    return result


# =============================================================================
# DOCX GENERATION
# =============================================================================

def set_cell_text(cell: Any, text: str, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(text))
    run.bold = bold


def add_table_row(
    table: Any,
    values: List[str],
    bold: bool = False,
) -> None:
    cells = table.add_row().cells

    for index, value in enumerate(values):
        set_cell_text(cells[index], value, bold=bold)


def checks_from_row(row: pd.Series) -> List[str]:
    """
    Checks are configured in TestMapping with | separators.

    Example:
        Track circuit occupied|Record Loc Max|Confirm target speed
    """
    configured_checks = safe_text(row.get("checks"))

    if configured_checks:
        return [
            normalise_spaces(item)
            for item in configured_checks.split("|")
            if normalise_spaces(item)
        ]

    return [
        "Confirm engineering source and approved design data.",
        "Record OMAP / onboard / wayside evidence reference.",
        "Confirm participating variant state.",
    ]


def create_docx_test_sheet(
    row: pd.Series,
    project: Dict[str, str],
    sheet_number: str,
) -> bytes:
    """
    Create a generic project-configurable DOCX test sheet.

    The tests and checks are driven by TestMapping, not hard-coded
    LS01–LS14 Python logic.
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing python-docx. Install with: pip install python-docx"
        ) from exc

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

    heading_run = heading.add_run(
        project["procedure_title"]
    )
    heading_run.bold = True
    heading_run.font.size = Pt(13)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run(
        f"{safe_text(row['test_code'])} – {safe_text(row['test_name'])}"
    )
    title_run.bold = True
    title_run.font.size = Pt(11)

    document.add_paragraph()

    details_table = document.add_table(rows=0, cols=4)
    details_table.style = "Table Grid"

    add_table_row(
        details_table,
        [
            "Procedure Reference",
            project["procedure_reference"],
            "Test Sheet Number",
            sheet_number,
        ],
    )

    add_table_row(
        details_table,
        [
            "Project / Contract",
            project["project"],
            "Trackside Version",
            project["trackside_version"],
        ],
    )

    add_table_row(
        details_table,
        [
            "ATP Version",
            project["atp_version"],
            "ATO Version",
            project["ato_version"],
        ],
    )

    document.add_paragraph()

    location_table = document.add_table(rows=0, cols=4)
    location_table.style = "Table Grid"

    add_table_row(
        location_table,
        [
            "Sector",
            safe_text(row.get("sector")),
            "Branch",
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
        participate_abs = safe_text(
            row.get("participate_abscissa_m")
        )

        if participate_abs:
            participate_abs += " m"

        add_table_row(
            location_table,
            [
                "Participating Abscissa",
                participate_abs,
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
                "Abscissa Requirement",
                "Not required for this test",
            ],
        )

    anticipate_variant = safe_text(row.get("anticipate_variant"))

    if anticipate_variant:
        anticipated_abs = safe_text(
            row.get("anticipate_abscissa_m")
        )

        if anticipated_abs:
            anticipated_abs += " m"

        add_table_row(
            location_table,
            [
                "Anticipated Variant",
                anticipate_variant,
                "Anticipated Abscissa",
                anticipated_abs or "To be confirmed",
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

    test_table = document.add_table(rows=1, cols=4)
    test_table.style = "Table Grid"

    headers = [
        "Test Action / Check",
        "Expected Result",
        "Actual Result / Evidence",
        "OK?",
    ]

    for index, text in enumerate(headers):
        set_cell_text(test_table.rows[0].cells[index], text, bold=True)

    for check in checks_from_row(row):
        add_table_row(
            test_table,
            [
                check,
                "Compliant with approved design and test procedure.",
                "",
                "☐ Y   ☐ N   ☐ N/A",
            ],
        )

    document.add_paragraph()

    comments = document.add_paragraph()
    comments.add_run("Engineering Notes / Source Evidence").bold = True

    notes_table = document.add_table(rows=1, cols=1)
    notes_table.style = "Table Grid"

    note_text = (
        f"Source row: {safe_text(row.get('source_row'))}\n"
        f"Event: {safe_text(row.get('event_name'))}\n"
        f"Remarks: {safe_text(row.get('remarks'))}\n\n"
        "This sheet is automatically generated. The extracted data, "
        "variants, abscissae, test applicability and acceptance criteria "
        "must be reviewed against approved project documents before issue."
    )

    set_cell_text(notes_table.rows[0].cells[0], note_text)

    document.add_paragraph()

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
            "Contractor / Supplier",
            "Client / Employer",
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
        "UNCONTROLLED WHEN PRINTED – Verify approved design data before use."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(7)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


def create_docx_zip(
    register: pd.DataFrame,
    project: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for sequence, (_, row) in enumerate(register.iterrows(), start=1):
            sheet_number = (
                f"{project['sheet_prefix']}-{sequence:03d}"
            )

            branch = safe_text(row.get("branch")) or "NO_BRANCH"
            test_code = safe_text(row.get("test_code")) or "NO_CODE"

            abs_value = safe_text(
                row.get("participate_abscissa_m")
            ) or "NO_ABSCISSA"

            filename = clean_filename(
                f"{sheet_number}_{test_code}_{branch}_KP_{abs_value}.docx"
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


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    selected_events: pd.DataFrame,
    project: Dict[str, str],
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        cover = pd.DataFrame(
            [
                ["Procedure Reference", project["procedure_reference"]],
                ["Procedure Title", project["procedure_title"]],
                ["Project / Contract", project["project"]],
                ["Trackside Version", project["trackside_version"]],
                ["ATP Version", project["atp_version"]],
                ["ATO Version", project["ato_version"]],
                ["Generated Date", str(date.today())],
                ["Generated Test Sheets", len(register)],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(writer, sheet_name="Cover", index=False)
        register.to_excel(writer, sheet_name="Test Register", index=False)
        selected_events.to_excel(
            writer,
            sheet_name="Selected Subdivision Events",
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
                ].width = min(max(max_length + 2, 12), 65)

    return output.getvalue()


# =============================================================================
# TEMPLATE DOWNLOAD
# =============================================================================

def create_input_template() -> bytes:
    """
    Downloadable workbook template. This is only a starting structure;
    project data must be imported from approved subdivision information.
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
                    "Associated variant: (TUC KP T/C); "
                    "Anticipate variant: 14-07-1"
                ),
                "variant_number": "1",
                "participate_variant": "TUC KP T/C",
                "anticipate_variant": "14-07-1",
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
                "remarks": "Associated variant: (TUC KP T/C)",
                "variant_number": "5",
                "participate_variant": "TUC KP T/C",
                "anticipate_variant": "",
                "location_name": "TC 2450",
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
                "needs_abscissa": True,
                "include_anticipate": True,
                "checks": (
                    "Track circuit occupied|"
                    "Available mode CM/AUTO|"
                    "Record Loc Max|"
                    "Record target distance|"
                    "Track circuit free"
                ),
            },
            {
                "event_pattern": "RETURN CHANNEL",
                "test_code": "LS13",
                "test_name": "Return Channel Test",
                "needs_abscissa": False,
                "include_anticipate": False,
                "checks": (
                    "Record return channel location|"
                    "Confirm return-channel loop|"
                    "Confirm trackside reception"
                ),
            },
            {
                "event_pattern": "END OF SHUNT",
                "test_code": "LS14",
                "test_name": "End of Shunt Test",
                "needs_abscissa": False,
                "include_anticipate": False,
                "checks": (
                    "Record shunt route name|"
                    "Confirm RM operation|"
                    "Confirm CM/AM resume"
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
        ],
        columns=["Field", "Value"],
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
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
# STREAMLIT APPLICATION
# =============================================================================

def render_styles() -> None:
    st.markdown(
        """
        <style>
        .hero {
            background: linear-gradient(135deg, #102A43, #164E72);
            color: white;
            padding: 24px 30px;
            border-radius: 14px;
            margin-bottom: 16px;
        }
        .hero h1 {
            color: white !important;
            margin: 0 !important;
        }
        .notice {
            background: #FFF7E8;
            border-left: 5px solid #E37400;
            border-radius: 8px;
            padding: 14px;
            margin: 12px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def read_project_sheet(
    excel_file: Any,
) -> Dict[str, str]:
    defaults = {
        "procedure_reference": "C1252-TAC-XXXX",
        "procedure_title": "ATC Dynamic Test Procedure",
        "project": "",
        "trackside_version": "",
        "atp_version": "",
        "ato_version": "",
        "sheet_prefix": "ATC-TEST",
    }

    try:
        project_df = pd.read_excel(
            excel_file,
            sheet_name="Project",
        )
    except Exception:
        return defaults

    if "Field" not in project_df.columns or "Value" not in project_df.columns:
        return defaults

    for _, row in project_df.iterrows():
        field = safe_text(row["Field"]).lower()
        value = safe_text(row["Value"])

        mapping = {
            "procedure reference": "procedure_reference",
            "procedure title": "procedure_title",
            "project / contract": "project",
            "project": "project",
            "trackside version": "trackside_version",
            "atp version": "atp_version",
            "ato version": "ato_version",
            "test sheet prefix": "sheet_prefix",
        }

        if field in mapping:
            defaults[mapping[field]] = value

    if not defaults["sheet_prefix"]:
        defaults["sheet_prefix"] = defaults["procedure_reference"]

    return defaults


def main() -> None:
    st.set_page_config(
        page_title="ATC Test Sheet Generator",
        page_icon="🚆",
        layout="wide",
    )

    render_styles()

    st.markdown(
        """
        <div class="hero">
            <h1>ATC Subdivision Test Sheet Generator</h1>
            <p>
                Select a start and end KP, identify applicable ATC tests,
                resolve participating and anticipated variants, and generate
                a reviewable test-sheet register and DOCX test sheets.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="notice">
            <b>Engineering warning:</b><br>
            This application assists test planning. It does not approve
            abscissae, variants, stopping points, safety conditions or test
            applicability. All generated data must be checked against approved
            subdivision, invariant, signalling, PAT and test documentation.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.download_button(
        "Download Input Workbook Template",
        data=create_input_template(),
        file_name="ATC_Test_Sheet_Generator_Template.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    st.markdown("## 1. Upload One Project Workbook")

    uploaded = st.file_uploader(
        "Upload one Excel workbook containing Subdivision and TestMapping sheets",
        type=["xlsx", "xlsm"],
    )

    if uploaded is None:
        st.info(
            "Upload an Excel workbook. Use the downloadable template if "
            "needed."
        )
        return

    try:
        raw_subdivision = pd.read_excel(
            uploaded,
            sheet_name="Subdivision",
        )

        uploaded.seek(0)

        raw_mapping = pd.read_excel(
            uploaded,
            sheet_name="TestMapping",
        )

        uploaded.seek(0)

        project_defaults = read_project_sheet(uploaded)

        subdivision = standardise_subdivision_dataframe(
            raw_subdivision
        )

        test_mapping = standardise_mapping_dataframe(
            raw_mapping
        )

    except Exception as exc:
        st.error(f"Cannot read uploaded workbook: {exc}")
        return

    if subdivision.empty:
        st.error("Subdivision sheet contains no usable rows.")
        return

    if subdivision["kp"].notna().sum() == 0:
        st.error(
            "No KP/Abscissa values were found. Add a kp, K.P., "
            "Abscissa or Chainage column."
        )
        return

    st.success(
        f"Loaded {len(subdivision)} subdivision rows and "
        f"{len(test_mapping)} configurable test-mapping rules."
    )

    with st.expander("Project Information", expanded=True):
        col1, col2, col3 = st.columns(3)

        with col1:
            project_defaults["project"] = st.text_input(
                "Project / Contract",
                value=project_defaults["project"],
            )

            project_defaults["trackside_version"] = st.text_input(
                "Trackside Version",
                value=project_defaults["trackside_version"],
            )

        with col2:
            project_defaults["atp_version"] = st.text_input(
                "ATP Version",
                value=project_defaults["atp_version"],
            )

            project_defaults["ato_version"] = st.text_input(
                "ATO Version",
                value=project_defaults["ato_version"],
            )

        with col3:
            project_defaults["procedure_reference"] = st.text_input(
                "Procedure Reference",
                value=project_defaults["procedure_reference"],
            )

            project_defaults["sheet_prefix"] = st.text_input(
                "Test Sheet Prefix",
                value=project_defaults["sheet_prefix"],
            )

            project_defaults["procedure_title"] = st.text_input(
                "Procedure Title",
                value=project_defaults["procedure_title"],
            )

    st.markdown("## 2. Select Start and End Point")

    available_branches = sorted(
        branch
        for branch in subdivision["branch"].dropna().unique()
        if safe_text(branch)
    )

    branch_options = ["All branches"] + available_branches

    selected_branch_display = st.selectbox(
        "Branch Filter",
        options=branch_options,
    )

    selected_branch = (
        ""
        if selected_branch_display == "All branches"
        else selected_branch_display
    )

    filtered_for_range = subdivision.copy()

    if selected_branch:
        filtered_for_range = filtered_for_range[
            filtered_for_range["branch"] == selected_branch
        ].copy()

    minimum_kp = float(filtered_for_range["kp"].min())
    maximum_kp = float(filtered_for_range["kp"].max())

    col1, col2 = st.columns(2)

    with col1:
        start_kp = st.number_input(
            "Start KP / Abscissa",
            min_value=minimum_kp,
            max_value=maximum_kp,
            value=minimum_kp,
            step=0.1,
            format="%.3f",
        )

    with col2:
        end_kp = st.number_input(
            "End KP / Abscissa",
            min_value=minimum_kp,
            max_value=maximum_kp,
            value=maximum_kp,
            step=0.1,
            format="%.3f",
        )

    if st.button(
        "Find Applicable Tests",
        type="primary",
        use_container_width=True,
    ):
        register = create_test_register(
            subdivision=subdivision,
            mapping=test_mapping,
            start_kp=float(start_kp),
            end_kp=float(end_kp),
            selected_branch=selected_branch,
        )

        lower_kp = min(float(start_kp), float(end_kp))
        upper_kp = max(float(start_kp), float(end_kp))

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
                selected_events["branch"] == selected_branch
            ].copy()

        st.session_state["atc_register"] = register
        st.session_state["atc_selected_events"] = selected_events

    register = st.session_state.get(
        "atc_register",
        pd.DataFrame(),
    )

    selected_events = st.session_state.get(
        "atc_selected_events",
        pd.DataFrame(),
    )

    if register.empty:
        st.warning(
            "No tests have been generated yet, or no TestMapping rules "
            "matched events in the selected range."
        )

        with st.expander("View TestMapping Rules"):
            st.dataframe(test_mapping, use_container_width=True)

        return

    st.markdown("## 3. Review Generated Test Register")

    st.caption(
        "Review every generated test. You may add, remove or edit rows before "
        "generating DOCX test sheets."
    )

    editable_columns = [
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

    register = register[
        [column for column in editable_columns if column in register.columns]
    ].copy()

    reviewed_register = st.data_editor(
        register,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
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
                "Checks (separate by |)",
                width="large",
            ),
        },
    )

    st.session_state["atc_register"] = reviewed_register

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric("Generated Test Sheets", len(reviewed_register))

    metric2.metric(
        "Tests Requiring Abscissa",
        int(
            reviewed_register["needs_abscissa"]
            .fillna(False)
            .astype(bool)
            .sum()
        ),
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

    st.markdown("## 4. Download Outputs")

    excel_bytes = create_excel_bytes(
        register=reviewed_register,
        selected_events=selected_events,
        project=project_defaults,
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.download_button(
            "Download Excel Register",
            data=excel_bytes,
            file_name=clean_filename(
                f"{project_defaults['sheet_prefix']}_Test_Register.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
        )

    with col2:
        st.download_button(
            "Download CSV Register",
            data=reviewed_register.to_csv(index=False).encode("utf-8"),
            file_name=clean_filename(
                f"{project_defaults['sheet_prefix']}_Test_Register.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )

    with col3:
        try:
            docx_zip = create_docx_zip(
                register=reviewed_register,
                project=project_defaults,
            )

            st.download_button(
                "Download DOCX Test Sheets",
                data=docx_zip,
                file_name=clean_filename(
                    f"{project_defaults['sheet_prefix']}_Test_Sheets.zip"
                ),
                mime="application/zip",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(f"DOCX generation failed: {exc}")

    with st.expander("Selected Source Events"):
        st.dataframe(selected_events, use_container_width=True)

    with st.expander("Configured TestMapping Rules"):
        st.dataframe(test_mapping, use_container_width=True)


if __name__ == "__main__":
    main()
