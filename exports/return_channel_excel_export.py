from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import (
    Alignment,
    Border,
    Font,
    PatternFill,
    Side,
)
from openpyxl.utils import get_column_letter


# ---------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------

HEADER_FILL = "FFFFFF"
GROUP_BEFORE_FILL = "92D050"
GROUP_AFTER_FILL = "5BC0DE"
SUBHEADER_FILL = "FFFFFF"
SECTION_FILL = "D9D9D9"

THIN_BLACK = Side(
    style="thin",
    color="000000",
)

BORDER = Border(
    left=THIN_BLACK,
    right=THIN_BLACK,
    top=THIN_BLACK,
    bottom=THIN_BLACK,
)


# ---------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------

def build_excel(
    dataframe: pd.DataFrame,
) -> bytes:
    """
    Build Return Channel H3 checklist Excel output.

    The optional Cable Rack terminal column is included only when
    at least one extracted row has a non-empty value in:

        H3 - Check list Before Cut-in -
        CABLE RACK terminal blocks are opened and secure with cable tie
    """
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Return Channel"

    terminal_column = (
        "H3 - Check list Before Cut-in - "
        "CABLE RACK terminal blocks are opened and secure "
        "with cable tie"
    )

    fuse_open_column = (
        "H3 - Check list Before Cut-in - "
        "Fuses are opened"
    )

    fuse_closed_column = (
        "H3 - Check list After Cut-in - "
        "Fuses are closed"
    )

    include_terminal_column = False

    if terminal_column in dataframe.columns:
        terminal_values = (
            dataframe[terminal_column]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        include_terminal_column = bool(
            (terminal_values != "").any()
        )

    base_columns = [
        "No.",
        "Location",
        "Item",
        "Drawing No. Check list",
        "Drawing Rev.",
        "A11 CoV Work Procedure Appendix",
    ]

    output_columns = [
        fuse_open_column,
    ]

    if include_terminal_column:
        output_columns.append(
            terminal_column
        )

    output_columns.append(
        fuse_closed_column
    )

    all_columns = base_columns + output_columns

    for column in all_columns:
        if column not in dataframe.columns:
            dataframe[column] = ""

    # --------------------------------------------------------------
    # Header rows
    # --------------------------------------------------------------

    # Row 1: Group headings.
    worksheet.merge_cells(
        start_row=1,
        start_column=1,
        end_row=2,
        end_column=1,
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=2,
        end_row=2,
        end_column=2,
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=3,
        end_row=2,
        end_column=3,
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=4,
        end_row=2,
        end_column=4,
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=5,
        end_row=2,
        end_column=5,
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=6,
        end_row=2,
        end_column=6,
    )

    worksheet.cell(
        row=1,
        column=1,
        value="No.",
    )

    worksheet.cell(
        row=1,
        column=2,
        value="Location",
    )

    worksheet.cell(
        row=1,
        column=3,
        value="Item",
    )

    worksheet.cell(
        row=1,
        column=4,
        value="Drawing No. Check list",
    )

    worksheet.cell(
        row=1,
        column=5,
        value="Drawing Rev.",
    )

    worksheet.cell(
        row=1,
        column=6,
        value=(
            "A11 COV Work Procedure\n"
            "Appendix"
        ),
    )

    before_start_column = 7
    before_end_column = (
        before_start_column
        + len(output_columns)
        - 2
    )

    after_column = before_end_column + 1

    worksheet.merge_cells(
        start_row=1,
        start_column=before_start_column,
        end_row=1,
        end_column=before_end_column,
    )

    worksheet.cell(
        row=1,
        column=before_start_column,
        value="Check list Before Cut-in",
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=after_column,
        end_row=1,
        end_column=after_column,
    )

    worksheet.cell(
        row=1,
        column=after_column,
        value="Check list After Cut-in",
    )

    # Row 2: Actual checklist columns.
    worksheet.cell(
        row=2,
        column=before_start_column,
        value="Fuses are opened",
    )

    current_column = before_start_column + 1

    if include_terminal_column:
        worksheet.cell(
            row=2,
            column=current_column,
            value=(
                "CABLE RACK terminal blocks are opened "
                "and secure with cable tie"
            ),
        )

        current_column += 1

    worksheet.cell(
        row=2,
        column=after_column,
        value="Fuses are closed",
    )

    # --------------------------------------------------------------
    # Header formatting
    # --------------------------------------------------------------

    for row in range(1, 3):
        for column in range(
            1,
            after_column + 1,
        ):
            cell = worksheet.cell(
                row=row,
                column=column,
            )

            cell.border = BORDER
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )

            cell.font = Font(
                bold=True,
                size=10,
            )

            if row == 1:
                cell.fill = PatternFill(
                    fill_type="solid",
                    fgColor=HEADER_FILL,
                )

            else:
                cell.fill = PatternFill(
                    fill_type="solid",
                    fgColor=SUBHEADER_FILL,
                )

    for column in range(
        before_start_column,
        before_end_column + 1,
    ):
        worksheet.cell(
            row=1,
            column=column,
        ).fill = PatternFill(
            fill_type="solid",
            fgColor=GROUP_BEFORE_FILL,
        )

    worksheet.cell(
        row=1,
        column=after_column,
    ).fill = PatternFill(
        fill_type="solid",
        fgColor=GROUP_AFTER_FILL,
    )

    worksheet.row_dimensions[1].height = 28
    worksheet.row_dimensions[2].height = 46

    # --------------------------------------------------------------
    # Optional location section headings
    # --------------------------------------------------------------

    sorted_dataframe = dataframe.copy()

    drawing_number_sort = (
        sorted_dataframe[
            "Drawing No. Check list"
        ]
        .astype(str)
        .str.extract(
            r"/R\d+/(\d+)"
        )[0]
    )

    sorted_dataframe["_drawing_number_sort"] = (
        pd.to_numeric(
            drawing_number_sort,
            errors="coerce",
        )
        .fillna(999999999)
    )

    sorted_dataframe = sorted_dataframe.sort_values(
        by=[
            "Location",
            "_drawing_number_sort",
            "No.",
        ],
        kind="stable",
    )

    sorted_dataframe = sorted_dataframe.drop(
        columns=["_drawing_number_sort"]
    )

    current_row = 3
    current_location = None

    for _, record in sorted_dataframe.iterrows():
        location = str(
            record.get("Location", "")
        ).strip()

        if location != current_location:
            current_location = location

            for column in range(
                1,
                after_column + 1,
            ):
                cell = worksheet.cell(
                    row=current_row,
                    column=column,
                )

                cell.border = BORDER
                cell.fill = PatternFill(
                    fill_type="solid",
                    fgColor=SECTION_FILL,
                )

                cell.alignment = Alignment(
                    vertical="center",
                    wrap_text=True,
                )

                cell.font = Font(
                    bold=True,
                    size=10,
                )

            worksheet.cell(
                row=current_row,
                column=1,
                value="I",
            )

            worksheet.cell(
                row=current_row,
                column=2,
                value=location,
            )

            worksheet.cell(
                row=current_row,
                column=3,
                value="ATC - Return Channel",
            )

            current_row += 1

        values = [
            record.get("No.", ""),
            record.get("Location", ""),
            record.get("Item", ""),
            record.get(
                "Drawing No. Check list",
                "",
            ),
            record.get("Drawing Rev.", ""),
            record.get(
                "A11 CoV Work Procedure Appendix",
                "",
            ),
            record.get(fuse_open_column, ""),
        ]

        if include_terminal_column:
            values.append(
                record.get(
                    terminal_column,
                    "",
                )
            )

        values.append(
            record.get(
                fuse_closed_column,
                "",
            )
        )

        for column, value in enumerate(
            values,
            start=1,
        ):
            cell = worksheet.cell(
                row=current_row,
                column=column,
                value=value,
            )

            cell.border = BORDER
            cell.alignment = Alignment(
                vertical="top",
                horizontal=(
                    "center"
                    if column <= 6
                    else "left"
                ),
                wrap_text=True,
            )

            cell.font = Font(
                size=10,
            )

        worksheet.row_dimensions[
            current_row
        ].height = 48

        current_row += 1

    # --------------------------------------------------------------
    # Column widths
    # --------------------------------------------------------------

    widths = {
        1: 8,
        2: 13,
        3: 20,
        4: 30,
        5: 12,
        6: 20,
        7: 34,
        8: 34,
        9: 34,
    }

    for column_index, width in widths.items():
        if column_index <= after_column:
            worksheet.column_dimensions[
                get_column_letter(column_index)
            ].width = width

    worksheet.freeze_panes = "A3"

    output = BytesIO()

    workbook.save(output)

    return output.getvalue()
