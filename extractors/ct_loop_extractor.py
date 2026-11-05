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
    r"\b"
    r"\d+"
    r"(?:/[A-Z0-9-]+)+"
    r"/R\d+"
    r"/\d+"
    r"(?:/[A-Z0-9-]+)*"
    r"\b",
    re.IGNORECASE,
)

# Examples:
# V0.C89
# V1.D87
# V10.A123
# V0.R40.7
# V1:R40.10
# V2:R23.11
TERMINAL_PATTERN = re.compile(
    r"\b"
    r"(?P<rack>V\d+)"
    r"\s*"
    r"[:.]"
    r"\s*"
    r"(?P<column>[A-Z]+)"
    r"(?P<terminal>\d+(?:[.-]\d+)*)"
    r"\b",
    re.IGNORECASE,
)

# Examples:
# CT.GN1(UP)
# TC.BW1(REP)
# CT.BD1(UP)
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"([A-Z]{1,}\d+)\s*"
    r"\(\s*([A-Z0-9]+)\s*\)",
    re.IGNORECASE,
)

FALLBACK_ITEM_PATTERN = re.compile(
    r"\b([A-Z]{2,}\d+)\s*"
    r"\(\s*([A-Z0-9]+)\s*\)",
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
    """
    Geometric distance from point to a finite vector line segment.
    """
    segment_x = end.x - start.x
    segment_y = end.y - start.y

    segment_length_squared = (
        segment_x * segment_x
        + segment_y * segment_y
    )

    if segment_length_squared == 0:
        return point_distance(point, start)

    projection = (
        (point.x - start.x) * segment_x
        + (point.y - start.y) * segment_y
    ) / segment_length_squared

    projection = max(0.0, min(1.0, projection))

    nearest = fitz.Point(
        start.x + projection * segment_x,
        start.y + projection * segment_y,
    )

    return point_distance(point, nearest)


def normalise_page_text(
    page_text: str,
) -> str:
    return re.sub(
        r"[^A-Z0-9]+",
        " ",
        page_text.upper(),
    ).strip()


def circular_hue_distance(
    first_hue: float,
    second_hue: float,
) -> float:
    difference = abs(first_hue - second_hue)

    return min(
        difference,
        360 - difference,
    )


def hue_bin_for_hue(
    hue: float,
    hue_bin_size: int,
) -> int:
    return int(hue // hue_bin_size)


def rgb_tuple_to_hsv(
    colour,
) -> tuple[float, float, float] | None:
    """
    Converts PyMuPDF colour values to HSV.

    Returns:
        hue:        0..360
        saturation: 0..1
        value:      0..1
    """
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
# Title-block metadata
# ---------------------------------------------------------------------

def normalise_drawing_number(
    drawing_number: str,
) -> str:
    return drawing_number.upper().strip()


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


def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.18,
) -> list[dict]:
    page_height = float(page.rect.height)
    page_width = float(page.rect.width)

    title_block_top = page_height * (
        1 - bottom_ratio
    )

    words = []

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, value = word[:5]
        y_center = (y0 + y1) / 2

        if y_center < title_block_top:
            continue

        words.append(
            {
                "value": value.strip(" ,;:()[]{}"),
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "x_center": round(
                    (x0 + x1) / 2,
                    2,
                ),
                "y_center": round(
                    (y0 + y1) / 2,
                    2,
                ),
            }
        )

    return words


def get_drawing_number_from_title_block(
    title_block_words: list[dict],
) -> str:
    title_block_text = " ".join(
        word["value"]
        for word in title_block_words
    )

    matches = list(
        DRAWING_PATTERN.finditer(title_block_text)
    )

    if not matches:
        return ""

    return matches[-1].group(0).upper()


def is_revision_value(
    value: str,
) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Z]{1,3}\d{1,3}",
            value.strip(" ,;:()[]{}").upper(),
        )
    )


def get_revision_from_title_block(
    title_block_words: list[dict],
) -> str:
    revision_labels = []

    for word in title_block_words:
        value = word["value"].upper()

        if re.fullmatch(r"REV\.?", value):
            revision_labels.append(word)

        elif value == "REVISION":
            revision_labels.append(word)

    if not revision_labels:
        return "REVIEW"

    candidates = []

    for revision_label in revision_labels:
        for word in title_block_words:
            value = word["value"].upper()

            if not is_revision_value(value):
                continue

            if word["x_center"] <= revision_label["x1"]:
                continue

            horizontal_distance = (
                word["x0"]
                - revision_label["x1"]
            )

            vertical_distance = abs(
                word["y_center"]
                - revision_label["y_center"]
            )

            if vertical_distance > 20:
                continue

            candidates.append(
                {
                    "value": value,
                    "score": (
                        horizontal_distance
                        + vertical_distance * 5
                    ),
                }
            )

    if not candidates:
        return "REVIEW"

    candidates.sort(
        key=lambda candidate: candidate["score"]
    )

    return candidates[0]["value"]


def get_title_block_metadata(
    page,
    log_callback=None,
) -> tuple[str, str]:
    title_block_words = get_bottom_title_block_words(
        page
    )

    drawing_number = get_drawing_number_from_title_block(
        title_block_words
    )

    drawing_revision = get_revision_from_title_block(
        title_block_words
    )

    write_log(
        log_callback,
        "Title-block extraction: "
        f"drawing={drawing_number or 'NOT FOUND'}; "
        f"revision={drawing_revision}.",
        "DEBUG",
    )

    return drawing_number, drawing_revision


# ---------------------------------------------------------------------
# Terminal and Item extraction
# ---------------------------------------------------------------------

def parse_terminal_number(
    terminal_number: str,
) -> tuple[int, ...]:
    parts = re.split(
        r"[.-]",
        str(terminal_number),
    )

    return tuple(
        int(part)
        for part in parts
        if part.isdigit()
    )


def terminal_numbers_are_consecutive(
    first_terminal: dict,
    second_terminal: dict,
) -> bool:
    first_parts = first_terminal[
        "terminal_number_parts"
    ]

    second_parts = second_terminal[
        "terminal_number_parts"
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


def extract_terminal_references(
    page,
) -> list[dict]:
    terminals = []
    seen = set()

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, value = word[:5]

        cleaned_value = value.strip(
            " ,;:()[]{}"
        )

        match = TERMINAL_PATTERN.fullmatch(
            cleaned_value
        )

        if not match:
            continue

        rack = match.group("rack").upper()
        column = match.group("column").upper()
        terminal_number = match.group("terminal")

        terminal_text = (
            f"{rack}.{column}{terminal_number}"
        )

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
                "terminal_number": terminal_number,
                "terminal_number_parts": (
                    parse_terminal_number(
                        terminal_number
                    )
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
    Extract CT./TC. Item labels and their locations.

    The fallback pattern is used only where prefixed CT./TC.
    labels are absent.
    """
    page_text = page.get_text(
        "text",
        sort=True,
    )

    item_labels = []
    seen = set()

    for pattern in [
        ITEM_PATTERN,
        FALLBACK_ITEM_PATTERN,
    ]:
        for match in pattern.finditer(page_text):
            item_name = (
                f"{match.group(1).upper()}"
                f"({match.group(2).upper()})"
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

                item_labels.append(
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

        if item_labels:
            break

    return item_labels


# ---------------------------------------------------------------------
# Continuous Transmission page detection
# ---------------------------------------------------------------------

def page_has_continuous_transmission(
    page_text: str,
) -> bool:
    normalised_text = normalise_page_text(page_text)

    return bool(
        CONTINUOUS_TRANSMISSION_PATTERN.search(
            normalised_text
        )
    )


# ---------------------------------------------------------------------
# Coloured vector geometry extraction
# ---------------------------------------------------------------------

def rect_to_segments(
    rectangle: fitz.Rect,
) -> list[tuple[fitz.Point, fitz.Point]]:
    top_left = fitz.Point(rectangle.x0, rectangle.y0)
    top_right = fitz.Point(rectangle.x1, rectangle.y0)
    bottom_right = fitz.Point(rectangle.x1, rectangle.y1)
    bottom_left = fitz.Point(rectangle.x0, rectangle.y1)

    return [
        (top_left, top_right),
        (top_right, bottom_right),
        (bottom_right, bottom_left),
        (bottom_left, top_left),
    ]


def drawing_item_to_segments(
    item,
) -> list[tuple[fitz.Point, fitz.Point]]:
    """
    Extracts basic line geometry from a PyMuPDF drawing item.

    Supported item types normally include:
        l  = line
        re = rectangle
        qu = quadrilateral
        c  = Bezier curve

    Curves are approximated using their first and last points.
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
        return rect_to_segments(
            fitz.Rect(item[1])
        )

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
    Extract saturated coloured vector line segments.

    Black, grey, white and faint lines are excluded.

    This does not rely on fixed red or cyan RGB values.
    """
    segments = []

    for drawing in page.get_drawings():
        colour = drawing.get("color")

        hsv = rgb_tuple_to_hsv(colour)

        if hsv is None:
            continue

        hue, saturation, value = hsv

        # Ignore monochrome drawing content.
        if saturation < 0.35:
            continue

        if value < 0.25:
            continue

        drawing_items = drawing.get("items", [])

        for item in drawing_items:
            for start, end in drawing_item_to_segments(
                item
            ):
                segment_length = point_distance(
                    start,
                    end,
                )

                # Ignore tiny coloured dots and decorative marks.
                if segment_length < 2.0:
                    continue

                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "hue": hue,
                        "saturation": saturation,
                        "value": value,
                        "length": segment_length,
                    }
                )

    return segments


def select_opposite_colour_groups(
    coloured_segments: list[dict],
    hue_bin_size: int = 15,
) -> tuple[int, int] | None:
    """
    Finds two dominant colour groups approximately opposite
    on the HSV colour wheel.

    Red/cyan is one example of opposite hues, but no fixed RGB
    red/cyan values are used.
    """
    hue_weights = Counter()

    for segment in coloured_segments:
        hue_bin = hue_bin_for_hue(
            segment["hue"],
            hue_bin_size,
        )

        hue_weights[hue_bin] += segment["length"]

    significant_bins = []

    for hue_bin, weight in hue_weights.items():
        if weight < 40:
            continue

        hue = (
            hue_bin * hue_bin_size
            + hue_bin_size / 2
        )

        significant_bins.append(
            {
                "hue_bin": hue_bin,
                "hue": hue,
                "weight": weight,
            }
        )

    if len(significant_bins) < 2:
        return None

    candidates = []

    for index, first in enumerate(
        significant_bins
    ):
        for second in significant_bins[index + 1:]:
            hue_distance = circular_hue_distance(
                first["hue"],
                second["hue"],
            )

            opposite_error = abs(
                180 - hue_distance
            )

            if opposite_error > 45:
                continue

            candidates.append(
                {
                    "first_bin": first["hue_bin"],
                    "second_bin": second["hue_bin"],
                    "score": (
                        first["weight"]
                        + second["weight"]
                        - opposite_error
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
        selected["first_bin"],
        selected["second_bin"],
    )


def segment_matches_colour_group(
    segment: dict,
    target_bin: int,
    hue_bin_size: int,
) -> bool:
    current_bin = hue_bin_for_hue(
        segment["hue"],
        hue_bin_size,
    )

    # Allows minor shade variations within a plotted circuit.
    return abs(current_bin - target_bin) <= 1


def build_connected_components(
    segments: list[dict],
    connection_tolerance: float,
) -> list[dict]:
    """
    Builds physical connected components from coloured vector segments.

    Connection is determined only from geometry:
        endpoint-to-endpoint proximity.

    No cable-rack information is used.
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

    for first_index, first_segment in enumerate(
        segments
    ):
        first_points = [
            first_segment["start"],
            first_segment["end"],
        ]

        for second_index in range(
            first_index + 1,
            len(segments),
        ):
            second_segment = segments[second_index]

            second_points = [
                second_segment["start"],
                second_segment["end"],
            ]

            connected = False

            for first_point in first_points:
                for second_point in second_points:
                    if (
                        point_distance(
                            first_point,
                            second_point,
                        )
                        <= connection_tolerance
                    ):
                        connected = True
                        break

                if connected:
                    break

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
            nodes.append(segment["start"])
            nodes.append(segment["end"])

        x_values = [point.x for point in nodes]
        y_values = [point.y for point in nodes]

        components.append(
            {
                "component_id": component_id,
                "segments": component_segments,
                "nodes": nodes,
                "length": sum(
                    segment["length"]
                    for segment in component_segments
                ),
                "rect": fitz.Rect(
                    min(x_values),
                    min(y_values),
                    max(x_values),
                    max(y_values),
                ),
            }
        )

    return components


def get_coloured_circuit_components(
    page,
) -> list[dict]:
    """
    Returns connected vector geometry for the two dominant opposite
    colours on the Continuous Transmission drawing.
    """
    hue_bin_size = 15

    coloured_segments = get_coloured_vector_segments(
        page
    )

    selected_groups = select_opposite_colour_groups(
        coloured_segments,
        hue_bin_size=hue_bin_size,
    )

    if selected_groups is None:
        return []

    first_bin, second_bin = selected_groups

    first_segments = [
        segment
        for segment in coloured_segments
        if segment_matches_colour_group(
            segment,
            first_bin,
            hue_bin_size,
        )
    ]

    second_segments = [
        segment
        for segment in coloured_segments
        if segment_matches_colour_group(
            segment,
            second_bin,
            hue_bin_size,
        )
    ]

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    connection_tolerance = max(
        2.5,
        min(page_scale * 0.004, 8.0),
    )

    first_components = build_connected_components(
        first_segments,
        connection_tolerance,
    )

    second_components = build_connected_components(
        second_segments,
        connection_tolerance,
    )

    components = []

    for colour_name, group_components in [
        ("COLOUR_1", first_components),
        ("COLOUR_2", second_components),
    ]:
        for component in group_components:
            if component["length"] < 25:
                continue

            component["colour_group"] = colour_name
            components.append(component)

    return components


# ---------------------------------------------------------------------
# Terminal selection from actual circuit connections
# ---------------------------------------------------------------------

def terminal_to_component_node_distance(
    terminal: dict,
    component: dict,
) -> float:
    """
    Distance from terminal text centre to nearest actual coloured
    circuit connection node.

    This intentionally uses segment endpoints/nodes rather than
    the large bounding rectangle of a coloured drawing object.
    """
    if not component["nodes"]:
        return 999999.0

    terminal_point = terminal["point"]

    return min(
        point_distance(
            terminal_point,
            node,
        )
        for node in component["nodes"]
    )


def terminal_to_component_segment_distance(
    terminal: dict,
    component: dict,
) -> float:
    if not component["segments"]:
        return 999999.0

    terminal_point = terminal["point"]

    return min(
        point_to_segment_distance(
            terminal_point,
            segment["start"],
            segment["end"],
        )
        for segment in component["segments"]
    )


def select_circuit_terminals(
    terminals: list[dict],
    components: list[dict],
    page,
) -> list[dict]:
    """
    Selects terminals attached to actual coloured-circuit nodes.

    A terminal close only to the middle of a line is not accepted
    unless it is also close to a physical endpoint/junction.

    This prevents inline wire references such as V1.R28.3 and
    V1.R28.4 from being treated as test terminals.
    """
    if not terminals or not components:
        return []

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    node_tolerance = max(
        14.0,
        min(page_scale * 0.018, 32.0),
    )

    segment_tolerance = max(
        8.0,
        min(page_scale * 0.010, 18.0),
    )

    accepted = []

    for terminal in terminals:
        best_match = None

        for component in components:
            node_distance = (
                terminal_to_component_node_distance(
                    terminal,
                    component,
                )
            )

            segment_distance = (
                terminal_to_component_segment_distance(
                    terminal,
                    component,
                )
            )

            # Terminal must be near a circuit node/connection.
            # Merely crossing a coloured line is insufficient.
            if node_distance > node_tolerance:
                continue

            if segment_distance > segment_tolerance:
                continue

            score = (
                node_distance * 3
                + segment_distance
            )

            if (
                best_match is None
                or score < best_match["score"]
            ):
                best_match = {
                    "component_id": component[
                        "component_id"
                    ],
                    "colour_group": component[
                        "colour_group"
                    ],
                    "node_distance": node_distance,
                    "segment_distance": segment_distance,
                    "score": score,
                }

        if best_match is None:
            continue

        selected_terminal = terminal.copy()

        selected_terminal["component_id"] = (
            best_match["component_id"]
        )

        selected_terminal["colour_group"] = (
            best_match["colour_group"]
        )

        selected_terminal["node_distance"] = round(
            best_match["node_distance"],
            2,
        )

        selected_terminal["segment_distance"] = round(
            best_match["segment_distance"],
            2,
        )

        accepted.append(selected_terminal)

    return accepted


# ---------------------------------------------------------------------
# Terminal pairing
# ---------------------------------------------------------------------

def terminal_pair_distance(
    first_terminal: dict,
    second_terminal: dict,
) -> float:
    return point_distance(
        first_terminal["point"],
        second_terminal["point"],
    )


def pair_selected_terminals(
    terminals: list[dict],
    page,
) -> list[dict]:
    """
    Pairs physically close consecutive terminals.

    Rack and column are used only to ensure that:
        V2.C3 & V2.C4

    is not incorrectly paired as:
        V1.C3 & V2.C4

    They are not used to identify a CT-loop page or circuit.
    """
    if not terminals:
        return []

    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    maximum_pair_distance = max(
        25.0,
        min(page_scale * 0.11, 130.0),
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

            geometry_distance = terminal_pair_distance(
                first_terminal,
                second_terminal,
            )

            if geometry_distance > maximum_pair_distance:
                continue

            candidates.append(
                {
                    "first": first_terminal,
                    "second": second_terminal,
                    "distance": geometry_distance,
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

        pair_colour_groups = {
            first["colour_group"],
            second["colour_group"],
        }

        pair_component_ids = {
            first["component_id"],
            second["component_id"],
        }

        pairs.append(
            {
                "text": (
                    f"{first['terminal']} & "
                    f"{second['terminal']}"
                ),
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
                "colour_groups": pair_colour_groups,
                "component_ids": pair_component_ids,
                "review_required": False,
            }
        )

    return pairs


# ---------------------------------------------------------------------
# Item-to-pair grouping
# ---------------------------------------------------------------------

def pair_to_item_distance(
    pair: dict,
    item_label: dict,
) -> float:
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


def group_item_occurrences(
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    grouped = defaultdict(list)

    for item_label in item_labels:
        grouped[item_label["item"]].append(
            item_label
        )

    return dict(grouped)


def find_pairs_for_item(
    item_occurrences: list[dict],
    pairs: list[dict],
) -> list[dict]:
    """
    Associates accepted circuit pairs to an Item using the nearest
    occurrence of that Item label.
    """
    scored_pairs = []

    for pair in pairs:
        distance = min(
            pair_to_item_distance(
                pair,
                item_occurrence,
            )
            for item_occurrence in item_occurrences
        )

        pair_copy = pair.copy()
        pair_copy["item_distance"] = distance

        scored_pairs.append(pair_copy)

    scored_pairs.sort(
        key=lambda pair: pair["item_distance"]
    )

    return scored_pairs


def format_pairs(
    pairs: list[dict],
) -> str:
    return "\n".join(
        f"- {pair['text']}"
        for pair in pairs
    )


# ---------------------------------------------------------------------
# CT-loop row creation
# ---------------------------------------------------------------------

def create_ct_loop_rows(
    page,
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> tuple[list[dict], list[dict]]:
    """
    Creates CT-loop checklist rows using:

        CONTINUOUS TRANSMISSION wording
            +
        two opposite coloured vector circuits
            +
        terminal labels connected to actual coloured circuit nodes.

    Cable racks are not used for CT-loop detection.

    Returns:
        rows
        selected_terminals_for_debug_image
    """
    if not terminals or not item_labels:
        return [], []

    components = get_coloured_circuit_components(
        page
    )

    if not components:
        write_log(
            log_callback,
            "No substantial opposite coloured vector "
            "circuit components found.",
            "WARNING",
        )
        return [], []

    selected_terminals = select_circuit_terminals(
        terminals,
        components,
        page,
    )

    if not selected_terminals:
        write_log(
            log_callback,
            "No terminals attached to coloured circuit "
            "connection nodes were found.",
            "WARNING",
        )
        return [], []

    pairs = pair_selected_terminals(
        selected_terminals,
        page,
    )

    if not pairs:
        write_log(
            log_callback,
            "No consecutive terminal pairs found on "
            "selected coloured circuits.",
            "WARNING",
        )
        return [], selected_terminals

    item_occurrences_by_name = group_item_occurrences(
        item_labels
    )

    rows = []

    for item_name, item_occurrences in (
        item_occurrences_by_name.items()
    ):
        item_pairs = find_pairs_for_item(
            item_occurrences,
            pairs,
        )

        if len(item_pairs) < 2:
            write_log(
                log_callback,
                f"Item={item_name}: skipped; fewer than "
                "two accepted coloured-circuit terminal pairs.",
                "DEBUG",
            )
            continue

        # First nearest pair is Step 6.
        step_6_pairs = item_pairs[:1]

        # Remaining circuit pairs are Step 7.
        step_7_pairs = item_pairs[1:3]

        if not step_7_pairs:
            continue

        # Step 14 is first Step 7 test pair.
        step_14_pairs = step_7_pairs[:1]

        review_required = any(
            pair["review_required"]
            for pair in (
                step_6_pairs
                + step_7_pairs
            )
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
                "Review Status": (
                    "REVIEW REQUIRED"
                    if review_required
                    else "Draft"
                ),
                "Engineer Remarks": (
                    "Candidate created from Continuous "
                    "Transmission coloured circuit geometry. "
                    "Cable rack topology was not used. "
                    "Verify against controlled drawing."
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

    return rows, selected_terminals


# ---------------------------------------------------------------------
# Page relevance
# ---------------------------------------------------------------------

def page_is_relevant(
    page,
    page_text: str,
    log_callback=None,
) -> bool:
    has_continuous_transmission = (
        page_has_continuous_transmission(page_text)
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

    opposite_colours = select_opposite_colour_groups(
        coloured_segments
    )

    write_log(
        log_callback,
        "Page relevance: "
        f"continuous_transmission="
        f"{has_continuous_transmission}; "
        f"terminals={len(terminal_matches)}; "
        f"items={len(item_matches)}; "
        f"coloured_segments={len(coloured_segments)}; "
        f"opposite_colours="
        f"{opposite_colours or 'NOT FOUND'}.",
        "DEBUG",
    )

    return (
        has_continuous_transmission
        and bool(terminal_matches)
        and bool(item_matches)
        and opposite_colours is not None
    )


# ---------------------------------------------------------------------
# Debug image
# ---------------------------------------------------------------------

def create_debug_image(
    page,
    selected_terminals: list[dict],
) -> bytes:
    """
    Draws boxes only around accepted coloured-circuit terminals.

    It does not draw boxes around every regex terminal reference.
    Therefore unrelated inline references such as V1.R28.3 are not
    boxed unless they genuinely connect to a coloured circuit node.
    """
    box_colour = (1.0, 0.0, 0.0)
    padding = 2.5

    shape = page.new_shape()

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
            color=box_colour,
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

                drawing_number, drawing_revision = (
                    get_title_block_metadata(
                        page,
                        log_callback=log_callback,
                    )
                )

                if not drawing_number:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "Drawing Number missing.",
                        "WARNING",
                    )
                    continue

                drawing_number = normalise_drawing_number(
                    drawing_number
                )

                drawing_revision = (
                    drawing_revision.upper().strip()
                )

                location = get_location_from_drawing_number(
                    drawing_number
                )

                terminals = extract_terminal_references(
                    page
                )

                item_labels = extract_item_labels(
                    page
                )

                if not terminals or not item_labels:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        f"terminals={len(terminals)}; "
                        f"items={len(item_labels)}.",
                        "WARNING",
                    )
                    continue

                ct_loop_rows, selected_terminals = (
                    create_ct_loop_rows(
                        page,
                        terminals,
                        item_labels,
                        log_callback=log_callback,
                    )
                )

                # Debug image contains only selected terminals,
                # not every Vx.Xnn regex match.
                debug_images[page_number] = (
                    create_debug_image(
                        page,
                        selected_terminals,
                    )
                )

                for row in ct_loop_rows:
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
                                "G1"
                            ),
                            "G1 - Step 6": (
                                row["G1 - Step 6"]
                            ),
                            "G1 - Step 7": (
                                row["G1 - Step 7"]
                            ),
                            "G1 - Step 14": (
                                row["G1 - Step 14"]
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
                    f"{len(ct_loop_rows)} row(s) in "
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
            ascending=True,
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
