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
# V10.A123
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
# CT.BD1(UP)
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"(?P<item>[A-Z]+\d+)"
    r"\s*\(\s*"
    r"(?P<state>[A-Z0-9]+)"
    r"\s*\)",
    re.IGNORECASE,
)

# Used only where CT./TC. prefix is absent.
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


# ---------------------------------------------------------------------
# Title-block metadata
# ---------------------------------------------------------------------

def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.30,
) -> list[dict]:
    """
    Extract words from the bottom drawing region.

    Continuous Transmission wording commonly appears in the title
    block, so this deliberately examines the lower 30 percent.
    """
    title_top = page.rect.height * (1 - bottom_ratio)

    words = []

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, value = word[:5]

        if ((y0 + y1) / 2) < title_top:
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


def get_drawing_number_from_title_block(
    title_block_words: list[dict],
) -> str:
    text = " ".join(
        word["value"]
        for word in title_block_words
    )

    matches = list(DRAWING_PATTERN.finditer(text))

    if not matches:
        return ""

    return matches[-1].group(0).upper()


def get_revision_from_title_block(
    title_block_words: list[dict],
) -> str:
    revision_labels = []

    for word in title_block_words:
        value = word["value"].upper()

        if value in {"REV", "REV.", "REVISION"}:
            revision_labels.append(word)

    if not revision_labels:
        return "REVIEW"

    candidates = []

    for label in revision_labels:
        for word in title_block_words:
            value = word["value"].upper()

            if not re.fullmatch(r"[A-Z]{1,3}\d{1,3}", value):
                continue

            if word["x_center"] <= label["x1"]:
                continue

            horizontal = word["x0"] - label["x1"]
            vertical = abs(
                word["y_center"]
                - label["y_center"]
            )

            if vertical > 20:
                continue

            candidates.append(
                (
                    horizontal + vertical * 5,
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


def get_title_block_metadata(
    page,
) -> tuple[str, str]:
    words = get_bottom_title_block_words(page)

    return (
        get_drawing_number_from_title_block(words),
        get_revision_from_title_block(words),
    )


# ---------------------------------------------------------------------
# Continuous Transmission detection
# ---------------------------------------------------------------------

def page_has_continuous_transmission(
    page,
    page_text: str,
) -> tuple[bool, str]:
    """
    Search both full page text and the explicit bottom title block.

    This supports drawings where CONTINUOUS TRANSMISSION exists only
    in the title block.
    """
    if CONTINUOUS_TRANSMISSION_PATTERN.search(
        normalise_text(page_text)
    ):
        return True, "PAGE TEXT"

    title_words = get_bottom_title_block_words(
        page,
        bottom_ratio=0.30,
    )

    title_text = " ".join(
        word["value"]
        for word in title_words
    )

    if CONTINUOUS_TRANSMISSION_PATTERN.search(
        normalise_text(title_text)
    ):
        return True, "BOTTOM TITLE BLOCK"

    return False, "NOT FOUND"


# ---------------------------------------------------------------------
# Terminal and Item extraction
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

        terminal = f"{rack}.{column}{number}"

        x_center = round((x0 + x1) / 2, 2)
        y_center = round((y0 + y1) / 2, 2)

        key = (
            terminal,
            round(x_center, 1),
            round(y_center, 1),
        )

        if key in seen:
            continue

        seen.add(key)

        terminals.append(
            {
                "terminal": terminal,
                "rack": rack,
                "column": column,
                "number": number,
                "number_parts": parse_terminal_number(number),
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
    Extract all Item labels with their physical PDF positions.
    """
    page_text = page.get_text("text", sort=True)

    labels = []
    seen = set()

    for pattern in [
        ITEM_PATTERN,
        FALLBACK_ITEM_PATTERN,
    ]:
        for match in pattern.finditer(page_text):
            item = (
                f"{match.group('item').upper()}"
                f"({match.group('state').upper()})"
            )

            source_text = match.group(0)

            for rect in page.search_for(source_text):
                key = (
                    item,
                    round(rect.x0, 1),
                    round(rect.y0, 1),
                )

                if key in seen:
                    continue

                seen.add(key)

                labels.append(
                    {
                        "item": item,
                        "x_center": round(
                            (rect.x0 + rect.x1) / 2,
                            2,
                        ),
                        "y_center": round(
                            (rect.y0 + rect.y1) / 2,
                            2,
                        ),
                        "point": fitz.Point(
                            (rect.x0 + rect.x1) / 2,
                            (rect.y0 + rect.y1) / 2,
                        ),
                    }
                )

        if labels:
            break

    return labels


# ---------------------------------------------------------------------
# Coloured vector extraction
# ---------------------------------------------------------------------

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


def drawing_item_to_segments(
    item,
) -> list[tuple[fitz.Point, fitz.Point]]:
    """
    Convert PyMuPDF drawing objects to vector segments.

    Supported PDF vector primitives:
        l  = line
        re = rectangle
        qu = quadrilateral
        c  = Bezier curve
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
    Extract saturated coloured PDF vector paths.

    No red, cyan, V0, V1, V2, rack, location or drawing-number
    values are hardcoded.
    """
    segments = []

    for drawing in page.get_drawings():
        hsv = rgb_to_hsv(drawing.get("color"))

        if hsv is None:
            continue

        hue, saturation, value = hsv

        # Ignore black, grey, white and weak colours.
        if saturation < 0.35 or value < 0.25:
            continue

        for item in drawing.get("items", []):
            for start, end in drawing_item_to_segments(item):
                length = point_distance(start, end)

                if length < 2:
                    continue

                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "hue": hue,
                        "length": length,
                    }
                )

    return segments


def select_two_colour_groups(
    segments: list[dict],
    hue_bin_size: int = 15,
) -> tuple[int, int] | None:
    """
    Finds the two dominant saturated colours.

    The colours are selected dynamically from PDF vector geometry.
    They must be visibly different colours, but the code does not
    assume exact red/cyan RGB values.
    """
    weights = Counter()

    for segment in segments:
        hue_bin = int(segment["hue"] // hue_bin_size)
        weights[hue_bin] += segment["length"]

    groups = []

    for hue_bin, weight in weights.items():
        if weight < 40:
            continue

        groups.append(
            {
                "bin": hue_bin,
                "hue": hue_bin * hue_bin_size + hue_bin_size / 2,
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

            # Two circuit colours must be sufficiently different.
            if hue_distance < 50:
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

    selected = candidates[0]

    return (
        selected["first"]["bin"],
        selected["second"]["bin"],
    )


def segment_belongs_to_group(
    segment: dict,
    group_bin: int,
    hue_bin_size: int = 15,
) -> bool:
    segment_bin = int(segment["hue"] // hue_bin_size)

    return abs(segment_bin - group_bin) <= 1


def build_colour_components(
    segments: list[dict],
    tolerance: float,
) -> list[dict]:
    """
    Build connected vector components from individual line segments.
    """
    if not segments:
        return []

    parent = list(range(len(segments)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]

        return index

    def union(first: int, second: int):
        first_root = find(first)
        second_root = find(second)

        if first_root != second_root:
            parent[second_root] = first_root

    for first_index, first in enumerate(segments):
        first_nodes = [first["start"], first["end"]]

        for second_index in range(
            first_index + 1,
            len(segments),
        ):
            second = segments[second_index]
            second_nodes = [second["start"], second["end"]]

            connected = any(
                point_distance(first_node, second_node)
                <= tolerance
                for first_node in first_nodes
                for second_node in second_nodes
            )

            if connected:
                union(first_index, second_index)

    grouped = defaultdict(list)

    for index, segment in enumerate(segments):
        grouped[find(index)].append(segment)

    components = []

    for component_id, component_segments in enumerate(
        grouped.values()
    ):
        nodes = []

        for segment in component_segments:
            nodes.extend(
                [
                    segment["start"],
                    segment["end"],
                ]
            )

        components.append(
            {
                "component_id": component_id,
                "segments": component_segments,
                "nodes": nodes,
                "length": sum(
                    segment["length"]
                    for segment in component_segments
                ),
            }
        )

    return components


def get_coloured_circuit_components(
    page,
) -> list[dict]:
    """
    Return connected circuit components for the two detected colours.
    """
    segments = get_coloured_vector_segments(page)

    selected_groups = select_two_colour_groups(segments)

    if selected_groups is None:
        return []

    page_scale = max(page.rect.width, page.rect.height)

    connection_tolerance = max(
        2.5,
        min(page_scale * 0.004, 8.0),
    )

    components = []

    for colour_id, hue_bin in enumerate(
        selected_groups,
        start=1,
    ):
        colour_segments = [
            segment
            for segment in segments
            if segment_belongs_to_group(
                segment,
                hue_bin,
            )
        ]

        for component in build_colour_components(
            colour_segments,
            connection_tolerance,
        ):
            if component["length"] < 25:
                continue

            component["colour_id"] = colour_id
            components.append(component)

    return components


# ---------------------------------------------------------------------
# Select terminals genuinely connected to coloured circuits
# ---------------------------------------------------------------------

def distance_to_component_node(
    terminal: dict,
    component: dict,
) -> float:
    return min(
        point_distance(terminal["point"], node)
        for node in component["nodes"]
    )


def distance_to_component_segment(
    terminal: dict,
    component: dict,
) -> float:
    return min(
        point_to_segment_distance(
            terminal["point"],
            segment["start"],
            segment["end"],
        )
        for segment in component["segments"]
    )


def select_coloured_circuit_terminals(
    page,
    terminals: list[dict],
    components: list[dict],
) -> list[dict]:
    """
    Select only terminals attached to coloured circuit geometry.

    A terminal must be near both:
    - a coloured circuit node / junction;
    - a coloured circuit segment.

    This avoids boxing text that is merely near a coloured cable path,
    such as V1.R28.3 / V1.R28.4 in the example drawing.
    """
    page_scale = max(page.rect.width, page.rect.height)

    node_tolerance = max(
        14.0,
        min(page_scale * 0.018, 32.0),
    )

    segment_tolerance = max(
        8.0,
        min(page_scale * 0.010, 18.0),
    )

    selected = []

    for terminal in terminals:
        best = None

        for component in components:
            node_distance = distance_to_component_node(
                terminal,
                component,
            )

            segment_distance = distance_to_component_segment(
                terminal,
                component,
            )

            if node_distance > node_tolerance:
                continue

            if segment_distance > segment_tolerance:
                continue

            score = node_distance * 3 + segment_distance

            if best is None or score < best["score"]:
                best = {
                    "score": score,
                    "colour_id": component["colour_id"],
                    "component_id": component["component_id"],
                }

        if best is None:
            continue

        selected_terminal = terminal.copy()
        selected_terminal.update(best)

        selected.append(selected_terminal)

    return selected


# ---------------------------------------------------------------------
# Pair selected terminals
# ---------------------------------------------------------------------

def pair_coloured_terminals(
    page,
    terminals: list[dict],
) -> list[dict]:
    """
    Pair consecutive terminal references by physical proximity.

    Rack and column are used only to stop invalid pair combinations.
    They are not used to identify CT-loop topology or pages.
    """
    page_scale = max(page.rect.width, page.rect.height)

    maximum_pair_distance = max(
        25.0,
        min(page_scale * 0.11, 130.0),
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

            distance = point_distance(
                first["point"],
                second["point"],
            )

            if distance > maximum_pair_distance:
                continue

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
                    (first["x_center"] + second["x_center"]) / 2,
                    2,
                ),
                "y_center": round(
                    (first["y_center"] + second["y_center"]) / 2,
                    2,
                ),
                "point": fitz.Point(
                    (first["x_center"] + second["x_center"]) / 2,
                    (first["y_center"] + second["y_center"]) / 2,
                ),
                "terminals": [first, second],
            }
        )

    return pairs


# ---------------------------------------------------------------------
# Item ownership and Step 6 / Step 7 / Step 14 creation
# ---------------------------------------------------------------------

def pair_item_score(
    pair: dict,
    item: dict,
) -> float:
    """
    Prefer a pair vertically aligned with the Item.

    The weighting is geometry only and contains no rack, location,
    drawing-number, terminal-number or Item-name assumptions.
    """
    horizontal = abs(pair["x_center"] - item["x_center"])
    vertical = abs(pair["y_center"] - item["y_center"])

    return vertical * 4 + horizontal


def assign_pairs_to_items(
    pairs: list[dict],
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    """
    Assign each pair to only one Item: the closest Item occurrence.

    This prevents the same pair being reused on every Item row.
    """
    grouped = defaultdict(list)

    for pair in pairs:
        if not item_labels:
            continue

        nearest_item = min(
            item_labels,
            key=lambda item: pair_item_score(pair, item),
        )

        pair_copy = pair.copy()
        pair_copy["item_score"] = pair_item_score(
            pair,
            nearest_item,
        )

        grouped[nearest_item["item"]].append(pair_copy)

    return dict(grouped)


def format_pairs(
    pairs: list[dict],
) -> str:
    return "\n".join(
        f"- {pair['text']}"
        for pair in pairs
    )


def select_step_pairs_for_item(
    item_pairs: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Selects:

        Step 6  = one terminal pair
        Step 7  = exactly two terminal pairs
        Step 14 = first Step 7 pair

    The selection is dynamic:

    - pairs are first separated by actual coloured circuit group;
    - the colour group with the larger valid pair set becomes Step 7;
    - the other colour group becomes Step 6;
    - Step 7 is always limited to two pairs because the G1 procedure
      requires two terminal-pair checks.

    No V0/V1/V2, cable rack, location or Item name is hardcoded.
    """
    pairs_by_colour = defaultdict(list)

    for pair in item_pairs:
        pairs_by_colour[pair["colour_id"]].append(pair)

    if len(pairs_by_colour) < 2:
        return [], [], []

    for pairs in pairs_by_colour.values():
        pairs.sort(
            key=lambda pair: (
                pair["item_score"],
                -pair["x_center"],
                pair["y_center"],
            )
        )

    colour_groups = sorted(
        pairs_by_colour.items(),
        key=lambda entry: (
            len(entry[1]),
            -min(
                pair["item_score"]
                for pair in entry[1]
            ),
        ),
        reverse=True,
    )

    step_7_colour, step_7_candidates = colour_groups[0]

    remaining_groups = [
        group
        for group in colour_groups[1:]
        if group[0] != step_7_colour
    ]

    if not remaining_groups:
        return [], [], []

    _, step_6_candidates = remaining_groups[0]

    if not step_6_candidates:
        return [], [], []

    if len(step_7_candidates) < 2:
        return [], [], []

    step_6_pairs = step_6_candidates[:1]

    # Step 7 is always exactly two terminal pairs.
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


def create_ct_loop_rows(
    page,
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> tuple[list[dict], list[dict]]:
    """
    CT-loop extraction flow:

        1. Find Continuous Transmission drawing page.
        2. Identify the two significant coloured circuits.
        3. Select terminals genuinely attached to coloured circuits.
        4. Pair physically adjacent consecutive terminals.
        5. Allocate pairs to their nearest Item.
        6. Produce:
            Step 6  = one pair
            Step 7  = exactly two pairs
            Step 14 = first Step 7 pair.

    Cable rack topology is not used.
    """
    components = get_coloured_circuit_components(page)

    if not components:
        write_log(
            log_callback,
            "No coloured circuit components found.",
            "WARNING",
        )
        return [], []

    selected_terminals = select_coloured_circuit_terminals(
        page,
        terminals,
        components,
    )

    if not selected_terminals:
        write_log(
            log_callback,
            "No terminals connected to coloured circuit "
            "nodes were found.",
            "WARNING",
        )
        return [], []

    pairs = pair_coloured_terminals(
        page,
        selected_terminals,
    )

    if not pairs:
        write_log(
            log_callback,
            "No valid coloured-circuit terminal pairs found.",
            "WARNING",
        )
        return [], selected_terminals

    pairs_by_item = assign_pairs_to_items(
        pairs,
        item_labels,
    )

    rows = []

    for item_name in sorted(pairs_by_item):
        item_pairs = pairs_by_item[item_name]

        (
            step_6_pairs,
            step_7_pairs,
            step_14_pairs,
        ) = select_step_pairs_for_item(item_pairs)

        if not step_6_pairs or len(step_7_pairs) != 2:
            write_log(
                log_callback,
                f"Item={item_name}: skipped; "
                "requires one Step 6 pair and exactly "
                "two Step 7 pairs.",
                "DEBUG",
            )
            continue

        selected_pair_terminals = []

        for pair in (
            step_6_pairs
            + step_7_pairs
        ):
            selected_pair_terminals.extend(
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
                    "coloured circuit vector geometry. "
                    "Cable rack topology was not used. "
                    "Verify against controlled drawing."
                ),
                "_selected_terminals": selected_pair_terminals,
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

    return rows, selected_terminals


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
        source,
    ) = page_has_continuous_transmission(
        page,
        page_text,
    )

    terminal_matches = TERMINAL_PATTERN.findall(
        page_text
    )

    item_matches = (
        ITEM_PATTERN.findall(page_text)
        or FALLBACK_ITEM_PATTERN.findall(page_text)
    )

    coloured_segments = get_coloured_vector_segments(page)

    colour_groups = select_two_colour_groups(
        coloured_segments
    )

    write_log(
        log_callback,
        "Page relevance: "
        f"continuous_transmission="
        f"{has_continuous_transmission}; "
        f"source={source}; "
        f"terminals={len(terminal_matches)}; "
        f"items={len(item_matches)}; "
        f"coloured_segments={len(coloured_segments)}; "
        f"colour_groups={colour_groups or 'NOT FOUND'}.",
        "DEBUG",
    )

    return (
        has_continuous_transmission
        and bool(terminal_matches)
        and bool(item_matches)
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
    Draw red boxes only around selected coloured-circuit terminals.

    It does not draw boxes around every terminal regex match.
    """
    shape = page.new_shape()
    padding = 2.5

    for terminal in selected_terminals:
        box = fitz.Rect(
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

        shape.draw_rect(box)

    if selected_terminals:
        shape.finish(
            color=(1.0, 0.0, 0.0),
            width=1.2,
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
            page_start = time.perf_counter()

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
                        "coloured-circuit page.",
                        "DEBUG",
                    )
                    continue

                drawing_number, drawing_revision = (
                    get_title_block_metadata(page)
                )

                if not drawing_number:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "drawing number missing.",
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

                rows, selected_terminals = create_ct_loop_rows(
                    page,
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                )

                debug_images[page_number] = create_debug_image(
                    page,
                    selected_terminals,
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

                write_log(
                    log_callback,
                    f"Page {page_number}: created "
                    f"{len(rows)} row(s) in "
                    f"{time.perf_counter() - page_start:.2f}s.",
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
            by=["_drawing_sort", "Item"],
            kind="stable",
        ).reset_index(drop=True)

        dataframe = dataframe.drop(
            columns=["_drawing_sort"]
        )

        dataframe["No."] = range(
            1,
            len(dataframe) + 1,
        )

    write_log(
        log_callback,
        "Extraction complete. "
        f"Rows created: {len(dataframe)}. "
        f"Total time: "
        f"{time.perf_counter() - start_time:.2f}s.",
    )

    if progress_callback is not None:
        progress_callback(100)

    return dataframe, debug_images
