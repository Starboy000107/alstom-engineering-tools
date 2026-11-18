from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd


# =============================================================================
# BASIC HELPERS
# =============================================================================

def safe_text(value: Any) -> str:
    """Safely convert a value to text."""
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
    return value[:160] or "ATC_Test_Sheet"


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


def format_distance(value: Any) -> str:
    numeric = to_float(value)

    if numeric is None:
        return ""

    return f"{numeric:.3f} m"


# =============================================================================
# DOCX HELPERS
# =============================================================================

def set_cell_text(
    cell: Any,
    value: Any,
    bold: bool = False,
    point_size: int = 8,
) -> None:
    from docx.shared import Pt

    cell.text = ""

    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(safe_text(value))
    run.bold = bold
    run.font.size = Pt(point_size)


def add_docx_row(
    table: Any,
    values: List[Any],
    bold: bool = False,
    point_size: int = 8,
) -> None:
    cells = table.add_row().cells

    for index, value in enumerate(values):
        if index < len(cells):
            set_cell_text(
                cells[index],
                value,
                bold=bold,
                point_size=point_size,
            )


def set_table_font_size(table: Any, point_size: int = 8) -> None:
    from docx.shared import Pt

    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(point_size)


def shade_cell(cell: Any, fill: str = "D9E1F2") -> None:
    """Apply a background colour to a DOCX table cell."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def add_heading(document: Any, text: str, size: int = 10) -> None:
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(size)


# =============================================================================
# COMMON APPENDIX-A STYLE SECTIONS
# =============================================================================

def add_appendix_a_header(
    document: Any,
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
    test_title: str,
    test_id: str,
) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = heading.add_run("ALSTOM\n")
    run.bold = True
    run.font.size = Pt(15)

    run = heading.add_run("Trackside ATC Low-Speed Dynamic Test Procedure")
    run.bold = True
    run.font.size = Pt(9)

    title = document.add_paragraph()

    run = title.add_run(f"{test_title} ({test_id})")
    run.bold = True
    run.font.size = Pt(11)

    control_table = document.add_table(rows=2, cols=6)
    control_table.style = "Table Grid"

    add_docx_row(
        control_table,
        [
            "Procedure Reference:",
            project_info.get("procedure_reference", ""),
            "Procedure Title:",
            project_info.get("procedure_title", ""),
            "Test Sheet No.:",
            sheet_number,
        ],
        point_size=7,
    )

    add_docx_row(
        control_table,
        [
            "Trackside Version:",
            safe_text(row.get("trackside_version")),
            "ATP Version:",
            safe_text(row.get("atp_version")),
            "ATO Version:",
            safe_text(row.get("ato_version")),
        ],
        point_size=7,
    )

    set_table_font_size(control_table, 7)


def add_location_information(
    document: Any,
    row: pd.Series,
) -> None:
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
        point_size=8,
    )

    add_docx_row(
        location_table,
        [
            "Section:",
            safe_text(row.get("section")),
            "Sub-section:",
            safe_text(row.get("subsection")),
        ],
        point_size=8,
    )

    add_docx_row(
        location_table,
        [
            "Direction:",
            safe_text(row.get("direction")),
            "Abscissa:",
            format_distance(row.get("abscissa_m")),
        ],
        point_size=8,
    )

    add_docx_row(
        location_table,
        [
            "Absolute K.P.:",
            format_distance(row.get("absolute_abscissa_m")),
            "Event:",
            safe_text(row.get("event_name")),
        ],
        point_size=8,
    )

    add_docx_row(
        location_table,
        [
            "Associated Variant:",
            safe_text(row.get("associated_variant")),
            "Location / Asset:",
            safe_text(row.get("location_name")),
        ],
        point_size=8,
    )

    set_table_font_size(location_table, 8)


def add_participate_anticipate_information(
    document: Any,
    row: pd.Series,
) -> None:
    """
    Planning data populated from the test register.

    Fields are allowed to remain blank when the source extraction cannot
    prove the relationship. They must be reviewed before generation.
    """
    table = document.add_table(rows=0, cols=4)
    table.style = "Table Grid"

    add_docx_row(
        table,
        [
            "Participating Variant:",
            safe_text(row.get("participating_variant")),
            "Anticipated Variant:",
            safe_text(row.get("anticipated_variant")),
        ],
        point_size=7,
    )

    add_docx_row(
        table,
        [
            "Participating Sub-section(s):",
            safe_text(row.get("participating_subsections")),
            "Anticipated Sub-section(s):",
            safe_text(row.get("anticipated_subsections")),
        ],
        point_size=7,
    )

    add_docx_row(
        table,
        [
            "Participating Branch(es):",
            safe_text(row.get("participating_branches")),
            "Anticipated Branch(es):",
            safe_text(row.get("anticipated_branches")),
        ],
        point_size=7,
    )

    set_table_font_size(table, 7)


def add_comments_and_approval(
    document: Any,
    row: pd.Series,
) -> None:
    document.add_paragraph()

    comments_table = document.add_table(rows=2, cols=1)
    comments_table.style = "Table Grid"

    set_cell_text(
        comments_table.rows[0].cells[0],
        "Comments:",
        bold=True,
        point_size=8,
    )

    text = (
        "DRAFT – ENGINEERING REVIEW REQUIRED\n\n"
        f"Source PDF Page: {safe_text(row.get('source_page'))}\n"
        f"Schedule Row: {safe_text(row.get('row_number'))}\n"
        f"Singularity Code: {safe_text(row.get('singularity_code'))}\n"
        f"Absolute Chainage: "
        f"{format_distance(row.get('absolute_abscissa_m'))}\n"
        f"Sub-section Start Chainage: "
        f"{format_distance(row.get('subsection_start_abscissa_m'))}\n"
        f"Calculated Local Abscissa: "
        f"{format_distance(row.get('abscissa_m'))}\n\n"
        f"Calculation Log:\n{safe_text(row.get('calculation_log'))}\n\n"
        f"Marker-PDF Source Evidence:\n"
        f"{safe_text(row.get('source_evidence'))}\n\n"
        f"Engineering Remarks:\n{safe_text(row.get('remarks'))}"
    )

    set_cell_text(
        comments_table.rows[1].cells[0],
        text,
        point_size=7,
    )

    document.add_paragraph()

    approval_table = document.add_table(rows=0, cols=3)
    approval_table.style = "Table Grid"

    add_docx_row(
        approval_table,
        [
            (
                "The test covered by this Test Sheet being completed, "
                "the test is considered as:"
            ),
            "Passed ☐",
            "Failed ☐",
        ],
        point_size=8,
    )

    add_docx_row(
        approval_table,
        ["", "Alstom", "MTR"],
        bold=True,
        point_size=8,
    )

    add_docx_row(approval_table, ["Name:", "", ""], point_size=8)
    add_docx_row(approval_table, ["Signature:", "", ""], point_size=8)
    add_docx_row(approval_table, ["Date:", "", ""], point_size=8)

    set_table_font_size(approval_table, 8)


def add_footer(document: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = paragraph.add_run(
        "UNCONTROLLED WHEN PRINTED – "
        "Not to be used before verification of applicable version number."
    )
    run.italic = True
    run.font.size = Pt(7)


# =============================================================================
# LS01 — MTIB INITIALISATION
# =============================================================================

def add_mtib_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "MTIB", 10)

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    headers = ["Step / Check", "Expected Result", "Actual Record", "Y / N"]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    rows = [
        ["1. Selected Mode", "RM", "", ""],
        ["1. Localisation Status", "Out Loc", "", ""],
        ["2. Selected Mode", "RM", "", ""],
        ["2. Localisation Status", "Wait Loc", "", ""],
        ["2. Beacon Code", "Byte 1", "", ""],
        ["2. Beacon Code", "Byte 2", "", ""],
        ["2. Beacon Code", "Byte 3", "", ""],
        ["2. Beacon Code", "Byte 4", "", ""],
        ["2. Calibration", "A-car Number", "", ""],
        ["2. Calibration", "Cal. Max", "", "mm"],
        ["3. Selected Mode", "RM", "", ""],
        ["3. Localisation Status", "Total Loc", "", ""],
    ]

    for values in rows:
        add_docx_row(table, values, point_size=8)

    set_table_font_size(table, 8)


# =============================================================================
# LS02 — STIB INITIALISATION
# =============================================================================

def add_stib_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "STIB", 10)

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    headers = ["Step / Check", "Expected Result", "Actual Record", "Y / N"]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    rows = [
        ["1. Selected Mode", "RM", "", ""],
        ["1. Localisation Status", "Out Loc", "", ""],
        ["2. Selected Mode", "RM", "", ""],
        ["2. Localisation Status", "Wait Inv", "", ""],
        ["2. Beacon Code", "Record expected / actual values", "", ""],
        ["2. Calibration", "Cal. Max", "", "mm"],
        ["3. Selected Mode", "RM", "", ""],
        ["3. Localisation Status", "Total Loc / Wait Beacon", "", ""],
        ["3. Mode Available", "CM/AM", "", ""],
        ["4. Selected Mode", "CM", "", ""],
        ["4. First RB / MTIB", "No Emergency Brake", "", ""],
    ]

    for values in rows:
        add_docx_row(table, values, point_size=8)

    set_table_font_size(table, 8)


# =============================================================================
# STOPPING POINT TEST TABLES
# LS03, LS04, LS05, LS06, LS10
# =============================================================================

def add_stopping_point_body(
    document: Any,
    row: pd.Series,
    test_type: str,
) -> None:
    titles = {
        "spacing": "Spacing Stopping Point – Track Circuit",
        "special": "Special Stopping Point – Signal Without Overlap",
        "complex": "Complex Stopping Point – Signal With Overlap",
        "point": "Convergence / Divergence",
        "esz": "Emergency Stop Zone",
    }

    add_heading(document, titles[test_type], 10)
    add_participate_anticipate_information(document, row)
    document.add_paragraph()

    table = document.add_table(rows=1, cols=5)
    table.style = "Table Grid"

    headers = [
        "Step / Test Item",
        "Expected Requirement",
        "Unit / Check",
        "Participate",
        "Anticipate",
    ]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 7)
        shade_cell(table.rows[0].cells[index])

    if test_type == "spacing":
        rows = [
            ["1. Track Circuit Occupied", "", "Y / N", "", ""],
            ["Available Mode", "CM/AUTO", "Y / N", "", ""],
            ["Localisation", "Loc Max (1)", "m", "", ""],
            ["", "Target Distance (2)", "m", "", ""],
            ["", "Abscissa (3)", "m", "", ""],
            ["", "(1) + (2) = (3), ±0.75 m where applicable", "Y/N/N/A", "", ""],
            ["OMAP Target Speed", "Equal 0", "kph", "", ""],
            ["Speedometer Target Speed", "Equal 0", "kph", "", ""],
            ["Associated Variant", "Restrictive", "Y / N", "", ""],
            ["2. Track Circuit Free", "", "Y / N", "", ""],
            ["Target Distance", ">= restrictive target where applicable", "m", "", ""],
            ["OMAP Target Speed", "Record", "kph", "", ""],
            ["Speedometer Target Speed", "> 0", "kph", "", ""],
            ["Associated Variant", "Permissive", "Y / N", "", ""],
            ["3. Variant Toggled", "", "Y / N", "", ""],
        ]

    elif test_type == "special":
        rows = [
            ["1. Restrictive Signal", "", "Y / N", "", ""],
            ["Available Mode", "CM/AUTO", "Y / N", "", ""],
            ["Localisation", "Loc Max (1)", "m", "", ""],
            ["", "Target Distance (2)", "m", "", ""],
            ["", "Abscissa (3)", "m", "", ""],
            ["", "(1) + (2) = (3), ±0.75 m where applicable", "Y/N/N/A", "", ""],
            ["OMAP Target Speed", "Equal 0", "kph", "", ""],
            ["Speedometer Target Speed", "Equal 0", "kph", "", ""],
            ["Associated Variant", "Restrictive", "Y / N", "", ""],
            ["2. Permissive Signal", "", "Y / N", "", ""],
            ["Target Distance", ">= restrictive target", "m", "", ""],
            ["OMAP Target Speed", "Record", "kph", "", ""],
            ["Speedometer Target Speed", "> 0", "kph", "", ""],
            ["Associated Variant", "Permissive", "Y / N", "", ""],
        ]

    elif test_type == "complex":
        rows = [
            ["1. Signal Red and Overlap Occupied", "", "Y / N", "", ""],
            ["Available Mode", "CM/AUTO", "Y / N", "", ""],
            ["Localisation", "Loc Max (1)", "m", "", ""],
            ["", "Target Distance (2)", "m", "", ""],
            ["", "Abscissa (3)", "m", "", ""],
            ["OMAP Target Speed", "Equal 0", "kph", "", ""],
            ["Speedometer Target Speed", "Equal 0", "kph", "", ""],
            ["Signal and Overlap Variants", "Restrictive", "Y / N", "", ""],
            ["2. Signal Red and Overlap Free", "", "Y / N", "", ""],
            ["Available Mode", "CM/AUTO", "Y / N", "", ""],
            ["Proceed Train in AM", "As applicable", "Y / N", "", ""],
            ["Localisation", "Loc Max (4)", "m", "", ""],
            ["", "Target Distance (5)", "m", "", ""],
            ["", "Abscissa (6)", "m", "", ""],
            ["Overlap Variant", "Permissive", "Y / N", "", ""],
            ["3. Signal Blue and Overlap Free", "", "Y / N", "", ""],
            ["Target Distance", ">= restrictive target", "m", "", ""],
            ["Signal and Overlap Variants", "Permissive", "Y / N", "", ""],
        ]

    elif test_type == "point":
        rows = [
            ["1. Point Uncontrolled", "", "Y / N", "", ""],
            ["Available Mode", "CM/AM", "Y / N", "", ""],
            ["Localisation", "Loc Max (1)", "m", "", ""],
            ["", "Target Distance (2)", "m", "", ""],
            ["", "Abscissa (3)", "m", "", ""],
            ["", "(1) + (2) = (3), ±0.75 m where applicable", "Y/N/N/A", "", ""],
            ["OMAP Target Speed", "Equal 0", "kph", "", ""],
            ["Speedometer Target Speed", "Equal 0", "kph", "", ""],
            ["Associated Variant", "Restrictive", "Y / N", "", ""],
            ["2. Point Controlled", "", "Y / N", "", ""],
            ["Target Distance", ">= restrictive target", "m", "", ""],
            ["OMAP Target Speed", "Record", "kph", "", ""],
            ["Speedometer Target Speed", "> 0", "kph", "", ""],
            ["Associated Variant", "Permissive", "Y / N", "", ""],
        ]

    else:
        rows = [
            ["1. ESZ Condition Active", "Associated Variant Restrictive", "Y / N", "", ""],
            ["Available Mode", "CM/AUTO", "Y / N", "", ""],
            ["Localisation", "Loc Max (1)", "m", "", ""],
            ["", "Target Distance (2)", "m", "", ""],
            ["", "Abscissa (3)", "m", "", ""],
            ["", "(1) + (2) = (3), ±8.25 m where applicable", "Y/N/N/A", "", ""],
            ["OMAP Target Speed", "Equal 0", "kph", "", ""],
            ["Speedometer Target Speed", "Equal 0", "kph", "", ""],
            ["2. ESZ Condition Resumed", "Associated Variant Permissive", "Y / N", "", ""],
            ["Target Distance", ">= restrictive target", "m", "", ""],
            ["OMAP Target Speed", "Record", "kph", "", ""],
            ["Speedometer Target Speed", "> 0", "kph", "", ""],
            ["3. Run in Restrictive Zone", "CM/AM unavailable", "Y / N", "", "N/A"],
            ["Run Until End of Zone", "CM available at end of zone", "Y / N", "", "N/A"],
        ]

    for values in rows:
        add_docx_row(table, values, point_size=7)

    set_table_font_size(table, 7)


# =============================================================================
# LS07 — END OF SACEM
# =============================================================================

def add_end_of_sacem_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "End of SACEM", 10)

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    headers = ["Check", "Expected Requirement", "Actual Record", "Y / N"]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    rows = [
        ["Available Mode", "CM/AUTO", "", ""],
        ["Localisation", "Loc Max (1)", "", "m"],
        ["", "Target Distance (2)", "", "m"],
        ["", "Abscissa (3)", "", "m"],
        ["", "(1) + (2) = (3)", "", "Y / N"],
        ["OMAP Target Speed", "Equal 0", "", "Y / N"],
        ["Speedometer Target Speed", "Equal 0", "", "Y / N"],
        ["Beyond End of SACEM", "CM/AM unavailable", "", "Y / N"],
    ]

    for values in rows:
        add_docx_row(table, values, point_size=8)

    set_table_font_size(table, 8)


# =============================================================================
# LS08 — STATION STOP / DOOR SIDE
# =============================================================================

def add_station_stop_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "Station Stopping Point and Door Side Verification", 10)

    station = safe_text(row.get("station_number"))
    platform = safe_text(row.get("platform"))

    document.add_paragraph(
        f"Station: {station or 'Engineering review required'}\n"
        f"Platform: {platform or 'Engineering review required'}"
    )

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    headers = ["Step / Check", "Expected Requirement", "Actual Record", "Y / N"]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    rows = [
        ["1. Localisation Status", "Total Loc", "", ""],
        ["Available Mode", "CM/AM", "", ""],
        ["Selected Mode", "CM", "", ""],
        ["Localisation", "Loc Max (1)", "", "m"],
        ["", "Precise Stop (2)", "", "m"],
        ["", "Measured Distance", "", "m"],
        ["", "Measured Distance < +0.35 m", "", "Y / N"],
        ["Train Door", "Correct Side Enabled", "", "A / B"],
        ["2. Stop Short", "Door Side Enabled", "", "Y / N"],
        ["", "Measured Distance > -0.35 m", "", "Y / N"],
        ["3. Overrun", "Door Side Enabled", "", "Y / N"],
        ["", "Measured Distance < +0.35 m", "", "Y / N"],
    ]

    for values in rows:
        add_docx_row(table, values, point_size=8)

    set_table_font_size(table, 8)


# =============================================================================
# LS09 — PSD
# =============================================================================

def add_psd_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "PSD Opening Pre-Test", 10)
    add_participate_anticipate_information(document, row)

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    headers = ["Step / Check", "Expected Requirement", "Actual Result", "Y / N"]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 7)
        shade_cell(table.rows[0].cells[index])

    rows = [
        ["1. Train Door", "Correct Side Enabled", "", ""],
        ["Available Mode", "CM/AM", "", ""],
        ["Selected Mode", "CM", "", ""],
        ["Open Doors – Incorrect Side", "OPEN relay not activated / PSD closed", "", ""],
        ["2. Open Doors – Correct Side", "OPEN relay activated / PSD opened", "", ""],
        ["All Door Closed", "Relay de-energised", "", ""],
        ["Associated Variants", "Restrictive", "", ""],
        ["Available Mode", "RM; no CM/AM", "", ""],
        ["3. PIOS / Override Active", "CM/AM available", "", ""],
        ["Associated Variants", "Permissive", "", ""],
        ["After 60 Seconds", "RM; no CM/AM", "", ""],
        ["Associated Variants", "Restrictive", "", ""],
        ["4. Close Doors – Correct Side", "CLOSE relay activated / PSD closed", "", ""],
        ["All Door Closed", "Relay energised", "", ""],
        ["Associated Variants", "Permissive", "", ""],
        ["Available Mode", "CM/AM", "", ""],
    ]

    for values in rows:
        add_docx_row(table, values, point_size=7)

    set_table_font_size(table, 7)


# =============================================================================
# LS11 — CHANGE OF END
# =============================================================================

def add_change_of_end_body(document: Any, row: pd.Series) -> None:
    reference = (
        safe_text(row.get("change_of_end_reference"))
        or safe_text(row.get("location_name"))
        or "Engineering Review Required"
    )

    add_heading(document, f"Change of End Zone – {reference}", 10)

    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    headers = ["Step / Check", "Expected Requirement", "Actual Record", "Y / N"]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    rows = [
        ["Step 1 – Cab 1 Arrival", "Selected Mode: CM", "", ""],
        ["", "Localisation Status: Total Loc", "", ""],
        ["", "CM/AUTO Available", "", ""],
        ["", "Loc Max", "", "m"],
        ["", "Loc Min", "", "m"],
        ["Step 2 – Cab 2 Departure", "Selected Mode: Shutdown", "", ""],
        ["", "Distance to Signal < 12 m", "", ""],
        ["Step 3 – Cab 2", "Selected Mode: CM", "", ""],
        ["", "Set Route", "", ""],
        ["", "Localisation Status: Total Loc", "", ""],
        ["", "CM/AUTO Available within 20 s", "", ""],
        ["", "Loc Max", "", "m"],
        ["", "Loc Min", "", "m"],
        ["Step 4 – Cab 1", "Localisation Status: Wait Cab", "", ""],
        ["1st RB", "No Delocalisation", "", ""],
        ["Step 4 – Cab 2", "1st RB / MTIB: No Delocalisation", "", ""],
    ]

    for values in rows:
        add_docx_row(table, values, point_size=8)

    set_table_font_size(table, 8)


# =============================================================================
# LS12 — TSR
# =============================================================================

def add_tsr_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "Temporary Speed Restriction (TSR)", 10)

    table = document.add_table(rows=1, cols=5)
    table.style = "Table Grid"

    headers = [
        "No.",
        "Track Circuit",
        "Section",
        "TSR Value (kph)",
        "Train Behaviour OK?",
    ]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    for number in range(1, 15):
        add_docx_row(
            table,
            [
                str(number),
                safe_text(row.get("track_circuit")) if number == 1 else "",
                safe_text(row.get("section")) if number == 1 else "",
                safe_text(row.get("tsr_value_kph")) if number == 1 else "",
                "",
            ],
            point_size=8,
        )

    set_table_font_size(table, 8)


# =============================================================================
# LS13 — RETURN CHANNEL
# =============================================================================

def add_return_channel_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "Return Channel Test", 10)

    table = document.add_table(rows=1, cols=5)
    table.style = "Table Grid"

    headers = [
        "No.",
        "Location",
        "Return Channel Loop",
        "OK?",
        "Remark",
    ]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 8)
        shade_cell(table.rows[0].cells[index])

    location = (
        safe_text(row.get("location_name"))
        or safe_text(row.get("subsection"))
    )

    channel = safe_text(row.get("return_channel_number"))

    for number in range(1, 15):
        add_docx_row(
            table,
            [
                str(number),
                location if number == 1 else "",
                channel if number == 1 else "",
                "",
                "",
            ],
            point_size=8,
        )

    set_table_font_size(table, 8)


# =============================================================================
# LS14 — END OF SHUNT
# =============================================================================

def add_end_of_shunt_body(document: Any, row: pd.Series) -> None:
    add_heading(document, "End of Shunt Test", 10)

    table = document.add_table(rows=2, cols=9)
    table.style = "Table Grid"

    headers = [
        "Shunt Route Name",
        "Step 1 – Begin\nBranch",
        "Step 1 – Begin\nLoc Max",
        "Step 1 – Begin\nTarget Distance",
        "Step 2\nRM",
        "Step 3 – End\nCM Resume",
        "Step 3 – End\nBranch",
        "Step 3 – End\nLoc Max",
        "OK?",
    ]

    for index, value in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], value, True, 7)
        shade_cell(table.rows[0].cells[index])

    values = [
        safe_text(row.get("shunt_route_name")),
        safe_text(row.get("branch")),
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]

    for index, value in enumerate(values):
        set_cell_text(table.rows[1].cells[index], value, point_size=7)

    document.add_paragraph()

    invariant_table = document.add_table(rows=1, cols=5)
    invariant_table.style = "Table Grid"

    headers = [
        "Shunt Route Name",
        "Begin Branch",
        "Begin Absolute Abscissa",
        "End Branch",
        "End Absolute Abscissa",
    ]

    for index, value in enumerate(headers):
        set_cell_text(invariant_table.rows[0].cells[index], value, True, 7)
        shade_cell(invariant_table.rows[0].cells[index])

    add_docx_row(
        invariant_table,
        [
            safe_text(row.get("shunt_route_name")),
            safe_text(row.get("branch")),
            format_distance(row.get("absolute_abscissa_m")),
            "",
            "",
        ],
        point_size=7,
    )

    set_table_font_size(table, 7)
    set_table_font_size(invariant_table, 7)


# =============================================================================
# DOCX TEST-SHEET DISPATCHER
# =============================================================================

def create_docx_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> bytes:
    """
    Generate a draft Appendix-A-style DOCX test sheet.

    This is a generated draft based on the extracted Subdivision PDF data.
    It is not an approved controlled replacement for the source procedure.
    """
    try:
        from docx import Document
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "Missing python-docx. Install python-docx>=1.1.2."
        ) from exc

    test_code = safe_text(row.get("test_code"))

    titles = {
        "LS01": ("Appendix A1. MTIB Initialization Test", "ATC-TNC-TRA-LS01"),
        "LS02": ("Appendix A2. STIB Initialization Test", "ATC-TNC-TRA-LS02"),
        "LS03": (
            "Appendix A3. Spacing Stopping Point Test – Track Circuit",
            "ATC-TNC-TRA-LS03",
        ),
        "LS04": (
            "Appendix A4. Special Stopping Point Test – Signal Without Overlap",
            "ATC-TNC-TRA-LS04",
        ),
        "LS05": (
            "Appendix A5. Complex Stopping Point Test – Signal With Overlap",
            "ATC-TNC-TRA-LS05",
        ),
        "LS06": (
            "Appendix A6. Stopping Point Test – Point",
            "ATC-TNC-TRA-LS06",
        ),
        "LS07": (
            "Appendix A7. Stopping Point Test – End of SACEM",
            "ATC-TNC-TRA-LS07",
        ),
        "LS08": (
            "Appendix A8. Station Stopping Point and Door Side Verification",
            "ATC-TNC-TRA-LS08",
        ),
        "LS09": (
            "Appendix A9. PSD Opening Pre-Test",
            "ATC-TNC-TRA-LS09",
        ),
        "LS10": (
            "Appendix A10. Emergency Stop Zone",
            "ATC-TNC-TRA-LS10",
        ),
        "LS11": (
            "Appendix A11. Change of End",
            "ATC-TNC-TRA-LS11",
        ),
        "LS12": (
            "Appendix A12. Temporary Speed Restriction",
            "ATC-TNC-TRA-LS12",
        ),
        "LS13": (
            "Appendix A13. Return Channel Test",
            "ATC-TNC-TRA-LS13",
        ),
        "LS14": (
            "Appendix A14. End of Shunt Test",
            "ATC-TNC-TRA-LS14",
        ),
    }

    if test_code not in titles:
        raise ValueError(f"Unknown test code: {test_code}")

    title, test_id = titles[test_code]

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.35)
    section.bottom_margin = Inches(0.35)
    section.left_margin = Inches(0.35)
    section.right_margin = Inches(0.35)

    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(8)

    add_appendix_a_header(
        document=document,
        row=row,
        project_info=project_info,
        sheet_number=sheet_number,
        test_title=title,
        test_id=test_id,
    )

    add_location_information(document, row)
    document.add_paragraph()

    if test_code == "LS01":
        add_mtib_body(document, row)

    elif test_code == "LS02":
        add_stib_body(document, row)

    elif test_code == "LS03":
        add_stopping_point_body(document, row, "spacing")

    elif test_code == "LS04":
        add_stopping_point_body(document, row, "special")

    elif test_code == "LS05":
        add_stopping_point_body(document, row, "complex")

    elif test_code == "LS06":
        add_stopping_point_body(document, row, "point")

    elif test_code == "LS07":
        add_end_of_sacem_body(document, row)

    elif test_code == "LS08":
        add_station_stop_body(document, row)

    elif test_code == "LS09":
        add_psd_body(document, row)

    elif test_code == "LS10":
        add_stopping_point_body(document, row, "esz")

    elif test_code == "LS11":
        add_change_of_end_body(document, row)

    elif test_code == "LS12":
        add_tsr_body(document, row)

    elif test_code == "LS13":
        add_return_channel_body(document, row)

    elif test_code == "LS14":
        add_end_of_shunt_body(document, row)

    add_comments_and_approval(document, row)
    add_footer(document)

    output = io.BytesIO()
    document.save(output)

    return output.getvalue()


# =============================================================================
# TXT EXPORT
# =============================================================================

def create_text_test_sheet(
    row: pd.Series,
    project_info: Dict[str, str],
    sheet_number: str,
) -> str:
    """Create a readable text version of the generated test sheet."""
    lines = [
        "=" * 100,
        "ALSTOM",
        "DRAFT – APPENDIX-A-STYLE ATC LOW-SPEED TEST SHEET",
        "=" * 100,
        "",
        f"Test Sheet No.: {sheet_number}",
        f"Test Code: {safe_text(row.get('test_code'))}",
        f"Test ID: {safe_text(row.get('test_id'))}",
        f"Test Name: {safe_text(row.get('test_name'))}",
        "",
        f"Project / Contract: {project_info.get('project', '')}",
        f"Procedure Reference: {project_info.get('procedure_reference', '')}",
        f"Procedure Title: {project_info.get('procedure_title', '')}",
        "",
        "-" * 100,
        "LOCATION / PLANNING INFORMATION",
        "-" * 100,
        f"Sector: {safe_text(row.get('sector'))}",
        f"Section: {safe_text(row.get('section'))}",
        f"Sub-section: {safe_text(row.get('subsection'))}",
        f"Branch: {safe_text(row.get('branch'))}",
        f"Direction: {safe_text(row.get('direction'))}",
        f"Event: {safe_text(row.get('event_name'))}",
        f"Location / Asset: {safe_text(row.get('location_name'))}",
        f"Associated Variant: {safe_text(row.get('associated_variant'))}",
        f"Absolute Chainage: {format_distance(row.get('absolute_abscissa_m'))}",
        f"Calculated Local Abscissa: {format_distance(row.get('abscissa_m'))}",
        "",
        f"Participating Variant: {safe_text(row.get('participating_variant'))}",
        f"Anticipated Variant: {safe_text(row.get('anticipated_variant'))}",
        f"Participating Sub-section(s): "
        f"{safe_text(row.get('participating_subsections'))}",
        f"Anticipated Sub-section(s): "
        f"{safe_text(row.get('anticipated_subsections'))}",
        f"Participating Branch(es): "
        f"{safe_text(row.get('participating_branches'))}",
        f"Anticipated Branch(es): "
        f"{safe_text(row.get('anticipated_branches'))}",
        "",
        "-" * 100,
        "SOURCE TRACEABILITY",
        "-" * 100,
        f"Source Page: {safe_text(row.get('source_page'))}",
        f"Schedule Row: {safe_text(row.get('row_number'))}",
        f"Singularity Code: {safe_text(row.get('singularity_code'))}",
        f"Calculation: {safe_text(row.get('calculation_log'))}",
        "",
        safe_text(row.get("source_evidence")),
        "",
        "-" * 100,
        "ENGINEERING CONTROL",
        "-" * 100,
        (
            "This is a draft machine-generated planning sheet. All locations, "
            "variants, branches, anticipated data, calculated abscissae and "
            "test applicability must be reviewed against approved project "
            "documents before use."
        ),
    ]

    return "\n".join(lines)


# =============================================================================
# EXCEL EXPORT
# =============================================================================

def create_excel_bytes(
    register: pd.DataFrame,
    project_info: Dict[str, str],
    singularities: Optional[pd.DataFrame] = None,
    variant_composition: Optional[pd.DataFrame] = None,
    transmission_calculations: Optional[pd.DataFrame] = None,
) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        cover = pd.DataFrame(
            [
                ["Project / Contract", project_info.get("project", "")],
                [
                    "Procedure Reference",
                    project_info.get("procedure_reference", ""),
                ],
                ["Procedure Title", project_info.get("procedure_title", "")],
                ["Generated Date", str(date.today())],
                ["Test Register Rows", len(register)],
                ["Extraction Method", "Marker-PDF only"],
                [
                    "Abscissa Calculation",
                    (
                        "Local Abscissa = Absolute Chainage - "
                        "Sub-section Start Chainage"
                    ),
                ],
                [
                    "Engineering Control",
                    (
                        "Draft planning output. Verify all data against the "
                        "approved source document before use."
                    ),
                ],
            ],
            columns=["Field", "Value"],
        )

        cover.to_excel(writer, sheet_name="Cover", index=False)
        register.to_excel(writer, sheet_name="Test Register", index=False)

        if singularities is not None:
            singularities.to_excel(
                writer,
                sheet_name="Singularities",
                index=False,
            )

        if variant_composition is not None:
            variant_composition.to_excel(
                writer,
                sheet_name="Variant Composition",
                index=False,
            )

        if transmission_calculations is not None:
            transmission_calculations.to_excel(
                writer,
                sheet_name="Transmission Calculation",
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
                ].width = min(max(maximum_length + 2, 12), 70)

    return output.getvalue()


# =============================================================================
# ZIP EXPORT
# =============================================================================

def create_test_sheet_export_zip(
    register: pd.DataFrame,
    project_info: Dict[str, str],
    pdf_text_log: str,
    singularities: pd.DataFrame,
    variant_composition: pd.DataFrame,
    transmission_calculations: pd.DataFrame,
    organised_source_log: str,
    progress_bar: Any,
    progress_text: Any,
) -> bytes:
    """
    Create the complete export ZIP.

    Contains:
    - Appendix-A-style DOCX sheets;
    - TXT equivalent sheets;
    - reviewed register CSV;
    - source extraction CSV files;
    - Marker-PDF audit logs.
    """
    output = io.BytesIO()
    total = len(register)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "PDF_Text_Extraction_Log.txt",
            safe_text(pdf_text_log),
        )

        archive.writestr(
            "Organised_Source_Extraction_Log.txt",
            safe_text(organised_source_log),
        )

        archive.writestr(
            "ATC_Low_Speed_Test_Register.csv",
            register.to_csv(index=False),
        )

        archive.writestr(
            "Singularities.csv",
            singularities.to_csv(index=False),
        )

        archive.writestr(
            "Variant_Composition.csv",
            variant_composition.to_csv(index=False),
        )

        archive.writestr(
            "Transmission_Anticipation_Calculations.csv",
            transmission_calculations.to_csv(index=False),
        )

        for sequence, (_, row) in enumerate(register.iterrows(), start=1):
            test_code = safe_text(row.get("test_code"))
            subsection = safe_text(row.get("subsection")) or "NO_SUBSECTION"

            sheet_prefix = (
                safe_text(project_info.get("sheet_prefix"))
                or safe_text(project_info.get("procedure_reference"))
                or "ATC-TEST"
            )

            sheet_number = f"{sheet_prefix}-{sequence:03d}"

            progress_text.info(
                f"Generating test sheet {sequence} of {total}: "
                f"{test_code} / {subsection}"
            )

            basename = clean_filename(
                f"{sheet_number}_{test_code}_{subsection}_"
                f"KP_{safe_text(row.get('abscissa_m'))}"
            )

            document_bytes = create_docx_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            text_sheet = create_text_test_sheet(
                row=row,
                project_info=project_info,
                sheet_number=sheet_number,
            )

            archive.writestr(
                f"Test_Sheets/{basename}.docx",
                document_bytes,
            )

            archive.writestr(
                f"Test_Sheets/{basename}.txt",
                text_sheet,
            )

            if total:
                progress_bar.progress(int(sequence / total * 100))

    progress_bar.progress(100)
    progress_text.success(
        f"Export completed: {total} Appendix-A-style DOCX and TXT test sheet(s)."
    )

    return output.getvalue()
