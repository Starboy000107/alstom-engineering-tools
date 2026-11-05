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

# Examples:
# V0.C89
# V1.D87
# V0.R40.7
# V1:R40.10
TERMINAL_PATTERN = re.compile(
    r"\b"
    r"(?P<rack>V\d+)"
    r"\s*[:.]\s*"
    r"(?P<column>[A-Z]+)"
    r"(?P<number>\d+(?:[.-]\d+)*)"
    r"\b",
    re.IGNORECASE,
)

# Examples:
# CT.BU1(UP)
# TC.BW1(REP)
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"(?P<item>[A-Z]+\d+)"
    r"\s*\(\s*"
    r"(?P<state>[A-Z0-9]+)"
    r"\s*\)",
    re.IGNORECASE,
)

# Examples:
# BU1(UP)
# BW1(REP)
# KX1(BRANCH)
FALLBACK_ITEM_PATTERN = re.compile(
    r"\b"
    r"(?P<item>[A-Z]{2,}\d+)"
    r"\s*\(\s*"
    r"(?P<state>[A-Z0-9]+)"
    r"\s*\)",
    re.IGNORECASE,
)

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

    projection = (
        (point.x - start.x) * dx
        + (point.y - start.y) * dy
    ) / length_squared

    projection = max(0.0, min(1.0, projection))

    closest = fitz.Point(
        start.x + projection * dx,
        start.y + projection * dy,
    )

    return point_distance(point, closest)


def circular_hue_distance(
    first_hue: float,
    second_hue: float,
) -> float:
    difference = abs(first_hue - second_hue)

    return min(
        difference,
        360 - difference,
    )


def parse_terminal_number(
    number: str,
) -> tuple[int, ...]:
    return tuple(
        int(part)
        for part in re.split(r"[.-]", number)
        if part.isdigit()
    )


def terminal_numbers_are_consecutive(
    first: dict,
    second: dict,
) -> bool:
    first_parts = first["number_parts"]
    second_parts = second["number_parts"]

    if not first_parts or not second_parts:
        return False

    if len(first_parts) != len(second_parts):
        return False

    if len(first_parts) == 1:
        return second_parts[0] == first_parts[0] + 1

    return (
        first_parts[:-1] == second_parts[:-1]
        and second_parts[-1] == first_parts[-1] + 1
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


# ---------------------------------------------------------------------
# Title block
# ---------------------------------------------------------------------

def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.35,
) -> list[dict]:
    """
    Continuous Transmission is commonly written in the title block.

    35% is intentionally used because title blocks may extend
    significantly above the bottom edge of an A1 / A0 drawing.
    """
    title_top = page.rect.height * (1 - bottom_ratio)

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
    Finds CONTINUOUS TRANSMISSION either in the whole drawing
    text or specifically in the bottom title block.
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


def get_drawing_number_from_title_block(
    page,
) -> str:
    title_block_text = get_title_block_text(page)

    matches = list(
        DRAWING_PATTERN.finditer(title_block_text)
    )

    if not matches:
        return ""

    return matches[-1].group(0).upper()


def get_revision_from_title_block(
    page,
) -> str:
    words = get_bottom_title_block_words(page)

    revision_labels = []

    for word in words:
        value = word["value"].upper()

        if value in {"REV", "REV.", "REVISION"}:
            revision_labels.append(word)

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
        x0, y0, x1, y1, value = word[:5]

        match = TERMINAL_PATTERN.fullmatch(
            value.strip(" ,;:()[]{}")
        )

        if not match:
            continue

        rack = match.group("rack").upper()
        column = match.group("column").upper()
        number = match.group("number")

        terminal_text = f"{rack}.{column}{number}"

        x_center = round((x0 + x1) / 2, 2)
        y_center = round((y0 + y1) / 2, 2)

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
                "number_parts": parse_terminal_number(
                    number
                ),
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "x_center": x_center,
                "y_center": y_center,
                "point": fitz.Point(x_center, y_center),
            }
        )

    return terminals


def extract_item_labels(
    page,
) -> list[dict]:
    """
    Extract Item labels and physical positions.

    Prefixed CT./TC. labels are preferred. The fallback pattern
    supports drawings where the Item prefix is not written.
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

            for rectangle in page.search_for(source_text):
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
# Coloured circuit detection
# ---------------------------------------------------------------------

def drawing_item_to_segments(
    item,
) -> list[tuple[fitz.Point, fitz.Point]]:
    """
    Convert normal PDF drawing primitives into line segments.
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


def get_coloured_vector_segments(
    page,
) -> list[dict]:
    """
    Find saturated coloured vector geometry.

    This is dynamic and does not use:
    - red/cyan RGB values;
    - cable rack identities;
    - locations;
    - Item names;
    - terminal numbers.
    """
    segments = []

    for drawing in page.get_drawings():
        possible_colours = [
            drawing.get("color"),
            drawing.get("fill"),
        ]

        for colour in possible_colours:
            hsv = rgb_to_hsv(colour)

            if hsv is None:
                continue

            hue, saturation, value = hsv

            # Exclude monochrome drawing content.
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
                            "saturation": saturation,
                            "value": value,
                        }
                    )

    return segments


def get_two_dominant_colour_bins(
    coloured_segments: list[dict],
    hue_bin_size: int = 15,
) -> tuple[int, int] | None:
    """
    Finds the two largest saturated colour families.

    They must be meaningfully different. This supports red/cyan,
    red/blue-green, magenta/green etc. without fixed RGB values.
    """
    weights = Counter()

    for segment in coloured_segments:
        hue_bin = int(
            segment["hue"] // hue_bin_size
        )

        weights[hue_bin] += segment["length"]

    groups = []

    for hue_bin, weight in weights.items():
        if weight < 25:
            continue

        groups.append(
            {
                "hue_bin": hue_bin,
                "hue": (
                    hue_bin * hue_bin_size
                    + hue_bin_size / 2
                ),
                "weight": weight,
            }
        )

    if len(groups) < 2:
        return None

    candidates = []

    for index, first in enumerate(groups):
        for second in groups[index + 1:]:
            hue_distance = circular_hue_distance(
                first["hue"],
                second["hue"],
            )

            # Do not select two shades of the same colour.
            if hue_distance < 40:
                continue

            candidates.append(
                {
                    "first": first,
                    "second": second,
                    "score": (
                        first["weight"]
                        + second["weight"]
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

    best = candidates[0]

    return (
        best["first"]["hue_bin"],
        best["second"]["hue_bin"],
    )


def segment_matches_colour_bin(
    segment: dict,
    colour_bin: int,
    hue_bin_size: int = 15,
) -> bool:
    segment_bin = int(
        segment["hue"] // hue_bin_size
    )

    return abs(segment_bin - colour_bin) <= 1


def get_selected_circuit_segments(
    page,
) -> dict[int, list[dict]]:
    """
    Return the two dominant coloured circuit segment groups.
    """
    segments = get_coloured_vector_segments(page)

    colour_bins = get_two_dominant_colour_bins(
        segments
    )

    if colour_bins is None:
        return {}

    groups = {}

    for colour_id, colour_bin in enumerate(
        colour_bins,
        start=1,
    ):
        groups[colour_id] = [
            segment
            for segment in segments
            if segment_matches_colour_bin(
                segment,
                colour_bin,
            )
        ]

    return groups


# ---------------------------------------------------------------------
# Terminal association with coloured circuits
# ---------------------------------------------------------------------

def terminal_distance_to_segments(
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


def select_terminals_near_coloured_circuits(
    page,
    terminals: list[dict],
    colour_segments: dict[int, list[dict]],
) -> list[dict]:
    """
    Select terminals close to the actual coloured circuit lines.

    Unlike the earlier strict implementation, this does NOT require
    terminal text to be located at a line endpoint. Terminal text is
    usually positioned beside a terminal symbol, not on the endpoint.

    The selected terminal must be reasonably close to one of the two
    coloured circuits. The threshold is proportional to page size.
    """
    if not terminals or not colour_segments:
        return []

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_distance = max(
        22.0,
        min(page_scale * 0.045, 72.0),
    )

    selected = []

    for terminal in terminals:
        colour_distances = []

        for colour_id, segments in colour_segments.items():
            distance = terminal_distance_to_segments(
                terminal,
                segments,
            )

            colour_distances.append(
                (
                    distance,
                    colour_id,
                )
            )

        colour_distances.sort()

        if not colour_distances:
            continue

        best_distance, best_colour = colour_distances[0]

        if best_distance > maximum_distance:
            continue

        terminal_copy = terminal.copy()

        terminal_copy["colour_id"] = best_colour
        terminal_copy["colour_distance"] = round(
            best_distance,
            2,
        )

        selected.append(terminal_copy)

    return selected


# ---------------------------------------------------------------------
# Pair terminals
# ---------------------------------------------------------------------

def pair_terminals(
    page,
    selected_terminals: list[dict],
) -> list[dict]:
    """
    Make terminal pairs from nearby consecutive references.

    Rack and column are used only to ensure a valid electrical
    terminal pair, such as:

        V2.C3 & V2.C4

    and prevent invalid cross-reference pairs such as:

        V1.C3 & V2.C4

    Cable rack topology is not used to find CT loops.
    """
    if not selected_terminals:
        return []

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_pair_distance = max(
        28.0,
        min(page_scale * 0.14, 170.0),
    )

    candidates = []

    for first_index, first in enumerate(
        selected_terminals
    ):
        for second in selected_terminals[
            first_index + 1:
        ]:
            if first["rack"] != second["rack"]:
                continue

            if first["column"] != second["column"]:
                continue

            if not terminal_numbers_are_consecutive(
                first,
                second,
            ):
                continue

            distance = point_distance(
                first["point"],
                second["point"],
            )

            if distance > maximum_pair_distance:
                continue

            # Both labels should belong to the same detected coloured
            # circuit group whenever possible.
            if first["colour_id"] != second["colour_id"]:
                continue

            candidates.append(
                {
                    "first": first,
                    "second": second,
                    "distance": distance,
                    "colour_id": first["colour_id"],
                }
            )

    candidates.sort(
        key=lambda candidate: candidate["distance"]
    )

    used = set()
    pairs = []

    for candidate in candidates:
        first = candidate["first"]
        second = candidate["second"]

        first_key = (
            first["terminal"],
            round(first["x_center"], 1),
            round(first["y_center"], 1),
        )

        second_key = (
            second["terminal"],
            round(second["x_center"], 1),
            round(second["y_center"], 1),
        )

        if first_key in used or second_key in used:
            continue

        used.add(first_key)
        used.add(second_key)

        pairs.append(
            {
                "text": (
                    f"{first['terminal']} & "
                    f"{second['terminal']}"
                ),
                "colour_id": candidate["colour_id"],
                "x_center": round(
                    (
                        first["x_center"]
                        + second["x_center"]
                    ) / 2,
                    2,
                ),
                "y_center": round(
                    (
                        first["y_center"]
                        + second["y_center"]
                    ) / 2,
                    2,
                ),
                "point": fitz.Point(
                    (
                        first["x_center"]
                        + second["x_center"]
                    ) / 2,
                    (
                        first["y_center"]
                        + second["y_center"]
                    ) / 2,
                ),
                "terminals": [
                    first,
                    second,
                ],
            }
        )

    return pairs


# ---------------------------------------------------------------------
# Item ownership
# ---------------------------------------------------------------------

def pair_to_item_score(
    pair: dict,
    item: dict,
) -> float:
    """
    Dynamic geometry score.

    Vertical alignment is weighted more strongly because CT Items and
    their terminal references are commonly in the same circuit row.
    """
    horizontal = abs(
        pair["x_center"]
        - item["x_center"]
    )

    vertical = abs(
        pair["y_center"]
        - item["y_center"]
    )

    return vertical * 3.5 + horizontal


def group_items_by_name(
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    grouped = defaultdict(list)

    for item in item_labels:
        grouped[item["item"]].append(item)

    return dict(grouped)


def assign_pairs_to_nearest_item(
    pairs: list[dict],
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    """
    Every pair belongs to one Item only.

    This avoids reusing one terminal pair in multiple output rows.
    """
    grouped = defaultdict(list)

    for pair in pairs:
        if not item_labels:
            continue

        best_item = min(
            item_labels,
            key=lambda item: pair_to_item_score(
                pair,
                item,
            ),
        )

        pair_copy = pair.copy()

        pair_copy["item_score"] = pair_to_item_score(
            pair,
            best_item,
        )

        grouped[best_item["item"]].append(pair_copy)

    return dict(grouped)


def format_pairs(
    pairs: list[dict],
) -> str:
    return "\n".join(
        f"- {pair['text']}"
        for pair in pairs
    )


# ---------------------------------------------------------------------
# Step selection
# ---------------------------------------------------------------------

def choose_step_pairs(
    item_pairs: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Required G1 layout:

        Step 6:
            one terminal pair

        Step 7:
            exactly two terminal pairs

        Step 14:
            the first Step 7 pair

    The code uses detected coloured circuit groups to make Step 6
    and Step 7 come from different coloured circuit groups where
    possible. No rack topology is used.
    """
    if len(item_pairs) < 3:
        return [], [], []

    pairs_by_colour = defaultdict(list)

    for pair in item_pairs:
        pairs_by_colour[pair["colour_id"]].append(pair)

    for pairs in pairs_by_colour.values():
        pairs.sort(
            key=lambda pair: (
                pair["item_score"],
                -pair["x_center"],
                pair["y_center"],
            )
        )

    sorted_groups = sorted(
        pairs_by_colour.items(),
        key=lambda group: (
            len(group[1]),
            -group[1][0]["item_score"],
        ),
        reverse=True,
    )

    # Preferred behaviour:
    # - colour group with at least two pairs -> Step 7
    # - another colour group -> Step 6
    for step_7_colour, step_7_candidates in sorted_groups:
        if len(step_7_candidates) < 2:
            continue

        step_6_candidates = []

        for colour_id, candidates in sorted_groups:
            if colour_id == step_7_colour:
                continue

            step_6_candidates.extend(candidates)

        if step_6_candidates:
            step_6_candidates.sort(
                key=lambda pair: pair["item_score"]
            )

            step_6_pairs = step_6_candidates[:1]

            step_7_pairs = sorted(
                step_7_candidates[:2],
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

    # Fallback:
    # If colours are fragmented in the PDF, retain the three closest
    # valid circuit pairs rather than returning no rows.
    ordered = sorted(
        item_pairs,
        key=lambda pair: (
            pair["item_score"],
            -pair["x_center"],
            pair["y_center"],
        ),
    )

    if len(ordered) < 3:
        return [], [], []

    step_6_pairs = ordered[:1]

    step_7_pairs = sorted(
        ordered[1:3],
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


# ---------------------------------------------------------------------
# CT-loop extraction
# ---------------------------------------------------------------------

def create_ct_loop_rows(
    page,
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> tuple[list[dict], list[dict]]:
    """
    Extraction sequence:

        1. Page identified as Continuous Transmission.
        2. Find two significant coloured circuit families.
        3. Find terminal labels near coloured circuit linework.
        4. Build consecutive terminal pairs.
        5. Assign each pair to its nearest Item.
        6. Create G1 rows:
           - one Step 6 pair;
           - exactly two Step 7 pairs;
           - first Step 7 pair for Step 14.

    Cable-rack topology is not used.
    """
    colour_segments = get_selected_circuit_segments(
        page
    )

    if len(colour_segments) < 2:
        write_log(
            log_callback,
            "CT loop skipped: fewer than two significant "
            "coloured circuit groups were detected.",
            "WARNING",
        )
        return [], []

    selected_terminals = (
        select_terminals_near_coloured_circuits(
            page,
            terminals,
            colour_segments,
        )
    )

    write_log(
        log_callback,
        "CT circuit selection: "
        f"all_terminals={len(terminals)}; "
        f"selected_terminals={len(selected_terminals)}; "
        f"colour_1_segments="
        f"{len(colour_segments.get(1, []))}; "
        f"colour_2_segments="
        f"{len(colour_segments.get(2, []))}.",
        "DEBUG",
    )

    pairs = pair_terminals(
        page,
        selected_terminals,
    )

    write_log(
        log_callback,
        f"CT terminal pairing: pairs={len(pairs)}.",
        "DEBUG",
    )

    if not pairs:
        return [], []

    pairs_by_item = assign_pairs_to_nearest_item(
        pairs,
        item_labels,
    )

    item_occurrences = group_items_by_name(item_labels)

    rows = []
    output_terminals = []

    for item_name in sorted(item_occurrences):
        item_pairs = pairs_by_item.get(item_name, [])

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
                f"available_pairs={len(item_pairs)}; "
                "requires 1 Step 6 pair and "
                "2 Step 7 pairs.",
                "DEBUG",
            )
            continue

        for pair in step_6_pairs + step_7_pairs:
            output_terminals.extend(
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
                    "Candidate created from Continuous "
                    "Transmission title-block detection and "
                    "coloured circuit geometry. Cable rack "
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

    return rows, remove_duplicate_terminals(output_terminals)


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
# Page relevance
# ---------------------------------------------------------------------

def page_is_relevant(
    page,
    page_text: str,
    log_callback=None,
) -> bool:
    (
        has_continuous_transmission,
        continuous_transmission_source,
    ) = page_has_continuous_transmission(
        page,
        page_text,
    )

    terminals = TERMINAL_PATTERN.findall(page_text)

    items = (
        ITEM_PATTERN.findall(page_text)
        or FALLBACK_ITEM_PATTERN.findall(page_text)
    )

    coloured_segments = get_coloured_vector_segments(
        page
    )

    colour_groups = get_two_dominant_colour_bins(
        coloured_segments
    )

    write_log(
        log_callback,
        "Page relevance: "
        f"continuous_transmission="
        f"{has_continuous_transmission}; "
        f"source={continuous_transmission_source}; "
        f"terminals={len(terminals)}; "
        f"items={len(items)}; "
        f"coloured_segments={len(coloured_segments)}; "
        f"colour_groups={colour_groups or 'NOT FOUND'}.",
        "DEBUG",
    )

    return (
        has_continuous_transmission
        and bool(terminals)
        and bool(items)
        and colour_groups is not None
    )


# ---------------------------------------------------------------------
# Debug image
# ---------------------------------------------------------------------

def create_debug_image(
    page,
    selected_terminals: list[dict],
) -> bytes:
    """
    Boxes only terminals included in final candidate rows.

    It will not draw a box around every regex reference on the page.
    """
    shape = page.new_shape()

    padding = 2.5

    for terminal in selected_terminals:
        terminal_box = fitz.Rect(
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

        shape.draw_rect(terminal_box)

    if selected_terminals:
        shape.finish(
            color=(1.0, 0.0, 0.0),
            width=1.3,
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

                if not page_is_relevant(
                    page,
                    page_text,
                    log_callback=log_callback,
                ):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "not a Continuous Transmission "
                        "coloured circuit page.",
                        "DEBUG",
                    )
                    continue

                drawing_number = (
                    get_drawing_number_from_title_block(page)
                )

                drawing_revision = (
                    get_revision_from_title_block(page)
                )

                if not drawing_number:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "drawing number not found.",
                        "WARNING",
                    )
                    continue

                drawing_number = drawing_number.upper().strip()
                drawing_revision = drawing_revision.upper().strip()

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

                rows, output_terminals = create_ct_loop_rows(
                    page,
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                )

                debug_images[page_number] = (
                    create_debug_image(
                        page,
                        output_terminals,
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
