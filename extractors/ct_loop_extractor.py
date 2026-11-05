from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import fitz
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[float], None]

CT_PAGE_RE = re.compile(r"CONTINUOUS\s+TRANSMISSI", re.I)
DRAWING_RE = re.compile(
    r"\b(?P<number>\d+(?:/[A-Z0-9-]+)+/R\d+/\d+)"
    r"(?:/(?P<revision>[A-Z]{1,4}\d{1,3}))?\b",
    re.I,
)
ITEM_RE = re.compile(
    r"\bCT\.?\s*(?P<item>[A-Z][A-Z0-9-]*)"
    r"\s*\(\s*(?P<suffix>[A-Z0-9-]+)\s*\)",
    re.I,
)
TERMINAL_RE = re.compile(
    r"\b(?P<prefix>[A-Z]{1,6}\d{0,4})\s*[.:]\s*"
    r"(?P<column>[A-Z]{1,6})(?P<number>\d+)\b",
    re.I,
)
REVISION_RE = re.compile(r"^[A-Z]{1,4}\d{1,3}$", re.I)

MAX_WORD_WINDOW = 5
PAIR_X_TOLERANCE_RATIO = 0.012
PAIR_Y_MIN_RATIO = 0.008
PAIR_Y_MAX_RATIO = 0.055
CIRCUIT_Y_TOLERANCE_RATIO = 0.085

OUTPUT_COLUMNS = [
    "Include",
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


@dataclass(frozen=True)
class TextToken:
    text: str
    rect: fitz.Rect
    block: int
    line: int
    word_no: int

    @property
    def x(self) -> float:
        return (self.rect.x0 + self.rect.x1) / 2

    @property
    def y(self) -> float:
        return (self.rect.y0 + self.rect.y1) / 2


@dataclass(frozen=True)
class Terminal:
    name: str
    prefix: str
    column: str
    number: int
    rect: fitz.Rect

    @property
    def x(self) -> float:
        return (self.rect.x0 + self.rect.x1) / 2

    @property
    def y(self) -> float:
        return (self.rect.y0 + self.rect.y1) / 2


@dataclass(frozen=True)
class TerminalPair:
    first: Terminal
    second: Terminal

    @property
    def x(self) -> float:
        return (self.first.x + self.second.x) / 2

    @property
    def y(self) -> float:
        return (self.first.y + self.second.y) / 2

    @property
    def names(self) -> tuple[str, str]:
        return self.first.name, self.second.name


@dataclass(frozen=True)
class ItemLabel:
    name: str
    rect: fitz.Rect

    @property
    def x(self) -> float:
        return (self.rect.x0 + self.rect.x1) / 2

    @property
    def y(self) -> float:
        return (self.rect.y0 + self.rect.y1) / 2


def write_log(callback: LogCallback | None, message: str, level: str = "INFO") -> None:
    if callback is not None:
        callback(message, level)


def union_rects(rectangles: Iterable[fitz.Rect]) -> fitz.Rect:
    rectangles = list(rectangles)
    result = fitz.Rect(rectangles[0])
    for rectangle in rectangles[1:]:
        result.include_rect(rectangle)
    return result


def get_tokens(page: fitz.Page) -> list[TextToken]:
    tokens = []
    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, text, block, line, word_no = word[:8]
        tokens.append(
            TextToken(
                text=str(text),
                rect=fitz.Rect(x0, y0, x1, y1),
                block=int(block),
                line=int(line),
                word_no=int(word_no),
            )
        )
    return tokens


def token_windows(tokens: list[TextToken]) -> Iterable[list[TextToken]]:
    lines: defaultdict[tuple[int, int], list[TextToken]] = defaultdict(list)
    for token in tokens:
        lines[(token.block, token.line)].append(token)
    for line_tokens in lines.values():
        line_tokens.sort(key=lambda token: token.word_no)
        for start in range(len(line_tokens)):
            limit = min(MAX_WORD_WINDOW, len(line_tokens) - start)
            for size in range(1, limit + 1):
                yield line_tokens[start : start + size]


def extract_items(tokens: list[TextToken]) -> list[ItemLabel]:
    found: list[ItemLabel] = []
    seen: set[tuple[str, int, int]] = set()
    for window in token_windows(tokens):
        text = "".join(token.text for token in window).strip(" ,;")
        match = ITEM_RE.fullmatch(text)
        if not match:
            continue
        name = f"{match.group('item').upper()}({match.group('suffix').upper()})"
        rect = union_rects(token.rect for token in window)
        key = name, round(rect.x0), round(rect.y0)
        if key not in seen:
            seen.add(key)
            found.append(ItemLabel(name=name, rect=rect))
    return sorted(found, key=lambda item: (item.y, item.x))


def extract_terminals(tokens: list[TextToken]) -> list[Terminal]:
    found: list[Terminal] = []
    seen: set[tuple[str, int, int]] = set()
    for window in token_windows(tokens):
        text = "".join(token.text for token in window).strip(" ,;()[]{}")
        match = TERMINAL_RE.fullmatch(text)
        if not match:
            continue
        prefix = match.group("prefix").upper()
        # CT/TC labels and drawing cross-references are not terminal blocks.
        if not re.fullmatch(r"V\d+", prefix, re.I):
            continue
        column = match.group("column").upper()
        number = int(match.group("number"))
        name = f"{prefix}.{column}{number}"
        rect = union_rects(token.rect for token in window)
        key = name, round(rect.x0), round(rect.y0)
        if key not in seen:
            seen.add(key)
            found.append(Terminal(name, prefix, column, number, rect))
    return found


def make_terminal_pairs(page: fitz.Page, terminals: list[Terminal]) -> list[TerminalPair]:
    x_tolerance = max(8.0, page.rect.width * PAIR_X_TOLERANCE_RATIO)
    y_min = max(4.0, page.rect.height * PAIR_Y_MIN_RATIO)
    y_max = max(24.0, page.rect.height * PAIR_Y_MAX_RATIO)
    grouped: defaultdict[tuple[str, str], list[Terminal]] = defaultdict(list)
    for terminal in terminals:
        grouped[(terminal.prefix, terminal.column)].append(terminal)

    pairs: list[TerminalPair] = []
    used: set[tuple[str, int, int]] = set()
    for group in grouped.values():
        group.sort(key=lambda terminal: (terminal.x, terminal.y))
        for first in group:
            candidates = []
            for second in group:
                if second.number != first.number + 1:
                    continue
                dx = abs(first.x - second.x)
                dy = second.y - first.y
                if dx <= x_tolerance and y_min <= dy <= y_max:
                    candidates.append((dx + abs(dy - page.rect.height * 0.027), second))
            if not candidates:
                continue
            second = min(candidates, key=lambda candidate: candidate[0])[1]
            key = first.name, round(first.x), round(first.y)
            if key in used:
                continue
            used.add(key)
            pairs.append(TerminalPair(first, second))
    return sorted(pairs, key=lambda pair: (pair.y, pair.x))


def select_title_block_metadata(
    page: fitz.Page,
    tokens: list[TextToken],
) -> tuple[str, str]:
    page_height = float(page.rect.height)
    bottom_tokens = [token for token in tokens if token.y >= page_height * 0.72]
    candidates: list[tuple[float, str, str]] = []
    for window in token_windows(bottom_tokens):
        text = "".join(token.text for token in window).strip(" ,;()[]{}")
        match = DRAWING_RE.fullmatch(text)
        if not match:
            continue
        rect = union_rects(token.rect for token in window)
        number = match.group("number").upper()
        revision = (match.group("revision") or "").upper()
        candidates.append((rect.y1, number, revision))

    if candidates:
        _, drawing, revision = max(candidates, key=lambda value: value[0])
        if revision:
            return drawing, revision

    text = " ".join(token.text for token in tokens)
    all_matches = list(DRAWING_RE.finditer(text))
    if not all_matches:
        return "", "REVIEW"
    chosen = all_matches[-1]
    drawing = chosen.group("number").upper()
    revision = (chosen.group("revision") or "").upper()

    if not revision:
        # The file name in the title block commonly ends with the issue code.
        escaped = re.escape(drawing.replace("/", "_"))
        file_match = re.search(escaped + r"(?P<rev>[A-Z]{1,4}\d{1,3})\.DGN", text, re.I)
        if file_match:
            revision = file_match.group("rev").upper()
    return drawing, revision or "REVIEW"


def get_location(drawing_number: str) -> str:
    parts = drawing_number.split("/")
    for index, part in enumerate(parts):
        if re.fullmatch(r"R\d+", part, re.I) and index >= 2:
            return parts[index - 2].upper()
    return ""


def collapse_duplicate_items(
    items: list[ItemLabel],
    pairs: list[TerminalPair],
) -> list[ItemLabel]:
    """Keep the label occurrence nearest to its terminal-pair cluster."""
    grouped: defaultdict[str, list[ItemLabel]] = defaultdict(list)
    for item in items:
        grouped[item.name].append(item)
    collapsed = []
    for occurrences in grouped.values():
        if len(occurrences) == 1 or not pairs:
            collapsed.append(occurrences[0])
            continue
        collapsed.append(
            min(
                occurrences,
                key=lambda item: min(abs(item.y - pair.y) for pair in pairs),
            )
        )
    return sorted(collapsed, key=lambda item: (item.y, item.x))


def assign_pairs_to_items(
    page: fitz.Page,
    items: list[ItemLabel],
    pairs: list[TerminalPair],
) -> dict[ItemLabel, list[TerminalPair]]:
    assignments: dict[ItemLabel, list[TerminalPair]] = {item: [] for item in items}
    if not items:
        return assignments
    y_tolerance = max(45.0, page.rect.height * CIRCUIT_Y_TOLERANCE_RATIO)
    for pair in pairs:
        item = min(items, key=lambda candidate: abs(candidate.y - pair.y))
        if abs(item.y - pair.y) <= y_tolerance:
            assignments[item].append(pair)
    return assignments


def format_pair(pair: TerminalPair) -> str:
    return f"- {pair.first.name} & {pair.second.name}"


def derive_steps(pairs: list[TerminalPair]) -> tuple[str, str, str, list[str]]:
    """Derive G1 steps from the three terminal-pair lanes in the drawing.

    The algorithm is geometric. It does not inspect rack identities, terminal
    prefixes, item names, drawing numbers, or a predefined lookup table.
    """
    reasons: list[str] = []
    if len(pairs) < 3:
        return "", "", "", [f"only {len(pairs)} terminal pair(s) found"]

    # Keep the three pairs nearest to the median lane when noise is present.
    ordered = sorted(pairs, key=lambda pair: pair.x)
    if len(ordered) > 3:
        best_triplet = None
        best_score = math.inf
        for start in range(len(ordered) - 2):
            triplet = ordered[start : start + 3]
            gaps = [triplet[1].x - triplet[0].x, triplet[2].x - triplet[1].x]
            score = abs(gaps[0] - gaps[1]) + max(gaps) * 0.05
            if score < best_score:
                best_score = score
                best_triplet = triplet
        ordered = best_triplet or ordered[:3]
        reasons.append("more than three candidate terminal pairs found")

    left, middle, right = ordered
    step_6 = format_pair(middle)
    step_7 = f"{format_pair(right)}\n{format_pair(left)}"
    step_14 = format_pair(right)
    return step_6, step_7, step_14, reasons


def page_records(
    page: fitz.Page,
    page_number: int,
    log_callback: LogCallback | None = None,
) -> list[dict]:
    tokens = get_tokens(page)
    text = " ".join(token.text for token in tokens)
    upper_text = text.upper()
    if "CONTINUOUS" not in upper_text or "TRANSMISSI" not in upper_text:
        return []

    items = extract_items(tokens)
    terminals = extract_terminals(tokens)
    pairs = make_terminal_pairs(page, terminals)
    drawing, revision = select_title_block_metadata(page, tokens)
    if not drawing or not items or not pairs:
        return []

    assignments = assign_pairs_to_items(page, items, pairs)
    pairs_by_item: defaultdict[str, list[TerminalPair]] = defaultdict(list)
    for item, assigned_pairs in assignments.items():
        pairs_by_item[item.name].extend(assigned_pairs)

    records = []
    for item_name, item_pairs in pairs_by_item.items():
        unique_pairs = {pair.names: pair for pair in item_pairs}
        step_6, step_7, step_14, reasons = derive_steps(list(unique_pairs.values()))
        if not step_6 and not step_7:
            continue
        records.append(
            {
                "Include": True,
                "No.": 0,
                "Location": get_location(drawing),
                "Item": item_name,
                "Drawing No. Check list": drawing,
                "Drawing Rev.": revision,
                "A11 CoV Work Procedure Appendix": "G1",
                "G1 - Step 6": step_6,
                "G1 - Step 7": step_7,
                "G1 - Step 14": step_14,
                "PDF Page": page_number,
                "Review Status": "REVIEW REQUIRED" if reasons or revision == "REVIEW" else "Draft",
                "Engineer Remarks": (
                    "Extracted from terminal-pair geometry."
                    + (" Review reasons: " + "; ".join(reasons) + "." if reasons else "")
                ),
            }
        )
    write_log(
        log_callback,
        f"Page {page_number}: items={len(items)}, terminals={len(terminals)}, "
        f"pairs={len(pairs)}, rows={len(records)}.",
        "DEBUG",
    )
    return records


def _record_quality(record: dict) -> tuple[int, int, int]:
    return (
        record["Review Status"] != "REVIEW REQUIRED",
        record["Drawing Rev."] != "REVIEW",
        len(record["G1 - Step 7"] or ""),
    )


def extract_pdf(
    pdf_path: str | Path,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, dict[int, bytes]]:
    """Extract CT-loop rows from a vector PDF without drawing-specific tables."""
    started = time.perf_counter()
    pdf_path = Path(pdf_path)
    all_records: list[dict] = []

    with fitz.open(pdf_path) as document:
        total_pages = len(document)
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            all_records.extend(page_records(page, page_number, log_callback))
            if progress_callback is not None:
                progress_callback((page_number / max(total_pages, 1)) * 100)

    # Duplicate sheets can occur in combined issue packs. Retain the most
    # complete extraction for each drawing/item combination.
    selected: dict[tuple[str, str], dict] = {}
    for record in all_records:
        key = record["Drawing No. Check list"], record["Item"]
        current = selected.get(key)
        if current is None or _record_quality(record) > _record_quality(current):
            selected[key] = record

    dataframe = pd.DataFrame(selected.values(), columns=OUTPUT_COLUMNS)
    if not dataframe.empty:
        dataframe["_sort"] = pd.to_numeric(
            dataframe["Drawing No. Check list"].str.extract(r"/R\d+/(\d+)")[0],
            errors="coerce",
        ).fillna(10**9)
        dataframe = dataframe.sort_values(
            ["_sort", "PDF Page", "Item"], kind="stable"
        ).drop(columns="_sort").reset_index(drop=True)
        dataframe["No."] = range(1, len(dataframe) + 1)

    write_log(
        log_callback,
        f"Extraction complete: {len(dataframe)} rows in "
        f"{time.perf_counter() - started:.2f}s.",
    )
    if progress_callback is not None:
        progress_callback(100.0)
    return dataframe, {}


def export_excel(dataframe: pd.DataFrame, output_path: str | Path) -> Path:
    """Write the extracted CT-loop table to a formatted Excel workbook."""
    output_path = Path(output_path)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Links for CT Loop"

    worksheet.merge_cells("A1:A2")
    worksheet.merge_cells("B1:B2")
    worksheet.merge_cells("C1:C2")
    worksheet.merge_cells("D1:D2")
    worksheet.merge_cells("E1:E2")
    worksheet.merge_cells("F1:F2")
    worksheet.merge_cells("G1:H1")

    headers_1 = [
        "No.", "Location", "Item", "Drawing No. Check list", "Drawing Rev.",
        "A11 COV Work Procedure Appendix", "Check list Before Cut-in", "",
        "Check list After Cut-in",
    ]
    headers_2 = [
        "", "", "", "", "", "",
        "G1 - Step 6\nLinks are opened and secure with cable tie",
        "G1 - Step 7\nLinks are opened",
        "G1 - Step 14\nCABLE RACK terminal blocks are closed.",
    ]
    for column, value in enumerate(headers_1, 1):
        if column != 8:
            worksheet.cell(1, column, value)
    for column, value in enumerate(headers_2, 1):
        if column >= 7:
            worksheet.cell(2, column, value)

    worksheet.append(["I", "", "ATC - CT Loop", "", "", "", "", "", ""])
    for _, record in dataframe.iterrows():
        worksheet.append(
            [
                int(record["No."]), record["Location"], record["Item"],
                record["Drawing No. Check list"], record["Drawing Rev."],
                record["A11 CoV Work Procedure Appendix"],
                record["G1 - Step 6"], record["G1 - Step 7"], record["G1 - Step 14"],
            ]
        )

    dark_blue = PatternFill("solid", fgColor="1F4E78")
    light_blue = PatternFill("solid", fgColor="D9EAF7")
    for row in worksheet.iter_rows(min_row=1, max_row=2, min_col=1, max_col=9):
        for cell in row:
            cell.fill = dark_blue
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for cell in worksheet[3]:
        cell.fill = light_blue
        cell.font = Font(bold=True)
    for row in worksheet.iter_rows(min_row=4, max_row=worksheet.max_row, min_col=1, max_col=9):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)

    widths = [8, 12, 18, 34, 14, 22, 30, 38, 30]
    for column, width in enumerate(widths, 1):
        worksheet.column_dimensions[get_column_letter(column)].width = width
    worksheet.row_dimensions[1].height = 28
    worksheet.row_dimensions[2].height = 55
    worksheet.freeze_panes = "A4"
    worksheet.sheet_view.showGridLines = False
    worksheet.auto_filter.ref = f"A3:I{worksheet.max_row}"
    workbook.save(output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract CT-loop operations from a PDF.")
    parser.add_argument("pdf", type=Path, help="Input PDF")
    parser.add_argument("-o", "--output", type=Path, default=Path("ct_loop_output.xlsx"))
    args = parser.parse_args()
    dataframe, _ = extract_pdf(args.pdf)
    export_excel(dataframe, args.output)
    print(f"Created {args.output} with {len(dataframe)} row(s).")


if __name__ == "__main__":
    main()
