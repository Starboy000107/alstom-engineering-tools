from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Border
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.styles import Side
from openpyxl.utils import get_column_letter


EXPORT_COLUMNS = [
    "No.",
    "Location",
    "Item",
    "Drawing No. Check list",
    "Drawing Rev.",
    "A11 CoV Work Procedure Appendix",
    "G1 - Step 6",
    "G1 - Step 7",
    "G1 - Step 14",
    "PDF Page",
    "Review Status",
    "Engineer Remarks",
]


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
    workbook = Workbook()
    worksheet = workbook.active

    worksheet.title = "CT Loop Checklist"
    worksheet.sheet_view.showGridLines = False

    export_dataframe = dataframe.copy()

    if "Include" in export_dataframe.columns:
        export_dataframe = export_dataframe[
            export_dataframe["Include"] == True
        ].copy()

    for column in EXPORT_COLUMNS:
        if column not in export_dataframe.columns:
            export_dataframe[column] = ""

    export_dataframe = export_dataframe[
        EXPORT_COLUMNS
    ].copy()

    export_dataframe["No."] = range(
        1,
        len(export_dataframe) + 1,
    )

    black = "000000"
    white = "FFFFFF"
    before_cut_in_green = "92D050"
    after_cut_in_blue = "00B0F0"
    description_gray = "E7E6E6"
    data_row_green = "C6EFCE"

    thin_black = Side(
        style="thin",
        color=black,
    )

    for column in range(1, 7):
        worksheet.merge_cells(
            start_row=1,
            start_column=column,
            end_row=3,
            end_column=column,
        )

    worksheet.merge_cells("G1:H1")

    for column in range(10, 13):
        worksheet.merge_cells(
            start_row=1,
            start_column=column,
            end_row=3,
            end_column=column,
        )

    headers = {
        "A1": "No.",
        "B1": "Location",
        "C1": "Item",
        "D1": "Drawing No. Check list",
        "E1": "Drawing\nRev.",
        "F1": "A11 CoV Work Procedure\nAppendix",
        "J1": "PDF Page",
        "K1": "Review Status",
        "L1": "Engineer Remarks",
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

    worksheet["G1"] = "Check List Before Cut-in"
    worksheet["I1"] = "Check List After Cut-in"

    for cell_reference, colour in [
        ("G1", before_cut_in_green),
        ("I1", after_cut_in_blue),
    ]:
        cell = worksheet[cell_reference]
        cell.font = Font(
            bold=True,
            color=black,
        )
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        set_fill(cell, colour)

    checklist_headers = {
        "G2": "G1 - Step 6",
        "H2": "G1 - Step 7",
        "I2": "G1 - Step 14",
    }

    for cell_reference, value in checklist_headers.items():
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

        if cell_reference in ["G2", "H2"]:
            set_fill(
                cell,
                before_cut_in_green,
            )
        else:
            set_fill(
                cell,
                after_cut_in_blue,
            )

    descriptions = {
        "G3": (
            "Links are opened and secure "
            "with cable tie"
        ),
        "H3": "Links are opened",
        "I3": (
            "CABLE RACK terminal blocks "
            "are closed."
        ),
    }

    for cell_reference, value in descriptions.items():
        cell = worksheet[cell_reference]
        cell.value = value
        cell.font = Font(
            italic=True,
            size=9,
            color=black,
        )
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        set_fill(cell, description_gray)

    for row in range(1, 4):
        for column in range(1, 13):
            set_border(
                worksheet.cell(
                    row=row,
                    column=column,
                ),
                thin_black,
            )

    for row_number, record in enumerate(
        export_dataframe.itertuples(index=False),
        start=4,
    ):
        for column_number, value in enumerate(
            record,
            start=1,
        ):
            if pd.isna(value):
                value = ""

            cell = worksheet.cell(
                row=row_number,
                column=column_number,
                value=value,
            )

            set_fill(cell, data_row_green)

            if column_number in [
                1,
                2,
                5,
                6,
                10,
                11,
            ]:
                horizontal = "center"
            else:
                horizontal = "left"

            cell.alignment = Alignment(
                horizontal=horizontal,
                vertical="top",
                wrap_text=True,
            )

            cell.font = Font(
                size=10,
                color=black,
            )

            set_border(
                cell,
                thin_black,
            )

        worksheet.row_dimensions[
            row_number
        ].height = 54

    widths = {
        "A": 8,
        "B": 12,
        "C": 18,
        "D": 31,
        "E": 12,
        "F": 25,
        "G": 29,
        "H": 29,
        "I": 31,
        "J": 12,
        "K": 20,
        "L": 45,
    }

    for column_letter, width in widths.items():
        worksheet.column_dimensions[
            column_letter
        ].width = width

    worksheet.row_dimensions[1].height = 28
    worksheet.row_dimensions[2].height = 24
    worksheet.row_dimensions[3].height = 34

    worksheet.freeze_panes = "A4"

    worksheet.auto_filter.ref = (
        f"A3:{get_column_letter(len(EXPORT_COLUMNS))}"
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