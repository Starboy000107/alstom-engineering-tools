from __future__ import annotations

import colorsys
import math
import re
import time
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd


# ---------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------

DRAWING_PATTERN = re.compile(
    r"\b\d+(?:/[A-Z0-9-]+)+/R\d+/\d+(?:/[A-Z0-9-]+)*\b",
    re.IGNORECASE,
)

RETURN_CHANNEL_PATTERN = re.compile(
    r"\bRETURN\s+CHANNEL\b",
    re.IGNORECASE,
)

TERMINAL_PATTERN = re.compile(
    r"\b"
    r"(?P<rack>V\d+)"
    r"\s*[:.]\s*"
    r"(?P<column>[A-Z]+)"
    r"(?P<number>\d+(?:[.-]\d+)*)"
    r"\b",
    re.IGNORECASE,
)

# Generic Item identifier:
#
# AQ1
# ATC1
# AY1A
# UT1
# GW2
# X-12A
#
# Must:
# - start with a letter;
# - contain at least one digit;
# - allow letters, digits and hyphens.
ITEM_IDENTIFIER_PATTERN = (
    r"(?=[A-Z0-9-]*\d)"
    r"[A-Z][A-Z0-9-]*"
)

# Examples:
# TC.AQ1
# CT.AY1A
# TC.UT1(UP)
# CT.GW2
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    rf"(?P<item>{ITEM_IDENTIFIER_PATTERN})"
    r"(?:\s*\(\s*"
    r"(?P<state>[A-Z0-9-]+)"
    r"\s*\))?",
    re.IGNORECASE,
)

# Examples:
# X1.f1
# X3.F10
# X12-f3
FUSE_PATTERN = re.compile(
    r"\b"
    r"(?P<board>X\d+)"
    r"\s*[:.-]?\s*"
    r"(?P<fuse>F\d+)"
    r"\b",
    re.IGNORECASE,
)

NS1_RACK_PATTERN = re.compile(
    r"\bNS\d+\s+RACK\s+[A-Z0-9-]+\b",
    re.IGNORECASE,
)

IXL_CUBICLE_PATTERN = re.compile(
    r"\b"
    r"(?:IXL\s*/\s*)?"
    r"SACEM\s+COV\s+CUBICLE"
    r"\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Controlled normalisation
# ---------------------------------------------------------------------

DRAWING_SEGMENT_ALIASES = {}

ITEM_STATE_ALIASES = {}


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

def write_log(
    log_callback,
    message: str,
    level: str = "INFO",
):
    if log_callback is not None:
        log_callback(message, level)


# ---------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------

def normalise_text(
    value: str,
) -> str:
    return re.sub(
        r"[^A-Z0-9]+",
        " ",
        str(value).upper(),
    ).strip()


def normalise_drawing_number(
    drawing_number: str,
) -> str:
    if not drawing_number:
        return ""

    parts = drawing_number.upper().strip().split("/")

    return "/".join(
        DRAWING_SEGMENT_ALIASES.get(part, part)
        for part in parts
    )


def normalise_item_state(
    state: str,
) -> str:
    state = str(state).upper().strip()

    return ITEM_STATE_ALIASES.get(
        state,
        state,
    )


def point_distance(
    first: fitz.Point,
    second: fitz.Point,
) -> float:
    return math.hypot(
        first.x - second.x,
        first.y - second.y,
    )


def point_to_segment_distance(
    point: fitz.Point,
    start: fitz.Point,
    end: fitz.Point,
) -> float:
    dx = end.x - start.x
    dy = end.y - start.y

    length_squared = dx * dx + dy * dy

    if length_squared == 0:
        return point_distance(point, start)

    t = (
        (point.x - start.x) * dx
        + (point.y - start.y) * dy
    ) / length_squared

    t = max(0.0, min(1.0, t))

    closest_point = fitz.Point(
        start.x + t * dx,
        start.y + t * dy,
    )

    return point_distance(
        point,
        closest_point,
    )


def rgb_to_hsv(
    colour,
) -> tuple[float, float, float] | None:
    if not colour or len(colour) < 3:
        return None

    red, green, blue = colour[:3]

    if max(red, green, blue) > 1:
        red /= 255
        green /= 255
        blue /= 255

    hue, saturation, value = colorsys.rgb_to_hsv(
        red,
        green,
        blue,
    )

    return (
        hue * 360,
        saturation,
        value,
    )


def parse_terminal_number(
    value: str,
) -> tuple[int, ...]:
    return tuple(
        int(part)
        for part in re.split(
            r"[.-]",
            str(value),
        )
        if part.isdigit()
    )


def terminal_numbers_are_consecutive(
    first_terminal: dict,
    second_terminal: dict,
) -> bool:
    first_parts = first_terminal[
        "number_parts"
    ]

    second_parts = second_terminal[
        "number_parts"
    ]

    if not first_parts or not second_parts:
        return False

    if len(first_parts) != len(second_parts):
        return False

    if len(first_parts) == 1:
        return (
            second_parts[0]
            == first_parts[0] + 1
        )

    return (
        first_parts[:-1] == second_parts[:-1]
        and second_parts[-1]
        == first_parts[-1] + 1
    )


def format_lines(
    values: list[str],
) -> str:
    return "\n".join(
        f"- {value}"
        for value in values
        if value
    )


# ---------------------------------------------------------------------
# Drawing metadata
# ---------------------------------------------------------------------

def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.35,
) -> list[dict]:
    page_height = float(page.rect.height)
    title_top = page_height * (
        1 - bottom_ratio
    )

    words = []

    for word in page.get_text(
        "words",
        sort=True,
    ):
        x0, y0, x1, y1, value = word[:5]

        if (y0 + y1) / 2 < title_top:
            continue

        words.append(
            {
                "value": value.strip(
                    " ,;:()[]{}"
                ),
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "x_center": (
                    x0 + x1
                ) / 2,
                "y_center": (
                    y0 + y1
                ) / 2,
            }
        )

    return words


def get_title_block_text(
    page,
) -> str:
    return " ".join(
        word["value"]
        for word in get_bottom_title_block_words(
            page
        )
    )


def page_has_return_channel(
    page,
    page_text: str,
) -> tuple[bool, str]:
    if RETURN_CHANNEL_PATTERN.search(
        normalise_text(page_text)
    ):
        return True, "PAGE TEXT"

    title_block_text = get_title_block_text(page)

    if RETURN_CHANNEL_PATTERN.search(
        normalise_text(title_block_text)
    ):
        return True, "TITLE BLOCK"

    return False, "NOT FOUND"


def get_drawing_number(
    page,
) -> str:
    title_block_text = get_title_block_text(page)

    matches = list(
        DRAWING_PATTERN.finditer(
            title_block_text
        )
    )

    if not matches:
        return ""

    return normalise_drawing_number(
        matches[-1].group(0)
    )


def get_revision(
    page,
) -> str:
    words = get_bottom_title_block_words(page)

    revision_labels = [
        word
        for word in words
        if word["value"].upper()
        in {
            "REV",
            "REV.",
            "REVISION",
        }
    ]

    if not revision_labels:
        return "REVIEW"

    candidates = []

    for revision_label in revision_labels:
        for word in words:
            value = word["value"].upper()

            if not re.fullmatch(
                r"[A-Z]{1,3}\d{1,3}",
                value,
            ):
                continue

            if word["x_center"] <= revision_label["x1"]:
                continue

            vertical_distance = abs(
                word["y_center"]
                - revision_label["y_center"]
            )

            if vertical_distance > 20:
                continue

            horizontal_distance = (
                word["x0"]
                - revision_label["x1"]
            )

            candidates.append(
                (
                    horizontal_distance
                    + vertical_distance * 5,
                    value,
                )
            )

    if not candidates:
        return "REVIEW"

    candidates.sort()

    return candidates[0][1]


def get_location_from_drawing_number(
    drawing_number: str,
) -> str:
    parts = drawing_number.upper().split("/")

    for index, part in enumerate(parts):
        if re.fullmatch(r"R\d+", part):
            if index >= 2:
                return parts[index - 2]

    return ""


# ---------------------------------------------------------------------
# Item extraction
# ---------------------------------------------------------------------

def extract_item_labels(
    page,
) -> list[dict]:
    page_text = page.get_text(
        "text",
        sort=True,
    )

    labels = []
    seen = set()

    for match in ITEM_PATTERN.finditer(page_text):
        item_code = match.group("item").upper()

        state = normalise_item_state(
            match.group("state") or ""
        )

        item_name = item_code

        if state:
            item_name = (
                f"{item_code}({state})"
            )

        source_text = match.group(0)

        rectangles = page.search_for(
            source_text,
            quads=False,
        )

        for rectangle in rectangles:
            key = (
                item_name,
                round(rectangle.x0, 1),
                round(rectangle.y0, 1),
            )

            if key in seen:
                continue

            seen.add(key)

            labels.append(
                {
                    "item": item_name,
                    "x_center": round(
                        (
                            rectangle.x0
                            + rectangle.x1
                        ) / 2,
                        2,
                    ),
                    "y_center": round(
                        (
                            rectangle.y0
                            + rectangle.y1
                        ) / 2,
                        2,
                    ),
                    "point": fitz.Point(
                        (
                            rectangle.x0
                            + rectangle.x1
                        ) / 2,
                        (
                            rectangle.y0
                            + rectangle.y1
                        ) / 2,
                    ),
                }
            )

    return labels


# ---------------------------------------------------------------------
# Terminal extraction
# ---------------------------------------------------------------------

def extract_terminal_references(
    page,
) -> list[dict]:
    terminals = []
    seen = set()

    for word in page.get_text(
        "words",
        sort=True,
    ):
        x0, y0, x1, y1, value = word[:5]

        match = TERMINAL_PATTERN.fullmatch(
            value.strip(" ,;:()[]{}")
        )

        if not match:
            continue

        rack = match.group("rack").upper()
        column = match.group("column").upper()
        number = match.group("number")

        terminal_text = (
            f"{rack}.{column}{number}"
        )

        x_center = round(
            (x0 + x1) / 2,
            2,
        )

        y_center = round(
            (y0 + y1) / 2,
            2,
        )

        key = (
            terminal_text,
            round(x_center, 1),
            round(y_center, 1),
        )

        if key in seen:
            continue

        seen.add(key)

        terminals.append(
            {
                "terminal": terminal_text,
                "rack": rack,
                "column": column,
                "number": number,
                "number_parts": (
                    parse_terminal_number(number)
                ),
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "x_center": x_center,
                "y_center": y_center,
                "point": fitz.Point(
                    x_center,
                    y_center,
                ),
            }
        )

    return terminals


def build_terminal_pairs(
    page,
    terminals: list[dict],
) -> list[dict]:
    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_pair_distance = max(
        30.0,
        min(page_scale * 0.18, 250.0),
    )

    candidates = []

    for first_index, first_terminal in enumerate(
        terminals
    ):
        for second_terminal in terminals[
            first_index + 1:
        ]:
            if (
                first_terminal["rack"]
                != second_terminal["rack"]
            ):
                continue

            if (
                first_terminal["column"]
                != second_terminal["column"]
            ):
                continue

            if not terminal_numbers_are_consecutive(
                first_terminal,
                second_terminal,
            ):
                continue

            pair_distance = point_distance(
                first_terminal["point"],
                second_terminal["point"],
            )

            if pair_distance > maximum_pair_distance:
                continue

            x_center = round(
                (
                    first_terminal["x_center"]
                    + second_terminal["x_center"]
                ) / 2,
                2,
            )

            y_center = round(
                (
                    first_terminal["y_center"]
                    + second_terminal["y_center"]
                ) / 2,
                2,
            )

            candidates.append(
                {
                    "text": (
                        f"{first_terminal['terminal']} & "
                        f"{second_terminal['terminal']}"
                    ),
                    "x_center": x_center,
                    "y_center": y_center,
                    "point": fitz.Point(
                        x_center,
                        y_center,
                    ),
                    "pair_distance": pair_distance,
                    "terminals": [
                        first_terminal,
                        second_terminal,
                    ],
                }
            )

    return candidates


# ---------------------------------------------------------------------
# Green circuit detection
# ---------------------------------------------------------------------

def drawing_item_to_segments(
    item,
) -> list[tuple[fitz.Point, fitz.Point]]:
    if not item:
        return []

    item_type = item[0]

    if item_type == "l" and len(item) >= 3:
        return [
            (
                fitz.Point(item[1]),
                fitz.Point(item[2]),
            )
        ]

    if item_type == "re" and len(item) >= 2:
        rectangle = fitz.Rect(item[1])

        top_left = fitz.Point(
            rectangle.x0,
            rectangle.y0,
        )

        top_right = fitz.Point(
            rectangle.x1,
            rectangle.y0,
        )

        bottom_right = fitz.Point(
            rectangle.x1,
            rectangle.y1,
        )

        bottom_left = fitz.Point(
            rectangle.x0,
            rectangle.y1,
        )

        return [
            (top_left, top_right),
            (top_right, bottom_right),
            (bottom_right, bottom_left),
            (bottom_left, top_left),
        ]

    if item_type == "qu" and len(item) >= 2:
        quad = item[1]

        return [
            (quad.ul, quad.ur),
            (quad.ur, quad.lr),
            (quad.lr, quad.ll),
            (quad.ll, quad.ul),
        ]

    if item_type == "c" and len(item) >= 5:
        return [
            (
                fitz.Point(item[1]),
                fitz.Point(item[4]),
            )
        ]

    return []


def is_green_colour(
    colour,
) -> bool:
    hsv = rgb_to_hsv(colour)

    if hsv is None:
        return False

    hue, saturation, value = hsv

    # Green range. Cyan normally starts around 170-180 degrees
    # and is intentionally excluded.
    return (
        65 <= hue <= 165
        and saturation >= 0.25
        and value >= 0.20
    )


def get_green_vector_segments(
    page,
) -> list[dict]:
    segments = []

    for drawing in page.get_drawings():
        stroke_colour = drawing.get("color")
        fill_colour = drawing.get("fill")

        if not (
            is_green_colour(stroke_colour)
            or is_green_colour(fill_colour)
        ):
            continue

        for item in drawing.get("items", []):
            for start, end in drawing_item_to_segments(
                item
            ):
                length = point_distance(start, end)

                if length < 1.5:
                    continue

                segments.append(
                    {
                        "start": start,
                        "end": end,
                    }
                )

    return segments


def terminal_pair_is_green(
    terminal_pair: dict,
    green_segments: list[dict],
    page,
) -> bool:
    if not green_segments:
        return False

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_distance = max(
        18.0,
        min(page_scale * 0.04, 70.0),
    )

    for terminal in terminal_pair["terminals"]:
        for segment in green_segments:
            distance = point_to_segment_distance(
                terminal["point"],
                segment["start"],
                segment["end"],
            )

            if distance <= maximum_distance:
                return True

    return False


# ---------------------------------------------------------------------
# Fuse extraction
# ---------------------------------------------------------------------

def normalise_fuse_reference(
    board: str,
    fuse: str,
) -> str:
    return (
        f"{board.upper()}."
        f"{fuse.lower()}"
    )


def extract_fuse_references(
    page,
) -> list[dict]:
    page_text = page.get_text(
        "text",
        sort=True,
    )

    fuse_references = []
    seen = set()

    for match in FUSE_PATTERN.finditer(page_text):
        fuse_reference = normalise_fuse_reference(
            match.group("board"),
            match.group("fuse"),
        )

        rectangles = page.search_for(
            match.group(0),
            quads=False,
        )

        if not rectangles:
            key = (
                fuse_reference,
                None,
                None,
            )

            if key not in seen:
                seen.add(key)

                fuse_references.append(
                    {
                        "value": fuse_reference,
                        "point": None,
                    }
                )

            continue

        for rectangle in rectangles:
            key = (
                fuse_reference,
                round(rectangle.x0, 1),
                round(rectangle.y0, 1),
            )

            if key in seen:
                continue

            seen.add(key)

            fuse_references.append(
                {
                    "value": fuse_reference,
                    "point": fitz.Point(
                        (
                            rectangle.x0
                            + rectangle.x1
                        ) / 2,
                        (
                            rectangle.y0
                            + rectangle.y1
                        ) / 2,
                    ),
                }
            )

    return fuse_references


def get_equipment_names(
    page,
) -> tuple[str, str]:
    """
    Extract equipment names from the current PDF page.

    No default equipment name is invented. If a label is missing,
    REVIEW REQUIRED is returned so the engineer can confirm it.
    """
    page_text = page.get_text(
        "text",
        sort=True,
    )

    normalised_page_text = re.sub(
        r"\s+",
        " ",
        page_text.upper(),
    )

    ns1_match = NS1_RACK_PATTERN.search(
        normalised_page_text
    )

    ixl_match = IXL_CUBICLE_PATTERN.search(
        normalised_page_text
    )

    ns1_name = (
        ns1_match.group(0).upper()
        if ns1_match
        else "REVIEW REQUIRED - RACK NOT FOUND"
    )

    ixl_name = (
        ixl_match.group(0).upper()
        if ixl_match
        else "REVIEW REQUIRED - CUBICLE NOT FOUND"
    )

    if (
        not ixl_name.startswith("REVIEW REQUIRED")
        and not ixl_name.startswith("IXL/")
    ):
        ixl_name = f"IXL/{ixl_name}"

    return ns1_name, ixl_name


def select_fuses_for_item(
    item_label: dict,
    fuse_references: list[dict],
) -> list[str]:
    """
    Select up to two fuse references nearest to the Item occurrence.

    In Return Channel drawings there are normally two related fuse
    references:
      - NS1 Rack;
      - IXL/SACEM CoV Cubicle.

    If only one fuse reference is extractable from PDF text, it is
    used for both locations.
    """
    if not fuse_references:
        return []

    references_with_distance = []

    for fuse_reference in fuse_references:
        point = fuse_reference["point"]

        if point is None:
            distance = 999999.0
        else:
            distance = point_distance(
                item_label["point"],
                point,
            )

        references_with_distance.append(
            (
                distance,
                fuse_reference["value"],
            )
        )

    references_with_distance.sort()

    selected = []
    seen = set()

    for _, value in references_with_distance:
        if value in seen:
            continue

        seen.add(value)
        selected.append(value)

        if len(selected) == 2:
            break

    return selected


def build_fuse_checklist_text(
    ns1_rack_name: str,
    ixl_cubicle_name: str,
    fuse_values: list[str],
) -> str:
    if not fuse_values:
        return "REVIEW REQUIRED - FUSE VALUE NOT FOUND"

    if len(fuse_values) == 1:
        ns1_fuse = fuse_values[0]
        ixl_fuse = fuse_values[0]
    else:
        ns1_fuse = fuse_values[0]
        ixl_fuse = fuse_values[1]

    return format_lines(
        [
            f"{ns1_rack_name}: {ns1_fuse}",
            f"{ixl_cubicle_name}: {ixl_fuse}",
        ]
    )


# ---------------------------------------------------------------------
# Return Channel row creation
# ---------------------------------------------------------------------

def find_nearest_terminal_pair(
    item_label: dict,
    terminal_pairs: list[dict],
) -> dict | None:
    if not terminal_pairs:
        return None

    scored_pairs = []

    for terminal_pair in terminal_pairs:
        horizontal_distance = abs(
            terminal_pair["x_center"]
            - item_label["x_center"]
        )

        vertical_distance = abs(
            terminal_pair["y_center"]
            - item_label["y_center"]
        )

        score = (
            vertical_distance * 4
            + horizontal_distance
        )

        scored_pairs.append(
            (
                score,
                terminal_pair,
            )
        )

    scored_pairs.sort(
        key=lambda value: (
            value[0],
            value[1]["pair_distance"],
        )
    )

    return scored_pairs[0][1]


def group_item_labels_by_name(
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    grouped = defaultdict(list)

    for item_label in item_labels:
        grouped[item_label["item"]].append(
            item_label
        )

    return dict(grouped)


def create_return_channel_rows(
    page,
    item_labels: list[dict],
    terminal_pairs: list[dict],
    fuse_references: list[dict],
    green_segments: list[dict],
    log_callback=None,
) -> tuple[list[dict], list[dict]]:
    rows = []
    debug_terminals = []

    ns1_rack_name, ixl_cubicle_name = (
        get_equipment_names(page)
    )

    grouped_items = group_item_labels_by_name(
        item_labels
    )

    for item_name, item_occurrences in (
        grouped_items.items()
    ):
        # Use upper-most occurrence for visual ordering and
        # association where an Item label is repeated.
        item_occurrences = sorted(
            item_occurrences,
            key=lambda item: (
                item["y_center"],
                item["x_center"],
            ),
        )

        item_label = item_occurrences[0]

        terminal_pair = find_nearest_terminal_pair(
            item_label,
            terminal_pairs,
        )

        if terminal_pair is None:
            write_log(
                log_callback,
                f"Return Channel Item={item_name}: "
                "skipped; terminal pair not found.",
                "WARNING",
            )
            continue

        fuse_values = select_fuses_for_item(
            item_label,
            fuse_references,
        )

        fuse_text = build_fuse_checklist_text(
            ns1_rack_name,
            ixl_cubicle_name,
            fuse_values,
        )

        is_green_terminal = terminal_pair_is_green(
            terminal_pair,
            green_segments,
            page,
        )

        cable_rack_text = ""

        if is_green_terminal:
            cable_rack_text = format_lines(
                [terminal_pair["text"]]
            )

        debug_terminals.extend(
            terminal_pair["terminals"]
        )

        rows.append(
            {
                "Item": item_name,
                "Item Sort Y": item_label["y_center"],

                "H3 - Fuses opened": fuse_text,

                "H3 - Cable rack terminal blocks": (
                    cable_rack_text
                ),

                "H3 - Fuses closed": fuse_text,

                "Green Terminal Detected": (
                    is_green_terminal
                ),

                "Review Status": (
                    "Draft"
                    if fuse_values
                    else "REVIEW REQUIRED"
                ),

                "Engineer Remarks": (
                    "Candidate generated from Return Channel "
                    "title, fuse-reference text, terminal "
                    "geometry and green-circuit detection. "
                    "Verify controlled drawing."
                ),
            }
        )

        write_log(
            log_callback,
            f"Return Channel Item={item_name}; "
            f"fuses={fuse_values or 'NOT FOUND'}; "
            f"terminal={terminal_pair['text']}; "
            f"green_terminal={is_green_terminal}.",
            "DEBUG",
        )

    return rows, debug_terminals


# ---------------------------------------------------------------------
# Page relevance
# ---------------------------------------------------------------------

def page_is_return_channel_relevant(
    page_text: str,
    log_callback=None,
) -> bool:
    terminal_matches = TERMINAL_PATTERN.findall(
        page_text
    )

    item_matches = ITEM_PATTERN.findall(
        page_text
    )

    fuse_matches = FUSE_PATTERN.findall(
        page_text
    )

    write_log(
        log_callback,
        "Return Channel relevance: "
        f"terminals={len(terminal_matches)}; "
        f"items={len(item_matches)}; "
        f"fuses={len(fuse_matches)}.",
        "DEBUG",
    )

    return bool(terminal_matches) and bool(
        item_matches
    )


# ---------------------------------------------------------------------
# Debug image
# ---------------------------------------------------------------------

def create_debug_image(
    page,
    selected_terminals: list[dict],
) -> bytes:
    padding = 2.5
    shape = page.new_shape()

    for terminal in selected_terminals:
        rectangle = fitz.Rect(
            max(0, terminal["x0"] - padding),
            max(0, terminal["y0"] - padding),
            min(
                page.rect.width,
                terminal["x1"] + padding,
            ),
            min(
                page.rect.height,
                terminal["y1"] + padding,
            ),
        )

        shape.draw_rect(rectangle)

    if selected_terminals:
        shape.finish(
            color=(1.0, 0.0, 0.0),
            width=1.25,
            stroke_opacity=1.0,
        )

        shape.commit()

    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(1.5, 1.5),
        alpha=False,
    )

    return pixmap.tobytes("png")


# ---------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------

def extract_pdf(
    pdf_path: str | Path,
    log_callback=None,
    progress_callback=None,
) -> tuple[pd.DataFrame, dict[int, bytes]]:
    start_time = time.perf_counter()

    records = []
    debug_images = {}

    write_log(
        log_callback,
        f"Opening Return Channel PDF: {pdf_path}",
    )

    with fitz.open(pdf_path) as document:
        total_pages = len(document)

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_start_time = time.perf_counter()

            if progress_callback is not None:
                progress_callback(
                    (
                        page_index
                        / max(total_pages, 1)
                    ) * 100
                )

            try:
                page_text = page.get_text(
                    "text",
                    sort=True,
                )

                (
                    has_return_channel,
                    title_source,
                ) = page_has_return_channel(
                    page,
                    page_text,
                )

                if not has_return_channel:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "Return Channel title not found.",
                        "DEBUG",
                    )
                    continue

                write_log(
                    log_callback,
                    f"Page {page_number}: Return Channel "
                    f"drawing found; "
                    f"title_source={title_source}.",
                    "INFO",
                )

                if not page_is_return_channel_relevant(
                    page_text,
                    log_callback=log_callback,
                ):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "terminal or Item pattern missing.",
                        "DEBUG",
                    )
                    continue

                drawing_number = get_drawing_number(page)
                drawing_revision = get_revision(page)

                if not drawing_number:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "drawing number missing.",
                        "WARNING",
                    )
                    continue

                location = get_location_from_drawing_number(
                    drawing_number
                )

                item_labels = extract_item_labels(page)

                terminals = extract_terminal_references(
                    page
                )

                terminal_pairs = build_terminal_pairs(
                    page,
                    terminals,
                )

                fuse_references = extract_fuse_references(
                    page
                )

                green_segments = get_green_vector_segments(
                    page
                )

                write_log(
                    log_callback,
                    f"Page {page_number}: "
                    f"drawing={drawing_number}; "
                    f"revision={drawing_revision}; "
                    f"location={location or 'NOT FOUND'}; "
                    f"items={len(item_labels)}; "
                    f"terminals={len(terminals)}; "
                    f"terminal_pairs={len(terminal_pairs)}; "
                    f"fuses={len(fuse_references)}; "
                    f"green_segments={len(green_segments)}.",
                    "DEBUG",
                )

                if not item_labels or not terminal_pairs:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "Item labels or terminal pairs missing.",
                        "WARNING",
                    )
                    continue

                rows, debug_terminals = (
                    create_return_channel_rows(
                        page,
                        item_labels,
                        terminal_pairs,
                        fuse_references,
                        green_segments,
                        log_callback=log_callback,
                    )
                )

                if rows:
                    debug_images[page_number] = (
                        create_debug_image(
                            page,
                            debug_terminals,
                        )
                    )

                for row in rows:
                    records.append(
                        {
                            "Include": True,
                            "No.": 0,
                            "Location": location,
                            "Item": row["Item"],
                            "Drawing No. Check list": (
                                drawing_number
                            ),
                            "Drawing Rev.": drawing_revision,
                            "A11 CoV Work Procedure Appendix": (
                                "H3"
                            ),

                            "H3 - Check list Before Cut-in - "
                            "Fuses are opened": (
                                row["H3 - Fuses opened"]
                            ),

                            "H3 - Check list Before Cut-in - "
                            "CABLE RACK terminal blocks are "
                            "opened and secure with cable tie": (
                                row[
                                    "H3 - Cable rack terminal blocks"
                                ]
                            ),

                            "H3 - Check list After Cut-in - "
                            "Fuses are closed": (
                                row["H3 - Fuses closed"]
                            ),

                            "_item_y_sort": (
                                row["Item Sort Y"]
                            ),

                            "PDF Page": page_number,
                            "Review Status": (
                                row["Review Status"]
                            ),
                            "Engineer Remarks": (
                                row["Engineer Remarks"]
                            ),
                        }
                    )

                page_elapsed = (
                    time.perf_counter()
                    - page_start_time
                )

                write_log(
                    log_callback,
                    f"Page {page_number}: created "
                    f"{len(rows)} Return Channel row(s) "
                    f"in {page_elapsed:.2f}s.",
                    "INFO",
                )

            except Exception as error:
                write_log(
                    log_callback,
                    f"Page {page_number}: ERROR: {error}",
                    "ERROR",
                )

    dataframe = pd.DataFrame(records)

    if not dataframe.empty:
        dataframe = dataframe.drop_duplicates(
            subset=[
                "Location",
                "Item",
                "Drawing No. Check list",
                "Drawing Rev.",
                "A11 CoV Work Procedure Appendix",
                "PDF Page",
            ],
            keep="first",
        ).reset_index(drop=True)

        dataframe["_drawing_sort"] = (
            dataframe["Drawing No. Check list"]
            .astype(str)
            .str.extract(r"/R\d+/(\d+)")[0]
        )

        dataframe["_drawing_sort"] = pd.to_numeric(
            dataframe["_drawing_sort"],
            errors="coerce",
        ).fillna(999999999)

        # Drawing order first.
        # Within the same page, Item rows are ordered top-to-bottom
        # using the actual Item Y coordinate on the PDF.
        dataframe = dataframe.sort_values(
            by=[
                "_drawing_sort",
                "PDF Page",
                "_item_y_sort",
                "Item",
            ],
            ascending=True,
            kind="stable",
        ).reset_index(drop=True)

        dataframe = dataframe.drop(
            columns=[
                "_drawing_sort",
                "_item_y_sort",
            ],
            errors="ignore",
        )

        dataframe["No."] = range(
            1,
            len(dataframe) + 1,
        )

    elapsed = time.perf_counter() - start_time

    write_log(
        log_callback,
        "Return Channel extraction complete. "
        f"Rows created: {len(dataframe)}. "
        f"Total time: {elapsed:.2f}s.",
        "INFO",
    )

    if progress_callback is not None:
        progress_callback(100)

    return dataframe, debug_images
