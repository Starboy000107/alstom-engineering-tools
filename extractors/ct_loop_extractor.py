from __future__ import annotations

import colorsys
import math
import re
import time
from collections import Counter, defaultdict
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

TERMINAL_PATTERN = re.compile(
    r"\b"
    r"(?P<rack>V\d+)"
    r"\s*[:.]\s*"
    r"(?P<column>[A-Z]+)"
    r"(?P<number>\d+(?:[.-]\d+)*)"
    r"\b",
    re.IGNORECASE,
)

ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"(?P<item>[A-Z]+\d+)"
    r"\s*\(\s*"
    r"(?P<state>[A-Z0-9]+)"
    r"\s*\)",
    re.IGNORECASE,
)

FALLBACK_ITEM_PATTERN = re.compile(
    r"\b"
    r"(?P<item>[A-Z]{2,}\d+)"
    r"\s*\(\s*"
    r"(?P<state>[A-Z0-9]+)"
    r"\s*\)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------
# Continuous Transmission page detection
# ---------------------------------------------------------------------

# Drawing types which may contain coloured CT-loop / transmission
# circuitwork requiring extraction.
CIRCUIT_DRAWING_TITLE_PATTERN = re.compile(
    r"\b(?:"
    r"CONTINUOUS\s+TRANSMISSION"
    r"|DIFFERENTIAL\s+REPETITION"
    r")\b",
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
# Generic helpers
# ---------------------------------------------------------------------

def normalise_text(
    text: str,
) -> str:
    return re.sub(
        r"[^A-Z0-9]+",
        " ",
        str(text).upper(),
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

    closest_point = fitz.Point(
        start.x + t * dx,
        start.y + t * dy,
    )

    return point_distance(
        point,
        closest_point,
    )


def circular_hue_distance(
    first_hue: float,
    second_hue: float,
) -> float:
    distance = abs(first_hue - second_hue)

    return min(
        distance,
        360 - distance,
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
        for part in re.split(r"[.-]", value)
        if part.isdigit()
    )


def terminal_numbers_are_consecutive(
    first_terminal: dict,
    second_terminal: dict,
) -> bool:
    first_parts = first_terminal["number_parts"]
    second_parts = second_terminal["number_parts"]

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


# ---------------------------------------------------------------------
# Title block extraction
# ---------------------------------------------------------------------

def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.35,
) -> list[dict]:
    """
    Continuous Transmission is usually stated in the drawing title
    block. The title block may occupy more than the lower 18%, so this
    function checks the lower 35% of the sheet.
    """
    page_height = float(page.rect.height)
    title_top = page_height * (1 - bottom_ratio)

    words = []

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, value = word[:5]

        if (y0 + y1) / 2 < title_top:
            continue

        words.append(
            {
                "value": value.strip(" ,;:()[]{}"),
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "x_center": (x0 + x1) / 2,
                "y_center": (y0 + y1) / 2,
            }
        )

    return words


def get_title_block_text(
    page,
) -> str:
    return " ".join(
        word["value"]
        for word in get_bottom_title_block_words(page)
    )


def page_has_target_circuit_title(
    page,
    page_text: str,
) -> tuple[bool, str]:
    """
    Check whether the page/title block contains an accepted circuit
    drawing title.
    """
    if CIRCUIT_DRAWING_TITLE_PATTERN.search(
        normalise_text(page_text)
    ):
        return True, "PAGE TEXT"

    title_block_text = get_title_block_text(page)

    if CIRCUIT_DRAWING_TITLE_PATTERN.search(
        normalise_text(title_block_text)
    ):
        return True, "BOTTOM TITLE BLOCK"

    return False, "NOT FOUND"


def get_drawing_number(
    page,
) -> str:
    matches = list(
        DRAWING_PATTERN.finditer(
            get_title_block_text(page)
        )
    )

    if not matches:
        return ""

    return matches[-1].group(0).upper()


def get_revision(
    page,
) -> str:
    words = get_bottom_title_block_words(page)

    revision_words = [
        word
        for word in words
        if word["value"].upper()
        in {"REV", "REV.", "REVISION"}
    ]

    if not revision_words:
        return "REVIEW"

    candidates = []

    for revision_word in revision_words:
        for word in words:
            value = word["value"].upper()

            if not re.fullmatch(
                r"[A-Z]{1,3}\d{1,3}",
                value,
            ):
                continue

            if word["x_center"] <= revision_word["x1"]:
                continue

            vertical_distance = abs(
                word["y_center"]
                - revision_word["y_center"]
            )

            if vertical_distance > 20:
                continue

            horizontal_distance = (
                word["x0"]
                - revision_word["x1"]
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
    """
    Example:
        1252/W/NAC/ATL/R32/501

    Returns:
        NAC
    """
    parts = drawing_number.upper().split("/")

    for index, part in enumerate(parts):
        if re.fullmatch(r"R\d+", part):
            if index >= 2:
                return parts[index - 2]

    return ""


# ---------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------

def extract_terminal_references(
    page,
) -> list[dict]:
    terminals = []
    seen = set()

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, text = word[:5]

        match = TERMINAL_PATTERN.fullmatch(
            text.strip(" ,;:()[]{}")
        )

        if not match:
            continue

        rack = match.group("rack").upper()
        column = match.group("column").upper()
        number = match.group("number")

        terminal_name = (
            f"{rack}.{column}{number}"
        )

        x_center = round((x0 + x1) / 2, 2)
        y_center = round((y0 + y1) / 2, 2)

        key = (
            terminal_name,
            round(x_center, 1),
            round(y_center, 1),
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
                "number_parts": parse_terminal_number(
                    number
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


def extract_item_labels(
    page,
) -> list[dict]:
    """
    Extract Item labels and their positions.

    CT./TC.-prefixed labels are preferred. Fallback labels are used
    only when the prefixed labels do not exist on the page.
    """
    page_text = page.get_text(
        "text",
        sort=True,
    )

    labels = []
    seen = set()

    for pattern in [
        ITEM_PATTERN,
        FALLBACK_ITEM_PATTERN,
    ]:
        for match in pattern.finditer(page_text):
            item_name = (
                f"{match.group('item').upper()}"
                f"({match.group('state').upper()})"
            )

            source_text = match.group(0)

            rectangles = page.search_for(source_text)

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

        if labels:
            break

    return labels


# ---------------------------------------------------------------------
# Coloured vector circuit extraction
# ---------------------------------------------------------------------

def drawing_item_to_segments(
    item,
) -> list[tuple[fitz.Point, fitz.Point]]:
    """
    Convert standard PDF vector primitives into simple segments.

    Supported PyMuPDF drawing item types:
        l  line
        re rectangle
        qu quadrilateral
        c  Bezier curve
    """
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
        rect = fitz.Rect(item[1])

        top_left = fitz.Point(rect.x0, rect.y0)
        top_right = fitz.Point(rect.x1, rect.y0)
        bottom_right = fitz.Point(rect.x1, rect.y1)
        bottom_left = fitz.Point(rect.x0, rect.y1)

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


def get_coloured_vector_segments(
    page,
) -> list[dict]:
    """
    Extract solid red/cyan circuit-vector segments only.

    Excludes dashed revision boxes, notes/title-block areas, and
    non-red/non-cyan coloured vectors. This must use the same filtering
    principles as get_circuit_colours().
    """
    segments = []

    page_height = float(page.rect.height)

    # Ignore title block, notes, revision text and lower sheet detail.
    circuit_bottom_limit = page_height * 0.80

    for drawing in page.get_drawings():
        drawing_rect = drawing.get("rect")

        if drawing_rect is None:
            continue

        # Ignore lower title / notes / revision area.
        if drawing_rect.y0 >= circuit_bottom_limit:
            continue

        # Ignore dashed revision/change boundaries.
        if drawing_is_dashed(drawing):
            continue

        stroke_colour = drawing.get("color")
        fill_colour = drawing.get("fill")

        is_red = (
            colour_is_red(stroke_colour)
            or colour_is_red(fill_colour)
        )

        is_cyan = (
            colour_is_cyan(stroke_colour)
            or colour_is_cyan(fill_colour)
        )

        # Your requirement is red/cyan circuitwork only.
        if not is_red and not is_cyan:
            continue

        # Red occupies hue bin 0; cyan normally occupies bin 12–13.
        # HSV is retained because downstream logic groups colours by hue.
        colour = (
            stroke_colour
            if stroke_colour is not None
            else fill_colour
        )

        hsv = rgb_to_hsv(colour)

        if hsv is None:
            continue

        hue, _, _ = hsv

        for item in drawing.get("items", []):
            for start, end in drawing_item_to_segments(item):
                length = point_distance(start, end)

                if length < 1.5:
                    continue

                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "length": length,
                        "hue": hue,
                    }
                )

    return segments


def get_two_dominant_colour_bins(
    segments: list[dict],
    hue_bin_size: int = 15,
) -> tuple[int, int] | None:
    """
    Select two significant colour families dynamically.

    The two colours must be sufficiently different in hue. This
    supports red/cyan, red/blue-green, green/magenta, etc.
    """
    weights = Counter()

    for segment in segments:
        hue_bin = int(
            segment["hue"] // hue_bin_size
        )

        weights[hue_bin] += segment["length"]

    colour_groups = []

    for hue_bin, total_length in weights.items():
        if total_length < 20:
            continue

        colour_groups.append(
            {
                "bin": hue_bin,
                "hue": (
                    hue_bin * hue_bin_size
                    + hue_bin_size / 2
                ),
                "weight": total_length,
            }
        )

    if len(colour_groups) < 2:
        return None

    candidates = []

    for index, first_group in enumerate(
        colour_groups
    ):
        for second_group in colour_groups[index + 1:]:
            hue_distance = circular_hue_distance(
                first_group["hue"],
                second_group["hue"],
            )

            # Avoid selecting two shades of the same plotted colour.
            if hue_distance < 40:
                continue

            candidates.append(
                {
                    "first": first_group,
                    "second": second_group,
                    "score": (
                        first_group["weight"]
                        + second_group["weight"]
                        + hue_distance
                    ),
                }
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: candidate["score"],
        reverse=True,
    )

    selected = candidates[0]

    return (
        selected["first"]["bin"],
        selected["second"]["bin"],
    )


def segment_matches_colour_bin(
    segment: dict,
    colour_bin: int,
    hue_bin_size: int = 15,
) -> bool:
    segment_bin = int(
        segment["hue"] // hue_bin_size
    )

    # Accept nearby HSV bins to tolerate drawing shade variation.
    return abs(segment_bin - colour_bin) <= 1


def get_coloured_circuit_groups(
    page,
) -> dict[int, list[dict]]:
    """
    Return two detected coloured circuit groups.

    Keys:
        1
        2
    """
    segments = get_coloured_vector_segments(page)

    selected_bins = get_two_dominant_colour_bins(
        segments
    )

    if selected_bins is None:
        return {}

    groups = {}

    for group_id, colour_bin in enumerate(
        selected_bins,
        start=1,
    ):
        groups[group_id] = [
            segment
            for segment in segments
            if segment_matches_colour_bin(
                segment,
                colour_bin,
            )
        ]

    return groups


# ---------------------------------------------------------------------
# Terminal-pair candidates
# ---------------------------------------------------------------------

def terminal_distance_to_colour_group(
    terminal: dict,
    segments: list[dict],
) -> float:
    if not segments:
        return 999999.0

    return min(
        point_to_segment_distance(
            terminal["point"],
            segment["start"],
            segment["end"],
        )
        for segment in segments
    )


def classify_terminal_colour_group(
    terminal: dict,
    colour_groups: dict[int, list[dict]],
) -> tuple[int, float] | None:
    distances = []

    for colour_group_id, segments in colour_groups.items():
        distance = terminal_distance_to_colour_group(
            terminal,
            segments,
        )

        distances.append(
            (
                distance,
                colour_group_id,
            )
        )

    if not distances:
        return None

    distances.sort()

    return (
        distances[0][1],
        distances[0][0],
    )


def build_terminal_pair_candidates(
    page,
    terminals: list[dict],
    colour_groups: dict[int, list[dict]],
) -> list[dict]:
    """
    Build terminal pairs using:

    - matching rack;
    - matching column;
    - consecutive terminal numbers;
    - physical proximity;
    - nearest coloured circuit group.

    Rack is only used to avoid invalid references being paired
    together. It is NOT used to identify CT topology.
    """
    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_terminal_distance = max(
        22.0,
        min(page_scale * 0.06, 100.0),
    )

    maximum_pair_distance = max(
        25.0,
        min(page_scale * 0.14, 175.0),
    )

    classified_terminals = []

    for terminal in terminals:
        classification = classify_terminal_colour_group(
            terminal,
            colour_groups,
        )

        if classification is None:
            continue

        colour_group_id, colour_distance = classification

        if colour_distance > maximum_terminal_distance:
            continue

        terminal_copy = terminal.copy()

        terminal_copy["colour_group_id"] = colour_group_id
        terminal_copy["colour_distance"] = colour_distance

        classified_terminals.append(terminal_copy)

    candidates = []

    for first_index, first_terminal in enumerate(
        classified_terminals
    ):
        for second_terminal in classified_terminals[
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

            if (
                first_terminal["colour_group_id"]
                != second_terminal["colour_group_id"]
            ):
                continue

            pair_x = round(
                (
                    first_terminal["x_center"]
                    + second_terminal["x_center"]
                ) / 2,
                2,
            )

            pair_y = round(
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
                    "rack": first_terminal["rack"],
                    "column": first_terminal["column"],
                    "colour_group_id": first_terminal[
                        "colour_group_id"
                    ],
                    "colour_distance": (
                        first_terminal["colour_distance"]
                        + second_terminal["colour_distance"]
                    ) / 2,
                    "pair_distance": pair_distance,
                    "x_center": pair_x,
                    "y_center": pair_y,
                    "point": fitz.Point(pair_x, pair_y),
                    "terminals": [
                        first_terminal,
                        second_terminal,
                    ],
                }
            )

    candidates.sort(
        key=lambda pair: (
            pair["pair_distance"],
            pair["colour_distance"],
        )
    )

    used_terminals = set()
    unique_pairs = []

    for pair in candidates:
        first_terminal = pair["terminals"][0]
        second_terminal = pair["terminals"][1]

        first_key = (
            first_terminal["terminal"],
            round(first_terminal["x_center"], 1),
            round(first_terminal["y_center"], 1),
        )

        second_key = (
            second_terminal["terminal"],
            round(second_terminal["x_center"], 1),
            round(second_terminal["y_center"], 1),
        )

        if (
            first_key in used_terminals
            or second_key in used_terminals
        ):
            continue

        used_terminals.add(first_key)
        used_terminals.add(second_key)

        unique_pairs.append(pair)

    return unique_pairs

def build_fallback_terminal_pair_candidates(
    page,
    terminals: list[dict],
) -> list[dict]:
    """
    Fallback pairing for Continuous Transmission drawings.

    Some valid terminal text labels are not physically close enough to
    the red/cyan vector lines for strict colour-to-terminal association.

    This fallback uses:
      - same rack;
      - same terminal column;
      - consecutive terminal numbers;
      - reasonable physical label distance.

    It deliberately does not require a terminal to be close to a
    coloured circuit segment.
    """
    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_pair_distance = max(
        35.0,
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

            pair_x = round(
                (
                    first_terminal["x_center"]
                    + second_terminal["x_center"]
                ) / 2,
                2,
            )

            pair_y = round(
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
                    "rack": first_terminal["rack"],
                    "column": first_terminal["column"],

                    # Group 0 means fallback/non-colour-specific.
                    "colour_group_id": 0,
                    "colour_distance": 999.0,

                    "pair_distance": pair_distance,
                    "x_center": pair_x,
                    "y_center": pair_y,
                    "point": fitz.Point(pair_x, pair_y),
                    "terminals": [
                        first_terminal,
                        second_terminal,
                    ],
                }
            )

    candidates.sort(
        key=lambda pair: (
            pair["pair_distance"],
            pair["y_center"],
            pair["x_center"],
        )
    )

    # A terminal must belong to only one selected pair.
    used_terminals = set()
    selected_pairs = []

    for pair in candidates:
        first_terminal = pair["terminals"][0]
        second_terminal = pair["terminals"][1]

        first_key = (
            first_terminal["terminal"],
            round(first_terminal["x_center"], 1),
            round(first_terminal["y_center"], 1),
        )

        second_key = (
            second_terminal["terminal"],
            round(second_terminal["x_center"], 1),
            round(second_terminal["y_center"], 1),
        )

        if (
            first_key in used_terminals
            or second_key in used_terminals
        ):
            continue

        used_terminals.add(first_key)
        used_terminals.add(second_key)

        selected_pairs.append(pair)

    return selected_pairs

# ---------------------------------------------------------------------
# Item grouping and Step selection
# ---------------------------------------------------------------------

def pair_to_item_score(
    pair: dict,
    item_label: dict,
) -> float:
    """
    Geometry-only score.

    Vertical relationship is weighted higher because terminals and
    device labels commonly occupy the same drawing circuit row.
    """
    horizontal_distance = abs(
        pair["x_center"]
        - item_label["x_center"]
    )

    vertical_distance = abs(
        pair["y_center"]
        - item_label["y_center"]
    )

    return (
        vertical_distance * 4
        + horizontal_distance
    )


def group_items_by_name(
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    grouped = defaultdict(list)

    for item_label in item_labels:
        grouped[item_label["item"]].append(
            item_label
        )

    return dict(grouped)


def get_item_pair_candidates(
    item_occurrences: list[dict],
    pairs: list[dict],
) -> list[dict]:
    """
    Return all pairs ordered by relationship to a specific Item.

    Pairs are not permanently assigned to only one Item because an
    Item can have two separate branches on a crowded drawing.
    """
    candidates = []

    for pair in pairs:
        distance = min(
            pair_to_item_score(
                pair,
                item_occurrence,
            )
            for item_occurrence in item_occurrences
        )

        pair_copy = pair.copy()
        pair_copy["item_distance"] = distance

        candidates.append(pair_copy)

    candidates.sort(
        key=lambda pair: (
            pair["item_distance"],
            pair["colour_distance"],
            pair["pair_distance"],
        )
    )

    return candidates


def get_segment_projection(
    point: fitz.Point,
    start: fitz.Point,
    end: fitz.Point,
) -> float:
    """
    Return the relative projection of point onto the line:

        start ---------------- end

    0.0 = at start
    1.0 = at end
    0.5 = in the middle

    Values below 0 or above 1 mean the point is outside the
    start/end span.
    """
    dx = end.x - start.x
    dy = end.y - start.y

    length_squared = dx * dx + dy * dy

    if length_squared == 0:
        return 999.0

    return (
        (point.x - start.x) * dx
        + (point.y - start.y) * dy
    ) / length_squared


def choose_step_pairs(
    item_pairs: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Select G1 Steps 6, 7 and 14 using physical circuit geometry.

    No rack ID, terminal column, terminal name, Item name, drawing
    number, location, or fixed colour value is used.

    Required topology:

        Step 7 pair A ----- Step 6 bridge pair ----- Step 7 pair B

    The Step 6 pair must lie geometrically between two outer Step 7
    pairs. Step 14 is the right-most Step 7 pair.

    Example:

        V2.B87 & V2.B88      V1.D89 & V1.D90      V2.C3 & V2.C4
             Step 7                Step 6                Step 7
                                                           Step 14
    """
    if len(item_pairs) < 3:
        return [], [], []

    # Use only the most relevant candidates for the Item. This avoids
    # comparing every unrelated terminal pair on a complex sheet.
    candidate_pairs = sorted(
        item_pairs,
        key=lambda pair: (
            pair["item_distance"],
            pair["colour_distance"],
            pair["pair_distance"],
        ),
    )[:12]

    topology_candidates = []

    # Select every possible Step 6 candidate.
    for source_index, source_pair in enumerate(
        candidate_pairs
    ):
        remaining_pairs = [
            pair
            for index, pair in enumerate(candidate_pairs)
            if index != source_index
        ]

        # Select every possible pair of Step 7 outer candidates.
        for first_index, first_destination in enumerate(
            remaining_pairs
        ):
            for second_destination in remaining_pairs[
                first_index + 1:
            ]:
                first_point = first_destination["point"]
                second_point = second_destination["point"]
                source_point = source_pair["point"]

                outer_span = point_distance(
                    first_point,
                    second_point,
                )

                # Two destination pairs must not overlap physically.
                if outer_span < 20.0:
                    continue

                # Step 6 must fall on / near the line between the
                # two Step 7 pairs.
                source_to_outer_line = (
                    point_to_segment_distance(
                        source_point,
                        first_point,
                        second_point,
                    )
                )

                source_projection = get_segment_projection(
                    source_point,
                    first_point,
                    second_point,
                )

                # The bridge pair should be inside the outer-pair span.
                # A small tolerance permits imperfect PDF coordinates.
                if (
                    source_projection < -0.15
                    or source_projection > 1.15
                ):
                    continue

                first_to_source = point_distance(
                    first_point,
                    source_point,
                )

                second_to_source = point_distance(
                    second_point,
                    source_point,
                )

                # A good middle pair is reasonably balanced between
                # the two outer terminal pairs.
                balance_error = abs(
                    first_to_source
                    - second_to_source
                )

                # Prefer outer pairs sharing a circuit colour family.
                # No actual colour is hardcoded: these group IDs come
                # from dynamic PDF colour extraction.
                destination_colour_penalty = 0.0

                if (
                    first_destination["colour_group_id"]
                    != second_destination["colour_group_id"]
                ):
                    destination_colour_penalty = 80.0

                # Prefer the bridge pair to be near the same coloured
                # circuit family, but do not reject it if the bridge
                # terminal text itself is black.
                source_colour_penalty = 0.0

                if (
                    source_pair["colour_group_id"]
                    not in {
                        first_destination["colour_group_id"],
                        second_destination["colour_group_id"],
                    }
                ):
                    source_colour_penalty = 20.0

                # Lower score is better.
                #
                # Priority:
                # 1. Step 6 must lie on the Step 7-to-Step 7 path.
                # 2. Step 6 should be between the outer pairs.
                # 3. Outer pairs should belong to the same circuit
                #    colour group where possible.
                # 4. All three pairs should be related to the Item.
                score = (
                    source_to_outer_line * 8.0
                    + balance_error * 1.5
                    + destination_colour_penalty
                    + source_colour_penalty
                    + (
                        source_pair["item_distance"]
                        + first_destination["item_distance"]
                        + second_destination["item_distance"]
                    ) * 0.15
                    - outer_span * 0.05
                )

                topology_candidates.append(
                    {
                        "score": score,
                        "source_pair": source_pair,
                        "destination_pairs": [
                            first_destination,
                            second_destination,
                        ],
                        "outer_span": outer_span,
                        "source_to_outer_line": (
                            source_to_outer_line
                        ),
                        "source_projection": (
                            source_projection
                        ),
                        "balance_error": balance_error,
                    }
                )

    if not topology_candidates:
        return [], [], []

    topology_candidates.sort(
        key=lambda candidate: (
            candidate["score"],
            candidate["source_to_outer_line"],
            candidate["balance_error"],
            -candidate["outer_span"],
        )
    )

    selected = topology_candidates[0]

    step_6_pairs = [
        selected["source_pair"]
    ]

    # Required Step 7 order:
    # right-most pair first, then left-most pair.
    step_7_pairs = sorted(
        selected["destination_pairs"],
        key=lambda pair: (
            -pair["x_center"],
            pair["y_center"],
        ),
    )

    # Step 14 = first / right-most Step 7 pair.
    step_14_pairs = step_7_pairs[:1]

    return (
        step_6_pairs,
        step_7_pairs,
        step_14_pairs,
    )


def format_pairs(
    pairs: list[dict],
) -> str:
    return "\n".join(
        f"- {pair['text']}"
        for pair in pairs
    )


def remove_duplicate_terminals(
    terminals: list[dict],
) -> list[dict]:
    unique = []
    seen = set()

    for terminal in terminals:
        key = (
            terminal["terminal"],
            round(terminal["x_center"], 1),
            round(terminal["y_center"], 1),
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(terminal)

    return unique


# ---------------------------------------------------------------------
# CT loop row creation
# ---------------------------------------------------------------------

def create_ct_loop_rows(
    page,
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> tuple[list[dict], list[dict]]:
    """
    CT-loop extraction logic:

        1. Page is identified by CONTINUOUS TRANSMISSION.
        2. Two dominant coloured circuit families are detected.
        3. Valid consecutive terminal-pair candidates are found.
        4. Each Item receives:
             - one Step 6 pair;
             - exactly two Step 7 pairs;
             - first Step 7 pair for Step 14.

    Cable rack topology is not used.
    """
    colour_groups = get_coloured_circuit_groups(page)

    if len(colour_groups) < 2:
        write_log(
            log_callback,
            "No usable coloured circuit groups found.",
            "WARNING",
        )
        return [], []

    terminal_pairs = build_terminal_pair_candidates(
        page,
        terminals,
        colour_groups,
    )

    strict_pair_count = len(terminal_pairs)

    # Pages such as 501 and 503 can have valid terminal references
    # but the terminal text can be physically separated from the
    # red/cyan linework. If strict colour geometry gives fewer than
    # three pairs, use the terminal-number/physical-layout fallback.
    if len(terminal_pairs) < 3:
        fallback_pairs = (
            build_fallback_terminal_pair_candidates(
                page,
                terminals,
            )
        )

        if len(fallback_pairs) > len(terminal_pairs):
            terminal_pairs = fallback_pairs

            write_log(
                log_callback,
                "Using fallback terminal pairing because "
                "strict coloured-circuit pairing found fewer "
                f"than three pairs. strict_pairs="
                f"{strict_pair_count}; "
                f"fallback_pairs={len(fallback_pairs)}.",
                "INFO",
            )

    write_log(
        log_callback,
        "Circuit candidates: "
        f"terminals={len(terminals)}; "
        f"strict_pairs={strict_pair_count}; "
        f"usable_pairs={len(terminal_pairs)}; "
        f"colour_1_segments="
        f"{len(colour_groups.get(1, []))}; "
        f"colour_2_segments="
        f"{len(colour_groups.get(2, []))}.",
        "DEBUG",
    )

    if not terminal_pairs:
        return [], []

    grouped_items = group_items_by_name(item_labels)

    rows = []
    debug_terminals = []

    for item_name, item_occurrences in grouped_items.items():
        item_pairs = get_item_pair_candidates(
            item_occurrences,
            terminal_pairs,
        )

        (
            step_6_pairs,
            step_7_pairs,
            step_14_pairs,
        ) = choose_step_pairs(item_pairs)
        
        write_log(
            log_callback,
            f"Item={item_name}: pair candidates="
            f"{[(pair['text'], pair['colour_group_id'], "
            f"round(pair['item_distance'], 1), "
            f"round(pair['x_center'], 1), "
            f"round(pair['y_center'], 1)) for pair in item_pairs]}; "
            f"Step6={[pair['text'] for pair in step_6_pairs]}; "
            f"Step7={[pair['text'] for pair in step_7_pairs]}.",
            "DEBUG",
        )
        
        if (
            len(step_6_pairs) != 1
            or len(step_7_pairs) != 2
        ):
            write_log(
                log_callback,
                f"Item={item_name}: skipped; "
                "requires one Step 6 pair and two "
                "Step 7 pairs.",
                "DEBUG",
            )
            continue

        for pair in step_6_pairs + step_7_pairs:
            debug_terminals.extend(
                pair["terminals"]
            )

        rows.append(
            {
                "Item": item_name,
                "G1 - Step 6": format_pairs(
                    step_6_pairs
                ),
                "G1 - Step 7": format_pairs(
                    step_7_pairs
                ),
                "G1 - Step 14": format_pairs(
                    step_14_pairs
                ),
                "Review Status": "Draft",
                "Engineer Remarks": (
                    "Candidate generated from Continuous "
                    "Transmission title-block identification "
                    "and coloured circuit geometry. Cable rack "
                    "topology was not used. Verify against "
                    "controlled drawing."
                ),
            }
        )

        write_log(
            log_callback,
            f"Item={item_name}; "
            f"Step6={[pair['text'] for pair in step_6_pairs]}; "
            f"Step7={[pair['text'] for pair in step_7_pairs]}; "
            f"Step14={[pair['text'] for pair in step_14_pairs]}.",
            "DEBUG",
        )

    return (
        rows,
        remove_duplicate_terminals(debug_terminals),
    )


# ---------------------------------------------------------------------
# Page relevance
# ---------------------------------------------------------------------

def page_is_relevant(
    page_text: str,
    log_callback=None,
) -> bool:
    """
    Fast relevance check.

    Continuous Transmission title has already been checked before this
    function is called. Vector/circuit analysis is deliberately not
    done here because it is expensive and is performed only once later
    in create_ct_loop_rows().
    """
    terminal_matches = TERMINAL_PATTERN.findall(
        page_text
    )

    item_matches = (
        ITEM_PATTERN.findall(page_text)
        or FALLBACK_ITEM_PATTERN.findall(
            page_text
        )
    )

    write_log(
        log_callback,
        "Page relevance: "
        f"terminals={len(terminal_matches)}; "
        f"items={len(item_matches)}.",
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
    """
    Red boxes are drawn only around terminals used in final rows.

    It does not box all terminal-looking text on the page.
    """
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
# Continuous Transmission drawing/page filter
# ---------------------------------------------------------------------


def colour_is_red(
    colour,
) -> bool:
    """
    Detect red vector linework, such as:
        (1.0, 0.0, 0.0)
    """
    if not colour or len(colour) < 3:
        return False

    red, green, blue = colour[:3]

    return (
        red >= 0.60
        and green <= 0.45
        and blue <= 0.45
    )


def colour_is_cyan(
    colour,
) -> bool:
    """
    Detect cyan vector linework, such as:
        (0.0, 1.0, 1.0)
    """
    if not colour or len(colour) < 3:
        return False

    red, green, blue = colour[:3]

    return (
        red <= 0.45
        and green >= 0.55
        and blue >= 0.55
    )

def drawing_is_dashed(
    drawing: dict,
) -> bool:
    """
    Dashed objects are commonly revision boundaries, temporary
    mark-ups, or drawing clouds rather than actual circuit paths.
    """
    dashes = drawing.get("dashes")

    if dashes is None:
        return False

    normalised_dashes = str(dashes).replace(
        " ",
        "",
    ).upper()

    return normalised_dashes not in {
        "",
        "NONE",
        "[]0",
    }


def get_circuit_colours(
    page,
) -> set[str]:
    """
    Detect substantial solid red/cyan vector circuitwork.

    Excludes:
      - dashed revision boxes;
      - bottom title block / notes;
      - tiny isolated coloured text/marks.

    Unlike the older version, this accumulates vector segment length
    by colour over the page. That is important because a real circuit
    is often split into many small vector drawing objects.
    """
    page_height = float(page.rect.height)

    # Exclude title block, notes and revision text at the bottom.
    circuit_bottom_limit = page_height * 0.80

    red_length = 0.0
    cyan_length = 0.0

    # Adjust only if necessary after testing. A real coloured circuit
    # normally has hundreds or thousands of points of vector length.
    minimum_circuit_length = 120.0

    try:
        drawings = page.get_drawings()
    except Exception:
        return set()

    for drawing in drawings:
        drawing_rect = drawing.get("rect")

        if drawing_rect is None:
            continue

        # Ignore bottom title block / notes / sheet references.
        if drawing_rect.y0 >= circuit_bottom_limit:
            continue

        # Ignore dashed red revision/change rectangles.
        if drawing_is_dashed(drawing):
            continue

        stroke_colour = drawing.get("color")
        fill_colour = drawing.get("fill")

        is_red = (
            colour_is_red(stroke_colour)
            or colour_is_red(fill_colour)
        )

        is_cyan = (
            colour_is_cyan(stroke_colour)
            or colour_is_cyan(fill_colour)
        )

        if not is_red and not is_cyan:
            continue

        for item in drawing.get("items", []):
            for start, end in drawing_item_to_segments(
                item
            ):
                segment_length = point_distance(
                    start,
                    end,
                )

                # Ignore extremely small components / dots.
                if segment_length < 1.5:
                    continue

                if is_red:
                    red_length += segment_length

                if is_cyan:
                    cyan_length += segment_length

    found_colours = set()

    if red_length >= minimum_circuit_length:
        found_colours.add("RED")

    if cyan_length >= minimum_circuit_length:
        found_colours.add("CYAN")

    return found_colours

def get_bottom_title_block_text(
    page,
    bottom_ratio: float = 0.25,
) -> str:
    """
    Gets text from the bottom 25% of the drawing, including the
    title-block and drawing title area.
    """
    page_height = float(page.rect.height)

    title_block_top = page_height * (
        1 - bottom_ratio
    )

    values = []

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, value = word[:5]

        y_center = (y0 + y1) / 2

        if y_center >= title_block_top:
            values.append(value)

    return normalise_text(
        " ".join(values)
    )


def is_target_circuit_drawing(
    page_text: str,
) -> bool:
    """
    Fast title-text filter.

    Includes Continuous Transmission and Differential Repetition
    drawings. Colour/vector analysis happens only after this check.
    """
    return bool(
        CIRCUIT_DRAWING_TITLE_PATTERN.search(
            normalise_text(page_text)
        )
    )

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
        f"Opening PDF: {pdf_path}",
    )

    with fitz.open(pdf_path) as document:
        total_pages = len(document)

        write_log(
            log_callback,
            f"PDF opened successfully. "
            f"Total pages: {total_pages}.",
        )

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_start_time = time.perf_counter()

            if progress_callback is not None:
                progress_callback(
                    (page_index / total_pages) * 100
                )

            try:
                page_text = page.get_text(
                    "text",
                    sort=True,
                )

                # Fast first filter: do not inspect vectors unless the page title
                # contains CONTINUOUS TRANSMISSION.
                (
                    is_target_title,
                    target_title_source,
                ) = page_has_target_circuit_title(
                    page,
                    page_text,
                )

                if not is_target_title:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "target circuit drawing title not found.",
                        "DEBUG",
                    )
                    continue

                # This calls page.get_drawings(), so do it only after the fast
                # title-text check above.
                circuit_colours = get_circuit_colours(page)

                if not circuit_colours:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "target drawing title found, but no substantial "
                        "solid red/cyan circuitwork was detected.",
                        "DEBUG",
                    )
                    continue

                write_log(
                    log_callback,
                    f"Page {page_number}: target circuit drawing found; "
                    f"vector colours={', '.join(sorted(circuit_colours))}.",
                    "INFO",
                )
                terminal_matches = TERMINAL_PATTERN.findall(
                    page_text
                )

                prefixed_item_matches = ITEM_PATTERN.findall(
                    page_text
                )

                fallback_item_matches = FALLBACK_ITEM_PATTERN.findall(
                    page_text
                )

                write_log(
                    log_callback,
                    f"Page {page_number}: CT diagnostic: "
                    f"terminal_matches={len(terminal_matches)}; "
                    f"prefixed_item_matches={len(prefixed_item_matches)}; "
                    f"fallback_item_matches={len(fallback_item_matches)}.",
                    "INFO",
                )
                # Check Continuous Transmission title, terminal/item text,
                # and coloured circuit-vector groups.
                if not page_is_relevant(
                    page_text,
                    log_callback=log_callback,
                ):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "missing terminal/item pattern or usable "
                        "coloured circuit groups.",
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

                terminals = extract_terminal_references(page)
                item_labels = extract_item_labels(page)

                write_log(
                    log_callback,
                    f"Page {page_number}: "
                    f"drawing={drawing_number}; "
                    f"revision={drawing_revision}; "
                    f"location={location or 'NOT FOUND'}; "
                    f"terminals={len(terminals)}; "
                    f"items={len(item_labels)}.",
                    "DEBUG",
                )

                if not terminals or not item_labels:
                    continue

                rows, debug_terminals = create_ct_loop_rows(
                    page,
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                )

                # PNG rendering is expensive. Create a debug image only if
                # this page produced one or more output rows.
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
                            "A11 CoV Work Procedure Appendix": "G1",
                            "G1 - Step 6": row["G1 - Step 6"],
                            "G1 - Step 7": row["G1 - Step 7"],
                            "G1 - Step 14": row["G1 - Step 14"],
                            "PDF Page": page_number,
                            "Review Status": row["Review Status"],
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
                    f"{len(rows)} row(s) in "
                    f"{page_elapsed:.2f}s.",
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
                "G1 - Step 6",
                "G1 - Step 7",
                "G1 - Step 14",
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
                "Item",
            ],
            kind="stable",
        ).reset_index(drop=True)

        dataframe = dataframe.drop(
            columns=["_drawing_sort"]
        )

        dataframe["No."] = range(
            1,
            len(dataframe) + 1,
        )

    elapsed = time.perf_counter() - start_time

    write_log(
        log_callback,
        "Extraction complete. "
        f"Rows created: {len(dataframe)}. "
        f"Total time: {elapsed:.2f}s.",
    )

    if progress_callback is not None:
        progress_callback(100)

    return dataframe, debug_images
