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

CONTINUOUS_TRANSMISSION_PATTERN = re.compile(
    r"\bCONTINUOUS\s+TRANSMISSION\b",
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


def page_has_continuous_transmission(
    page,
    page_text: str,
) -> tuple[bool, str]:
    """
    Check both all page text and title-block text.

    This supports drawings where CONTINUOUS TRANSMISSION occurs only
    in the bottom-right title block.
    """
    if CONTINUOUS_TRANSMISSION_PATTERN.search(
        normalise_text(page_text)
    ):
        return True, "PAGE TEXT"

    title_block_text = get_title_block_text(page)

    if CONTINUOUS_TRANSMISSION_PATTERN.search(
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
    Get saturated coloured vector segments.

    No cable rack, location, Item name, drawing number, red RGB
    value, or cyan RGB value is hardcoded.
    """
    segments = []

    for drawing in page.get_drawings():
        colours = [
            drawing.get("color"),
            drawing.get("fill"),
        ]

        for colour in colours:
            hsv = rgb_to_hsv(colour)

            if hsv is None:
                continue

            hue, saturation, value = hsv

            # Ignore monochrome and nearly invisible paths.
            if saturation < 0.28:
                continue

            if value < 0.20:
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


def choose_step_pairs(
    item_pairs: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Produce required output shape:

        Step 6:
            exactly one terminal pair

        Step 7:
            exactly two terminal pairs

        Step 14:
            first Step 7 pair

    Preferred selection:
        - Step 7 comes from one coloured circuit group containing
          at least two terminal pairs.
        - Step 6 comes from the other coloured circuit group.

    Fallback:
        - If PDF colour grouping is fragmented, the best three
          geometrically associated pairs are used.

    No cable-rack topology is used.
    """
    if len(item_pairs) < 3:
        return [], [], []

    pairs_by_colour = defaultdict(list)

    for pair in item_pairs:
        pairs_by_colour[
            pair["colour_group_id"]
        ].append(pair)

    # Fallback pairs use colour group 0. They are valid terminal pairs,
    # but they do not carry reliable red/cyan association. In that case,
    # skip colour-group selection and use the geometry ordering below.
    has_only_fallback_pairs = (
        len(pairs_by_colour) == 1
        and 0 in pairs_by_colour
    )

    for pairs in pairs_by_colour.values():
        pairs.sort(
            key=lambda pair: (
                pair["item_distance"],
                pair["colour_distance"],
                -pair["x_center"],
                pair["y_center"],
            )
        )

    colour_groups = sorted(
        pairs_by_colour.items(),
        key=lambda group: (
            len(group[1]),
            -group[1][0]["item_distance"],
        ),
        reverse=True,
    )

    # Preferred: Step 7 has two pairs from one colour group.
    # Do not use this route when all pairs came from the fallback.
    for step_7_colour, step_7_candidates in (
        [] if has_only_fallback_pairs else colour_groups
    ):
        if len(step_7_candidates) < 2:
            continue

        step_6_candidates = []

        for colour_id, candidates in colour_groups:
            if colour_id == step_7_colour:
                continue

            step_6_candidates.extend(candidates)

        if not step_6_candidates:
            continue

        step_6_candidates.sort(
            key=lambda pair: (
                pair["item_distance"],
                pair["colour_distance"],
            )
        )

        step_6_pairs = step_6_candidates[:1]

        step_7_pairs = sorted(
            step_7_candidates[:2],
            key=lambda pair: (
                -pair["x_center"],
                pair["y_center"],
            ),
        )

        step_14_pairs = step_7_pairs[:1]

        return (
            step_6_pairs,
            step_7_pairs,
            step_14_pairs,
        )

    # Fallback for fragmented colour vectors:
    ordered_pairs = sorted(
        item_pairs,
        key=lambda pair: (
            pair["item_distance"],
            pair["colour_distance"],
            -pair["x_center"],
            pair["y_center"],
        )
    )

    if len(ordered_pairs) < 3:
        return [], [], []

    step_6_pairs = ordered_pairs[:1]

    step_7_pairs = sorted(
        ordered_pairs[1:3],
        key=lambda pair: (
            -pair["x_center"],
            pair["y_center"],
        ),
    )

    return (
        step_6_pairs,
        step_7_pairs,
        step_7_pairs[:1],
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
    page,
    page_text: str,
    log_callback=None,
) -> bool:
    (
        has_continuous_transmission,
        transmission_source,
    ) = page_has_continuous_transmission(
        page,
        page_text,
    )

    terminal_matches = TERMINAL_PATTERN.findall(
        page_text
    )

    item_matches = (
        ITEM_PATTERN.findall(page_text)
        or FALLBACK_ITEM_PATTERN.findall(
            page_text
        )
    )

    coloured_segments = get_coloured_vector_segments(
        page
    )

    colour_bins = get_two_dominant_colour_bins(
        coloured_segments
    )

    write_log(
        log_callback,
        "Page relevance: "
        f"continuous_transmission="
        f"{has_continuous_transmission}; "
        f"source={transmission_source}; "
        f"terminals={len(terminal_matches)}; "
        f"items={len(item_matches)}; "
        f"coloured_segments={len(coloured_segments)}; "
        f"colour_bins={colour_bins or 'NOT FOUND'}.",
        "DEBUG",
    )

    return (
        has_continuous_transmission
        and bool(terminal_matches)
        and bool(item_matches)
        and colour_bins is not None
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


def get_circuit_colours(
    page,
) -> set[str]:
    """
    Finds red and cyan vector objects on the PDF page.

    The red/cyan circuit lines in your screenshot are normally PDF
    vector drawings, so page.get_drawings() is used.
    """
    found_colours = set()

    try:
        drawings = page.get_drawings()
    except Exception:
        return found_colours

    for drawing in drawings:
        stroke_colour = drawing.get("color")
        fill_colour = drawing.get("fill")

        if (
            colour_is_red(stroke_colour)
            or colour_is_red(fill_colour)
        ):
            found_colours.add("RED")

        if (
            colour_is_cyan(stroke_colour)
            or colour_is_cyan(fill_colour)
        ):
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


def is_continuous_transmission_page(
    page,
) -> tuple[bool, set[str]]:
    """
    Detect Continuous Transmission drawings by their title text.

    Red/cyan circuit colours are returned for information/logging only.
    They do NOT reject a page because colours can be represented
    differently in different PDFs, or may be raster content.
    """
    full_page_text = normalise_text(
        page.get_text(
            "text",
            sort=True,
        )
    )

    has_continuous_transmission = bool(
        CONTINUOUS_TRANSMISSION_PATTERN.search(
            full_page_text
        )
    )

    circuit_colours = get_circuit_colours(page)

    return (
        has_continuous_transmission,
        circuit_colours,
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

                # Only continue if this page has:
                # - CONTINUOUS TRANSMISSION in the title area, and
                # - red and/or cyan circuit linework.
                is_ct_page, circuit_colours = (
                    is_continuous_transmission_page(page)
                )

                if not is_ct_page:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "CONTINUOUS TRANSMISSION not found in page text.",
                        "DEBUG",
                    )
                    continue

                write_log(
                    log_callback,
                    f"Page {page_number}: Continuous Transmission "
                    f"drawing found; vector colours="
                    f"{', '.join(sorted(circuit_colours)) or 'NOT DETECTED'}.",
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
                    page,
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
