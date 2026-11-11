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

ITEM_IDENTIFIER_PATTERN = (
    r"(?=[A-Z0-9-]*\d)"
    r"[A-Z][A-Z0-9-]*"
)

ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    rf"(?P<item>{ITEM_IDENTIFIER_PATTERN})"
    r"(?:\s*\(\s*"
    r"(?P<state>[A-Z0-9-]+)"
    r"\s*\))?",
    re.IGNORECASE,
)

FUSE_NUMBER_PATTERN = re.compile(
    r"^\s*(?:#|F)\s*(?P<number>\d+)\s*$",
    re.IGNORECASE,
)

X_TERMINAL_PATTERN = re.compile(
    r"^\s*X\s*[-.:]?\s*(?P<number>\d+)\s*$",
    re.IGNORECASE,
)

CURRENT_RATING_PATTERN = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:MA|A)\s*$",
    re.IGNORECASE,
)

NS_RACK_PATTERN = re.compile(
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
# Generic geometry helpers
# ---------------------------------------------------------------------

def normalise_text(
    value: str,
) -> str:
    return re.sub(
        r"[^A-Z0-9]+",
        " ",
        str(value).upper(),
    ).strip()


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

    closest = fitz.Point(
        start.x + t * dx,
        start.y + t * dy,
    )

    return point_distance(
        point,
        closest,
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
        if str(value).strip()
    )


def normalise_drawing_number(
    drawing_number: str,
) -> str:
    return str(drawing_number).upper().strip()


# ---------------------------------------------------------------------
# Title block / metadata
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

        y_center = (y0 + y1) / 2

        if y_center < title_top:
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
                "y_center": y_center,
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
    title_text = get_title_block_text(page)

    matches = list(
        DRAWING_PATTERN.finditer(title_text)
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

    labels = [
        word
        for word in words
        if word["value"].upper()
        in {
            "REV",
            "REV.",
            "REVISION",
        }
    ]

    if not labels:
        return "REVIEW"

    candidates = []

    for label in labels:
        for word in words:
            value = word["value"].upper()

            if not re.fullmatch(
                r"[A-Z]{1,3}\d{1,3}",
                value,
            ):
                continue

            if word["x_center"] <= label["x1"]:
                continue

            vertical_distance = abs(
                word["y_center"]
                - label["y_center"]
            )

            if vertical_distance > 20:
                continue

            horizontal_distance = (
                word["x0"]
                - label["x1"]
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
# Page word extraction
# ---------------------------------------------------------------------

def get_page_words(
    page,
) -> list[dict]:
    words = []

    for word in page.get_text(
        "words",
        sort=True,
    ):
        x0, y0, x1, y1, value = word[:5]

        words.append(
            {
                "value": str(value).strip(),
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
                "point": fitz.Point(
                    (
                        x0 + x1
                    ) / 2,
                    (
                        y0 + y1
                    ) / 2,
                ),
            }
        )

    return words


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
        state = (
            match.group("state") or ""
        ).upper().strip()

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

    for word in get_page_words(page):
        match = TERMINAL_PATTERN.fullmatch(
            word["value"].strip(
                " ,;:()[]{}"
            )
        )

        if not match:
            continue

        rack = match.group("rack").upper()
        column = match.group("column").upper()
        number = match.group("number")

        terminal_name = (
            f"{rack}.{column}{number}"
        )

        key = (
            terminal_name,
            round(word["x_center"], 1),
            round(word["y_center"], 1),
        )

        if key in seen:
            continue

        seen.add(key)

        terminals.append(
            {
                "terminal": terminal_name,
                "rack": rack,
                "column": column,
                "number": number,
                "number_parts": (
                    parse_terminal_number(number)
                ),
                "x0": word["x0"],
                "y0": word["y0"],
                "x1": word["x1"],
                "y1": word["y1"],
                "x_center": word["x_center"],
                "y_center": word["y_center"],
                "point": word["point"],
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
        28.0,
        min(page_scale * 0.15, 180.0),
    )

    candidates = []

    for first_index, first in enumerate(terminals):
        for second in terminals[first_index + 1:]:
            if first["rack"] != second["rack"]:
                continue

            if first["column"] != second["column"]:
                continue

            if not terminal_numbers_are_consecutive(
                first,
                second,
            ):
                continue

            pair_distance = point_distance(
                first["point"],
                second["point"],
            )

            if pair_distance > maximum_pair_distance:
                continue

            x_center = (
                first["x_center"]
                + second["x_center"]
            ) / 2

            y_center = (
                first["y_center"]
                + second["y_center"]
            ) / 2

            candidates.append(
                {
                    "text": (
                        f"{first['terminal']} & "
                        f"{second['terminal']}"
                    ),
                    "x_center": x_center,
                    "y_center": y_center,
                    "point": fitz.Point(
                        x_center,
                        y_center,
                    ),
                    "pair_distance": pair_distance,
                    "terminals": [first, second],
                }
            )

    return candidates


# ---------------------------------------------------------------------
# PDF drawing/vector helpers
# ---------------------------------------------------------------------

def drawing_item_to_segments(
    item,
) -> list[tuple[fitz.Point, fitz.Point, str]]:
    if not item:
        return []

    item_type = item[0]

    if item_type == "l" and len(item) >= 3:
        return [
            (
                fitz.Point(item[1]),
                fitz.Point(item[2]),
                "line",
            )
        ]

    if item_type == "c" and len(item) >= 5:
        return [
            (
                fitz.Point(item[1]),
                fitz.Point(item[4]),
                "curve",
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
            (top_left, top_right, "rectangle"),
            (top_right, bottom_right, "rectangle"),
            (bottom_right, bottom_left, "rectangle"),
            (bottom_left, top_left, "rectangle"),
        ]

    return []


def is_green_colour(
    colour,
) -> bool:
    hsv = rgb_to_hsv(colour)

    if hsv is None:
        return False

    hue, saturation, value = hsv

    # Generic green region in HSV.
    # Cyan is normally above this range.
    return (
        65 <= hue <= 165
        and saturation >= 0.30
        and value >= 0.20
    )


def get_green_segments(
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
            for start, end, segment_type in (
                drawing_item_to_segments(item)
            ):
                length = point_distance(start, end)

                if length < 1.0:
                    continue

                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "type": segment_type,
                    }
                )

    return segments

# ---------------------------------------------------------------------
# Fuse extraction
# ---------------------------------------------------------------------
# ---------------------------------------------------------------------
# Fuse symbol detection: shape first, text second
# ---------------------------------------------------------------------

def get_drawing_curve_bounds(
    drawing: dict,
) -> list[fitz.Rect]:
    """
    Return bounding boxes for curved PDF vector primitives.

    The fuse shown in the drawings is a small wave / sine-like shape.
    In native PDFs this is commonly represented by one or more Bezier
    curve primitives, item type "c".

    This function does not use fuse text, Item names, drawing numbers,
    locations, X board values, or fuse values.
    """
    curve_bounds = []

    for item in drawing.get("items", []):
        if not item:
            continue

        item_type = item[0]

        # PyMuPDF Bezier curve primitive.
        if item_type != "c":
            continue

        if len(item) < 5:
            continue

        points = [
            fitz.Point(item[1]),
            fitz.Point(item[2]),
            fitz.Point(item[3]),
            fitz.Point(item[4]),
        ]

        x_values = [
            point.x
            for point in points
        ]

        y_values = [
            point.y
            for point in points
        ]

        rectangle = fitz.Rect(
            min(x_values),
            min(y_values),
            max(x_values),
            max(y_values),
        )

        if rectangle.width <= 0 or rectangle.height <= 0:
            continue

        curve_bounds.append(rectangle)

    return curve_bounds


def rect_distance(
    first: fitz.Rect,
    second: fitz.Rect,
) -> float:
    """
    Minimum distance between two rectangles.

    Returns zero where the rectangles overlap or touch.
    """
    horizontal_distance = max(
        first.x0 - second.x1,
        second.x0 - first.x1,
        0,
    )

    vertical_distance = max(
        first.y0 - second.y1,
        second.y0 - first.y1,
        0,
    )

    return math.hypot(
        horizontal_distance,
        vertical_distance,
    )


def expand_rect(
    rectangle: fitz.Rect,
    padding: float,
) -> fitz.Rect:
    return fitz.Rect(
        rectangle.x0 - padding,
        rectangle.y0 - padding,
        rectangle.x1 + padding,
        rectangle.y1 + padding,
    )


def rect_center(
    rectangle: fitz.Rect,
) -> fitz.Point:
    return fitz.Point(
        (
            rectangle.x0
            + rectangle.x1
        ) / 2,
        (
            rectangle.y0
            + rectangle.y1
        ) / 2,
    )


def is_possible_fuse_wave(
    rectangle: fitz.Rect,
    page,
) -> bool:
    """
    Identify a small local curved wave shape.

    This is shape-only detection. It does not inspect nearby text.

    A fuse wave is normally:
      - a small curved vector;
      - wider than it is tall;
      - not a large curved drawing boundary;
      - not a full-page revision cloud.

    Values scale from PDF page dimensions, rather than using any
    drawing number, Item name, rack ID, location, X board, or fuse
    number.
    """
    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    minimum_width = max(
        3.0,
        page_scale * 0.002,
    )

    maximum_width = max(
        90.0,
        page_scale * 0.12,
    )

    minimum_height = max(
        1.0,
        page_scale * 0.0008,
    )

    maximum_height = max(
        30.0,
        page_scale * 0.04,
    )

    if rectangle.width < minimum_width:
        return False

    if rectangle.width > maximum_width:
        return False

    if rectangle.height < minimum_height:
        return False

    if rectangle.height > maximum_height:
        return False

    aspect_ratio = (
        rectangle.width
        / max(rectangle.height, 0.1)
    )

    # The common fuse wave is wide and shallow.
    if aspect_ratio < 1.2:
        return False

    return True


def cluster_fuse_wave_rectangles(
    wave_rectangles: list[fitz.Rect],
    page,
) -> list[dict]:
    """
    Group adjacent curved vector elements into individual fuse-wave
    symbol regions.

    A single wave symbol can be stored as several small Bezier curves.
    This joins close curves into one physical candidate.

    No text is used here.
    """
    if not wave_rectangles:
        return []

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    join_distance = max(
        8.0,
        min(page_scale * 0.02, 35.0),
    )

    remaining = list(wave_rectangles)
    clusters = []

    while remaining:
        first = remaining.pop(0)

        cluster = [first]
        changed = True

        while changed:
            changed = False

            combined = fitz.Rect(cluster[0])

            for rectangle in cluster[1:]:
                combined |= rectangle

            next_remaining = []

            for candidate in remaining:
                if (
                    rect_distance(
                        combined,
                        candidate,
                    )
                    <= join_distance
                ):
                    cluster.append(candidate)
                    changed = True
                else:
                    next_remaining.append(candidate)

            remaining = next_remaining

        cluster_rect = fitz.Rect(cluster[0])

        for rectangle in cluster[1:]:
            cluster_rect |= rectangle

        if not is_possible_fuse_wave(
            cluster_rect,
            page,
        ):
            continue

        clusters.append(
            {
                "rect": cluster_rect,
                "point": rect_center(cluster_rect),
                "curve_count": len(cluster),
            }
        )

    return clusters


def detect_fuse_wave_symbols(
    page,
) -> list[dict]:
    """
    Detect physical fuse-wave symbols from PDF vector geometry first.

    The result contains only symbol locations. No # number, X board,
    Item, drawing number or location is used in this stage.
    """
    curve_rectangles = []

    for drawing in page.get_drawings():
        drawing_rect = drawing.get("rect")

        if drawing_rect is None:
            continue

        # Ignore very large vector objects early.
        if (
            drawing_rect.width
            > page.rect.width * 0.70
            and drawing_rect.height
            > page.rect.height * 0.20
        ):
            continue

        curve_rectangles.extend(
            get_drawing_curve_bounds(drawing)
        )

    return cluster_fuse_wave_rectangles(
        curve_rectangles,
        page,
    )


def words_near_fuse_symbol(
    words: list[dict],
    fuse_symbol: dict,
    page,
) -> list[dict]:
    """
    Return text physically near one fuse-wave symbol.

    This is called only AFTER fuse shape detection.

    The local search region is scaled by page size. It includes the
    number label, X board identifier and rating around the fuse symbol.
    """
    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    horizontal_padding = max(
        55.0,
        min(page_scale * 0.10, 180.0),
    )

    vertical_padding = max(
        45.0,
        min(page_scale * 0.08, 140.0),
    )

    search_rect = fitz.Rect(
        fuse_symbol["rect"].x0 - horizontal_padding,
        fuse_symbol["rect"].y0 - vertical_padding,
        fuse_symbol["rect"].x1 + horizontal_padding,
        fuse_symbol["rect"].y1 + vertical_padding,
    )

    nearby_words = []

    for word in words:
        point = word["point"]

        if search_rect.contains(point):
            nearby_words.append(word)

    return nearby_words


def extract_local_fuse_number(
    nearby_words: list[dict],
    fuse_point: fitz.Point,
) -> dict | None:
    """
    Extract the nearest #number / Fnumber near a detected fuse symbol.
    """
    candidates = []

    for index, word in enumerate(nearby_words):
        value = word["value"].upper().strip()

        match = FUSE_NUMBER_PATTERN.fullmatch(value)

        if match:
            candidates.append(
                {
                    "number": int(
                        match.group("number")
                    ),
                    "point": word["point"],
                    "source_text": word["value"],
                }
            )
            continue

        # Supports separate words:
        # "#" + "7"
        # "F" + "7"
        if value not in {"#", "F"}:
            continue

        for next_index in range(
            index + 1,
            min(index + 4, len(nearby_words)),
        ):
            next_word = nearby_words[next_index]

            if abs(
                word["y_center"]
                - next_word["y_center"]
            ) > 18:
                continue

            if (
                next_word["x0"]
                - word["x1"]
            ) > 40:
                break

            next_value = next_word["value"].strip()

            if not next_value.isdigit():
                continue

            point = fitz.Point(
                (
                    word["x0"]
                    + next_word["x1"]
                ) / 2,
                (
                    word["y_center"]
                    + next_word["y_center"]
                ) / 2,
            )

            candidates.append(
                {
                    "number": int(next_value),
                    "point": point,
                    "source_text": (
                        f"{word['value']}"
                        f"{next_word['value']}"
                    ),
                }
            )

            break

    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: point_distance(
            candidate["point"],
            fuse_point,
        )
    )

    return candidates[0]


def extract_local_x_terminal(
    nearby_words: list[dict],
    fuse_point: fitz.Point,
) -> dict | None:
    """
    Extract nearest X<number> terminal-board label near the fuse wave.

    Supports:
      X1
      X 1
      X-1
      X.1
    """
    candidates = []

    for index, word in enumerate(nearby_words):
        value = word["value"].upper().strip()

        match = X_TERMINAL_PATTERN.fullmatch(value)

        if match:
            candidates.append(
                {
                    "number": int(
                        match.group("number")
                    ),
                    "point": word["point"],
                    "source_text": word["value"],
                }
            )
            continue

        if value != "X":
            continue

        for next_index in range(
            index + 1,
            min(index + 4, len(nearby_words)),
        ):
            next_word = nearby_words[next_index]

            if abs(
                word["y_center"]
                - next_word["y_center"]
            ) > 18:
                continue

            if (
                next_word["x0"]
                - word["x1"]
            ) > 40:
                break

            next_value = next_word["value"].strip()

            if not next_value.isdigit():
                continue

            point = fitz.Point(
                (
                    word["x0"]
                    + next_word["x1"]
                ) / 2,
                (
                    word["y_center"]
                    + next_word["y_center"]
                ) / 2,
            )

            candidates.append(
                {
                    "number": int(next_value),
                    "point": point,
                    "source_text": (
                        f"{word['value']}"
                        f"{next_word['value']}"
                    ),
                }
            )

            break

    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: point_distance(
            candidate["point"],
            fuse_point,
        )
    )

    return candidates[0]


def local_fuse_has_current_rating(
    nearby_words: list[dict],
) -> bool:
    """
    Current rating such as 100mA is validation evidence for a fuse
    symbol. It is not needed to construct the fuse value.
    """
    for word in nearby_words:
        if CURRENT_RATING_PATTERN.fullmatch(
            word["value"].upper()
        ):
            return True

    return False


def extract_fuse_candidates(
    page,
) -> list[dict]:
    """
    Shape-first fuse detection.

    Workflow:
      1. detect small wave-shaped vector symbols;
      2. inspect only local text around every wave;
      3. locate nearest fuse number (#7 / F7);
      4. locate nearest X board (X3);
      5. generate X3.f7.

    A candidate must have:
      - detected fuse-wave shape;
      - local fuse number;
      - local X board number.

    No KOW, OLY, drawing number, Item name, fixed X board, or fixed
    fuse number is used.
    """
    words = get_page_words(page)

    fuse_symbols = detect_fuse_wave_symbols(page)

    candidates = []
    seen = set()

    for fuse_symbol in fuse_symbols:
        nearby_words = words_near_fuse_symbol(
            words,
            fuse_symbol,
            page,
        )

        local_fuse_number = extract_local_fuse_number(
            nearby_words,
            fuse_symbol["point"],
        )

        local_x_terminal = extract_local_x_terminal(
            nearby_words,
            fuse_symbol["point"],
        )

        if local_fuse_number is None:
            continue

        if local_x_terminal is None:
            continue

        fuse_value = (
            f"X{local_x_terminal['number']}."
            f"f{local_fuse_number['number']}"
        )

        key = (
            fuse_value,
            round(fuse_symbol["point"].x, 1),
            round(fuse_symbol["point"].y, 1),
        )

        if key in seen:
            continue

        seen.add(key)

        rating_found = local_fuse_has_current_rating(
            nearby_words
        )

        confidence = 1

        if fuse_symbol["curve_count"] >= 1:
            confidence += 2

        if rating_found:
            confidence += 1

        candidates.append(
            {
                "value": fuse_value,
                "point": fuse_symbol["point"],
                "rect": fuse_symbol["rect"],
                "curve_count": fuse_symbol[
                    "curve_count"
                ],
                "rating_found": rating_found,
                "confidence": confidence,
                "fuse_label": local_fuse_number[
                    "source_text"
                ],
                "x_label": local_x_terminal[
                    "source_text"
                ],
            }
        )

    candidates.sort(
        key=lambda candidate: (
            candidate["point"].y,
            candidate["point"].x,
        )
    )

    return candidates

def log_fuse_detection_diagnostics(
    page,
    fuse_candidates: list[dict],
    log_callback=None,
):
    """
    Log shape-first fuse detection results.
    """
    symbols = detect_fuse_wave_symbols(page)

    symbol_diagnostics = []

    for symbol in symbols:
        symbol_diagnostics.append(
            (
                round(symbol["point"].x, 1),
                round(symbol["point"].y, 1),
                symbol["curve_count"],
                (
                    round(symbol["rect"].width, 1),
                    round(symbol["rect"].height, 1),
                ),
            )
        )

    candidate_diagnostics = []

    for candidate in fuse_candidates:
        candidate_diagnostics.append(
            (
                candidate["value"],
                candidate["fuse_label"],
                candidate["x_label"],
                candidate["curve_count"],
                candidate["rating_found"],
            )
        )

    write_log(
        log_callback,
        "Fuse shape diagnostic: "
        f"wave_symbols={symbol_diagnostics}; "
        f"fuse_candidates={candidate_diagnostics}.",
        "DEBUG",
    )

# ---------------------------------------------------------------------
# Equipment labels and fuse assignment
# ---------------------------------------------------------------------

def find_equipment_label_points(
    page,
) -> list[dict]:
    page_text = page.get_text(
        "text",
        sort=True,
    )

    labels = []

    ns_matches = list(
        NS_RACK_PATTERN.finditer(page_text)
    )

    for match in ns_matches:
        source_text = match.group(0)

        for rectangle in page.search_for(source_text):
            labels.append(
                {
                    "type": "NS_RACK",
                    "name": source_text.upper(),
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

    ixl_matches = list(
        IXL_CUBICLE_PATTERN.finditer(page_text)
    )

    for match in ixl_matches:
        source_text = match.group(0)

        for rectangle in page.search_for(source_text):
            name = source_text.upper()

            if not name.startswith("IXL/"):
                name = f"IXL/{name}"

            labels.append(
                {
                    "type": "IXL_CUBICLE",
                    "name": name,
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


def get_best_equipment_label(
    equipment_labels: list[dict],
    equipment_type: str,
) -> dict | None:
    candidates = [
        label
        for label in equipment_labels
        if label["type"] == equipment_type
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda label: (
            label["point"].y,
            label["point"].x,
        )
    )

    return candidates[0]


def select_fuse_for_equipment(
    fuse_candidates: list[dict],
    equipment_label: dict | None,
    excluded_values: set[str],
) -> dict | None:
    """
    Select the fuse physically nearest the relevant equipment label.

    Fuse-wave shape and local text determine a candidate.
    Equipment geometry determines whether it belongs to the NS Rack
    or IXL/SACEM CoV Cubicle side.
    """
    available = [
        candidate
        for candidate in fuse_candidates
        if candidate["value"] not in excluded_values
    ]

    if not available:
        return None

    if equipment_label is None:
        available.sort(
            key=lambda candidate: (
                -candidate.get("confidence", 0),
                candidate["point"].y,
                candidate["point"].x,
            )
        )

        return available[0]

    available.sort(
        key=lambda candidate: (
            point_distance(
                candidate["point"],
                equipment_label["point"],
            ),
            -candidate.get("confidence", 0),
            candidate["point"].y,
            candidate["point"].x,
        )
    )

    return available[0]


def build_fuse_text(
    ns_rack_label: dict | None,
    ixl_label: dict | None,
    ns_rack_fuse: dict | None,
    ixl_fuse: dict | None,
) -> tuple[str, bool]:
    values = []
    review_required = False

    if ns_rack_label is None:
        values.append(
            "REVIEW REQUIRED - NS RACK NOT FOUND"
        )
        review_required = True

    elif ns_rack_fuse is None:
        values.append(
            f"REVIEW REQUIRED - "
            f"{ns_rack_label['name']} FUSE NOT FOUND"
        )
        review_required = True

    else:
        values.append(
            f"{ns_rack_label['name']}: "
            f"{ns_rack_fuse['value']}"
        )

    if ixl_label is None:
        values.append(
            "REVIEW REQUIRED - IXL/SACEM COV "
            "CUBICLE NOT FOUND"
        )
        review_required = True

    elif ixl_fuse is None:
        values.append(
            f"REVIEW REQUIRED - "
            f"{ixl_label['name']} FUSE NOT FOUND"
        )
        review_required = True

    else:
        values.append(
            f"{ixl_label['name']}: "
            f"{ixl_fuse['value']}"
        )

    return format_lines(values), review_required


# ---------------------------------------------------------------------
# Direct green-terminal detection
# ---------------------------------------------------------------------

def terminal_is_directly_connected_to_green(
    terminal: dict,
    green_segments: list[dict],
    page,
) -> bool:
    """
    True only where green circuitwork directly touches the terminal
    reference/terminal connection region.

    It does not accept a green path somewhere else on the page.
    """
    if not green_segments:
        return False

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_touch_distance = max(
        3.5,
        min(page_scale * 0.005, 8.0),
    )

    terminal_point = terminal["point"]

    for segment in green_segments:
        distance = point_to_segment_distance(
            terminal_point,
            segment["start"],
            segment["end"],
        )

        if distance <= maximum_touch_distance:
            return True

        if point_distance(
            terminal_point,
            segment["start"],
        ) <= maximum_touch_distance:
            return True

        if point_distance(
            terminal_point,
            segment["end"],
        ) <= maximum_touch_distance:
            return True

    return False


def terminal_pair_is_directly_green(
    terminal_pair: dict,
    green_segments: list[dict],
    page,
) -> bool:
    """
    Both terminal references must directly connect to green linework.

    This prevents unrelated green graphics elsewhere on the drawing
    from creating the additional Cable Rack checklist column.
    """
    terminal_results = [
        terminal_is_directly_connected_to_green(
            terminal,
            green_segments,
            page,
        )
        for terminal in terminal_pair["terminals"]
    ]

    return all(terminal_results)


# ---------------------------------------------------------------------
# Item / terminal association
# ---------------------------------------------------------------------

def group_item_labels_by_name(
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    grouped = defaultdict(list)

    for item_label in item_labels:
        grouped[item_label["item"]].append(
            item_label
        )

    return dict(grouped)


def find_nearest_terminal_pair(
    item_occurrences: list[dict],
    terminal_pairs: list[dict],
) -> dict | None:
    if not terminal_pairs:
        return None

    scored = []

    for terminal_pair in terminal_pairs:
        best_score = None

        for item in item_occurrences:
            horizontal_distance = abs(
                terminal_pair["x_center"]
                - item["x_center"]
            )

            vertical_distance = abs(
                terminal_pair["y_center"]
                - item["y_center"]
            )

            score = (
                vertical_distance * 4
                + horizontal_distance
            )

            if (
                best_score is None
                or score < best_score
            ):
                best_score = score

        scored.append(
            (
                best_score,
                terminal_pair,
            )
        )

    scored.sort(
        key=lambda value: (
            value[0],
            value[1]["pair_distance"],
        )
    )

    return scored[0][1]


# ---------------------------------------------------------------------
# Row creation
# ---------------------------------------------------------------------

def create_return_channel_rows(
    page,
    item_labels: list[dict],
    terminal_pairs: list[dict],
    fuse_candidates: list[dict],
    green_segments: list[dict],
    log_callback=None,
) -> tuple[list[dict], list[dict]]:
    rows = []
    debug_terminals = []

    equipment_labels = find_equipment_label_points(
        page
    )

    ns_rack_label = get_best_equipment_label(
        equipment_labels,
        "NS_RACK",
    )

    ixl_label = get_best_equipment_label(
        equipment_labels,
        "IXL_CUBICLE",
    )

    items_by_name = group_item_labels_by_name(
        item_labels
    )

    for item_name, item_occurrences in (
        items_by_name.items()
    ):
        item_occurrences = sorted(
            item_occurrences,
            key=lambda item: (
                item["y_center"],
                item["x_center"],
            ),
        )

        terminal_pair = find_nearest_terminal_pair(
            item_occurrences,
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

        used_fuse_values = set()

        ns_rack_fuse = select_fuse_for_equipment(
            fuse_candidates,
            ns_rack_label,
            used_fuse_values,
        )

        if ns_rack_fuse is not None:
            used_fuse_values.add(
                ns_rack_fuse["value"]
            )

        ixl_fuse = select_fuse_for_equipment(
            fuse_candidates,
            ixl_label,
            used_fuse_values,
        )

        fuse_text, fuse_review_required = (
            build_fuse_text(
                ns_rack_label,
                ixl_label,
                ns_rack_fuse,
                ixl_fuse,
            )
        )

        terminal_is_green = (
            terminal_pair_is_directly_green(
                terminal_pair,
                green_segments,
                page,
            )
        )

        terminal_block_text = ""

        if terminal_is_green:
            terminal_block_text = format_lines(
                [terminal_pair["text"]]
            )

        debug_terminals.extend(
            terminal_pair["terminals"]
        )

        review_status = "Draft"

        if fuse_review_required:
            review_status = "REVIEW REQUIRED"

        rows.append(
            {
                "Item": item_name,

                "Item Sort Y": min(
                    item["y_center"]
                    for item in item_occurrences
                ),

                "H3 - Fuses opened": fuse_text,

                "H3 - Cable rack terminal blocks": (
                    terminal_block_text
                ),

                "H3 - Fuses closed": fuse_text,

                "Review Status": review_status,

                "Engineer Remarks": (
                    "Candidate generated from Return Channel "
                    "title, local fuse symbol/text groups, "
                    "terminal geometry and direct green-line "
                    "terminal connectivity. Verify against "
                    "controlled drawing."
                ),
            }
        )

        write_log(
            log_callback,
            f"Return Channel Item={item_name}; "
            f"terminal={terminal_pair['text']}; "
            f"ns_fuse="
            f"{ns_rack_fuse['value'] if ns_rack_fuse else 'NOT FOUND'}; "
            f"ixl_fuse="
            f"{ixl_fuse['value'] if ixl_fuse else 'NOT FOUND'}; "
            f"green_terminal={terminal_is_green}.",
            "DEBUG",
        )

    return rows, debug_terminals


# ---------------------------------------------------------------------
# Relevance / debug image
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

    write_log(
        log_callback,
        "Return Channel relevance: "
        f"terminals={len(terminal_matches)}; "
        f"items={len(item_matches)}.",
        "DEBUG",
    )

    return bool(terminal_matches) and bool(
        item_matches
    )


def create_debug_image(
    page,
    selected_terminals: list[dict],
) -> bytes:
    shape = page.new_shape()
    padding = 2.5

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
        "INFO",
    )

    with fitz.open(pdf_path) as document:
        total_pages = len(document)

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_start = time.perf_counter()

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

                has_return_channel, title_source = (
                    page_has_return_channel(
                        page,
                        page_text,
                    )
                )

                if not has_return_channel:
                    continue

                write_log(
                    log_callback,
                    f"Page {page_number}: "
                    "Return Channel drawing found; "
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

                fuse_candidates = extract_fuse_candidates(
                    page
                )

                log_fuse_detection_diagnostics(
                    page,
                    fuse_candidates,
                    log_callback=log_callback,
                )

                green_segments = get_green_segments(
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
                    f"fuse_candidates={len(fuse_candidates)}; "
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
                        fuse_candidates,
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

                elapsed = (
                    time.perf_counter() - page_start
                )

                write_log(
                    log_callback,
                    f"Page {page_number}: created "
                    f"{len(rows)} Return Channel row(s) "
                    f"in {elapsed:.2f}s.",
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

    total_elapsed = (
        time.perf_counter() - start_time
    )

    write_log(
        log_callback,
        "Return Channel extraction complete. "
        f"Rows created: {len(dataframe)}. "
        f"Total time: {total_elapsed:.2f}s.",
        "INFO",
    )

    if progress_callback is not None:
        progress_callback(100)

    return dataframe, debug_images
