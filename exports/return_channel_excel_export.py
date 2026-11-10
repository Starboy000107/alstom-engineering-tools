from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Border
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.styles import Side
from openpyxl.utils import get_column_letter


FUSE_OPEN_COLUMN = (
    "H3 - Check list Before Cut-in - "
    "Fuses are opened"
)

CABLE_RACK_COLUMN = (
    "H3 - Check list Before Cut-in - "
    "CABLE RACK terminal blocks are opened "
    "and secure with cable tie"
)

FUSE_CLOSED_COLUMN = (
    "H3 - Check list After Cut-in - "
    "Fuses are closed"
)


def set_fill(
    cell,
    colour: str,
):
    cell.fill = PatternFill(
        fill_type="solid",
        fgColor=colour,
    )


def set_border(
    cell,
    border_side,
):
    cell.border = Border(
        left=border_side,
        right=border_side,
        top=border_side,
        bottom=border_side,
    )


def build_excel(
    dataframe: pd.DataFrame,
) -> bytes:
    """
    Build Return Channel H3 Excel checklist.

    The Cable Rack terminal block column appears only when at least
    one selected record contains an extracted green-terminal value.
    """
    workbook = Workbook()
    worksheet = workbook.active

    worksheet.title = "Return Channel Checklist"
    worksheet.sheet_view.showGridLines = False

    export_dataframe = dataframe.copy()

    if "Include" in export_dataframe.columns:
        export_dataframe = export_dataframe[
            export_dataframe["Include"] == True
        ].copy()

    required_columns = [
        "No.",
        "Location",
        "Item",
        "Drawing No. Check list",
        "Drawing Rev.",
        "A11 CoV Work Procedure Appendix",
        FUSE_OPEN_COLUMN,
        CABLE_RACK_COLUMN,
        FUSE_CLOSED_COLUMN,
        "PDF Page",
        "Review Status",
        "Engineer Remarks",
    ]

    for column in required_columns:
        if column not in export_dataframe.columns:
            export_dataframe[column] = ""

    cable_rack_values = (
        export_dataframe[CABLE_RACK_COLUMN]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    include_cable_rack_column = bool(
        (cable_rack_values != "").any()
    )

    export_dataframe["No."] = range(
        1,
        len(export_dataframe) + 1,
    )

    black = "000000"
    white = "FFFFFF"
    before_cut_in_green = "92D050"
    after_cut_in_blue = "00B0F0"
    description_gray = "E7E6E6"
    section_gray = "D9D9D9"
    data_row_green = "C6EFCE"

    thin_black = Side(
        style="thin",
        color=black,
    )

    fixed_columns = [
        "No.",
        "Location",
        "Item",
        "Drawing No. Check list",
        "Drawing Rev.",
        "A11 CoV Work Procedure Appendix",
    ]

    for column_index in range(1, 7):
        worksheet.merge_cells(
            start_row=1,
            start_column=column_index,
            end_row=3,
            end_column=column_index,
        )

    before_start_column = 7
    before_columns_count = (
        2
        if include_cable_rack_column
        else 1
    )

    before_end_column = (
        before_start_column
        + before_columns_count
        - 1
    )

    after_column = before_end_column + 1
    pdf_page_column = after_column + 1
    review_status_column = after_column + 2
    remarks_column = after_column + 3
    final_column = remarks_column

    worksheet.merge_cells(
        start_row=1,
        start_column=before_start_column,
        end_row=1,
        end_column=before_end_column,
    )

    worksheet.merge_cells(
        start_row=1,
        start_column=after_column,
        end_row=1,
        end_column=after_column,
    )

    for column_index in [
        pdf_page_column,
        review_status_column,
        remarks_column,
    ]:
        worksheet.merge_cells(
            start_row=1,
            start_column=column_index,
            end_row=3,
            end_column=column_index,
        )

    headers = {
        "A1": "No.",
        "B1": "Location",
        "C1": "Item",
        "D1": "Drawing No. Check list",
        "E1": "Drawing\nRev.",
        "F1": (
            "A11 CoV Work Procedure\n"
            "Appendix"
        ),
    }

    for cell_reference, value in headers.items():
        cell = worksheet[cell_reference]
        cell.value = value
        cell.font = Font(
            bold=True,
            color=black,
        )
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        set_fill(cell, white)

    worksheet.cell(
        row=1,
        column=before_start_column,
        value="Check list Before Cut-in",
    )

    worksheet.cell(
        row=1,
        column=after_column,
        value="Check list After Cut-in",
    )

    worksheet.cell(
        row=1,
        column=pdf_page_column,
        value="PDF Page",
    )

    worksheet.cell(
        row=1,
        column=review_status_column,
        value="Review Status",
    )

    worksheet.cell(
        row=1,
        column=remarks_column,
        value="Engineer Remarks",
    )

    worksheet.cell(
        row=2,
        column=before_start_column,
        value="Fuses are opened",
    )

    if include_cable_rack_column:
        worksheet.cell(
            row=2,
            column=before_start_column + 1,
            value=(
                "CABLE RACK terminal blocks are "
                "opened and secure with cable tie"
            ),
        )

    worksheet.cell(
        row=2,
        column=after_column,
        value="Fuses are closed",
    )

    for row in range(1, 4):
        for column in range(1, final_column + 1):
            cell = worksheet.cell(
                row=row,
                column=column,
            )

            cell.font = Font(
                bold=(row <= 2),
                color=black,
                size=10,
            )

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )

            set_border(
                cell,
                thin_black,
            )

            if row == 1:
                set_fill(cell, white)

    for column in range(
        before_start_column,
        before_end_column + 1,
    ):
        set_fill(
            worksheet.cell(
                row=1,
                column=column,
            ),
            before_cut_in_green,
        )

        set_fill(
            worksheet.cell(
                row=2,
                column=column,
            ),
            before_cut_in_green,
        )

    set_fill(
        worksheet.cell(
            row=1,
            column=after_column,
        ),
        after_cut_in_blue,
    )

    set_fill(
        worksheet.cell(
            row=2,
            column=after_column,
        ),
        after_cut_in_blue,
    )

    worksheet.row_dimensions[1].height = 28
    worksheet.row_dimensions[2].height = 44
    worksheet.row_dimensions[3].height = 12

    # Sort drawing numbers numerically.
    export_dataframe["_drawing_sort"] = (
        export_dataframe["Drawing No. Check list"]
        .astype(str)
        .str.extract(r"/R\d+/(\d+)")[0]
    )

    export_dataframe["_drawing_sort"] = pd.to_numeric(
        export_dataframe["_drawing_sort"],
        errors="coerce",
    ).fillna(999999999)

    export_dataframe = export_dataframe.sort_values(
        by=[
            "Location",
            "_drawing_sort",
            "PDF Page",
            "No.",
        ],
        kind="stable",
    ).reset_index(drop=True)

    export_dataframe = export_dataframe.drop(
        columns=["_drawing_sort"]
    )

    current_row = 4
    current_location = None
    output_number = 1

    for _, record in export_dataframe.iterrows():
        location = str(
            record.get("Location", "")
        ).strip()

        if location != current_location:
            current_location = location

            for column in range(1, final_column + 1):
                cell = worksheet.cell(
                    row=current_row,
                    column=column,
                )

                set_fill(cell, section_gray)
                set_border(
                    cell,
                    thin_black,
                )

                cell.font = Font(
                    bold=True,
                    color=black,
                    size=10,
                )

                cell.alignment = Alignment(
                    horizontal="left",
                    vertical="center",
                    wrap_text=True,
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

            worksheet.row_dimensions[
                current_row
            ].height = 30

            current_row += 1

        values = [
            output_number,
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
            record.get(FUSE_OPEN_COLUMN, ""),
        ]

        if include_cable_rack_column:
            values.append(
                record.get(CABLE_RACK_COLUMN, "")
            )

        values.extend(
            [
                record.get(FUSE_CLOSED_COLUMN, ""),
                record.get("PDF Page", ""),
                record.get("Review Status", ""),
                record.get("Engineer Remarks", ""),
            ]
        )

        for column, value in enumerate(
            values,
            start=1,
        ):
            if pd.isna(value):
                value = ""

            cell = worksheet.cell(
                row=current_row,
                column=column,
                value=value,
            )

            set_fill(cell, data_row_green)
            set_border(
                cell,
                thin_black,
            )

            cell.font = Font(
                size=10,
                color=black,
            )

            if column in [
                1,
                2,
                5,
                6,
                pdf_page_column,
                review_status_column,
            ]:
                horizontal = "center"
            else:
                horizontal = "left"

            cell.alignment = Alignment(
                horizontal=horizontal,
                vertical="top",
                wrap_text=True,
            )

        worksheet.row_dimensions[
            current_row
        ].height = 54

        current_row += 1
        output_number += 1

    widths = {
        1: 8,
        2: 12,
        3: 18,
        4: 31,
        5: 12,
        6: 25,
        7: 33,
        8: 34,
        9: 33,
        10: 12,
        11: 20,
        12: 45,
    }

    for column, width in widths.items():
        if column <= final_column:
            worksheet.column_dimensions[
                get_column_letter(column)
            ].width = width

    worksheet.freeze_panes = "A4"

    worksheet.auto_filter.ref = (
        f"A3:{get_column_letter(final_column)}"
        f"{max(worksheet.max_row, 4)}"
    )

    worksheet.page_setup.orientation = "landscape"
    worksheet.page_setup.fitToWidth = 1
    worksheet.page_setup.fitToHeight = 0
    worksheet.sheet_properties.pageSetUpPr.fitToPage = True

    output = BytesIO()

    workbook.save(output)
    output.seek(0)

    return output.getvalue()
