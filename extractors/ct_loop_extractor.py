from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd


# ---------------------------------------------------------------------
# Text patterns
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

# Matches references such as:
# V0.R42.1
# V0:R42.1
# V1.A65
# V10.B87
# X1.R40.10
#
# The extractor does NOT use the rack identity for topology selection.
# It is retained only because it is part of the terminal reference text.
TERMINAL_PATTERN = re.compile(
    r"\b"
    r"(?P<prefix>[A-Z]{1,6}\d{0,4})"
    r"\s*"
    r"[:.]"
    r"\s*"
    r"(?P<column>[A-Z]{1,6})"
    r"(?P<terminal>\d+(?:[.-]\d+)*)"
    r"\b",
    re.IGNORECASE,
)

# Examples:
# CT.AY1B(UP)
# CT.YA1B(UP)
# TC.BW1(REP)
# CT.BD1(UP)
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"(?P<item>[A-Z][A-Z0-9-]*)\s*"
    r"\(\s*(?P<suffix>[A-Z0-9-]+)\s*\)",
    re.IGNORECASE,
)

# Used only if no CT./TC. item label is found.
FALLBACK_ITEM_PATTERN = re.compile(
    r"\b"
    r"(?P<item>[A-Z]{2,}[A-Z0-9-]*)"
    r"\s*"
    r"\(\s*(?P<suffix>[A-Z0-9-]+)\s*\)",
    re.IGNORECASE,
)

# The page must be a Continuous Transmission page.
# This deliberately accepts truncated text such as:
# CONTINUOUS TRANSMISSI
CT_LOOP_PAGE_PATTERN = re.compile(
    r"CONTINUOUS\s+TRANSMISSI",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Colour settings
# ---------------------------------------------------------------------
#
# The supplied example contains RED and CYAN circuits.
#
# In drawings cyan is commonly used instead of dark blue. Therefore the
# "BLUE" category below includes both blue and cyan / light-blue circuits.
#
# PyMuPDF returns drawing colours as tuples of floats:
# (red, green, blue), where every channel is between 0.0 and 1.0.
# ---------------------------------------------------------------------

RED_MIN = 0.45
RED_DOMINANCE = 0.16

BLUE_MIN = 0.40
BLUE_DOMINANCE = 0.10

CYAN_MIN = 0.45
CYAN_RED_MAX = 0.45

# Geometry tolerances are dynamically scaled from PDF page dimensions.
MIN_SNAP_DISTANCE = 3.0
MAX_SNAP_DISTANCE = 12.0

MIN_TERMINAL_DISTANCE = 8.0
MAX_TERMINAL_DISTANCE = 30.0

MIN_ITEM_DISTANCE = 15.0
MAX_ITEM_DISTANCE = 110.0


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

def write_log(
    log_callback,
    message: str,
    level: str = "INFO",
) -> None:
    if log_callback is not None:
        log_callback(message, level)


# ---------------------------------------------------------------------
# Basic helpers
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
        1252/W/OLY/ALT/R32/501

    Returns:
        OLY
    """
    parts = drawing_number.upper().split("/")

    for index, part in enumerate(parts):
        if re.fullmatch(r"R\d+", part):
            if index >= 2:
                return parts[index - 2]

    return ""


def point_distance(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    return math.hypot(
        first[0] - second[0],
        first[1] - second[1],
    )


def rect_distance(
    first: fitz.Rect,
    second: fitz.Rect,
) -> float:
    """
    Shortest Euclidean distance between two rectangles.

    Returns zero if the rectangles overlap.
    """
    horizontal_gap = max(
        first.x0 - second.x1,
        second.x0 - first.x1,
        0,
    )

    vertical_gap = max(
        first.y0 - second.y1,
        second.y0 - first.y1,
        0,
    )

    return math.hypot(
        horizontal_gap,
        vertical_gap,
    )


def make_rect_from_points(
    first: tuple[float, float],
    second: tuple[float, float],
    padding: float = 0.0,
) -> fitz.Rect:
    return fitz.Rect(
        min(first[0], second[0]) - padding,
        min(first[1], second[1]) - padding,
        max(first[0], second[0]) + padding,
        max(first[1], second[1]) + padding,
    )


def get_snap_distance(
    page,
) -> float:
    return max(
        MIN_SNAP_DISTANCE,
        min(
            float(page.rect.width) * 0.004,
            MAX_SNAP_DISTANCE,
        ),
    )


def get_terminal_connection_distance(
    page,
) -> float:
    return max(
        MIN_TERMINAL_DISTANCE,
        min(
            float(page.rect.width) * 0.020,
            MAX_TERMINAL_DISTANCE,
        ),
    )


def get_item_connection_distance(
    page,
) -> float:
    return max(
        MIN_ITEM_DISTANCE,
        min(
            float(page.rect.width) * 0.075,
            MAX_ITEM_DISTANCE,
        ),
    )


# ---------------------------------------------------------------------
# Title block metadata
# ---------------------------------------------------------------------

def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.18,
) -> list[dict]:
    page_height = float(page.rect.height)

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
# Red / blue / cyan circuit colour detection
# ---------------------------------------------------------------------

def colour_is_red(
    colour,
) -> bool:
    if not colour or len(colour) < 3:
        return False

    red, green, blue = colour[:3]

    return (
        red >= RED_MIN
        and red > green + RED_DOMINANCE
        and red > blue + RED_DOMINANCE
    )


def colour_is_blue_or_cyan(
    colour,
) -> bool:
    """
    Includes normal blue and cyan/light-blue CAD circuit colours.
    """
    if not colour or len(colour) < 3:
        return False

    red, green, blue = colour[:3]

    is_blue = (
        blue >= BLUE_MIN
        and blue > red + BLUE_DOMINANCE
        and blue > green * 0.60
    )

    is_cyan = (
        green >= CYAN_MIN
        and blue >= CYAN_MIN
        and red <= CYAN_RED_MAX
    )

    return is_blue or is_cyan


def get_colour_name(
    colour,
) -> str:
    if colour_is_red(colour):
        return "RED"

    if colour_is_blue_or_cyan(colour):
        return "BLUE/CYAN"

    return ""


# ---------------------------------------------------------------------
# Coloured vector circuit extraction
# ---------------------------------------------------------------------

def point_tuple(
    point,
) -> tuple[float, float]:
    return (
        float(point.x),
        float(point.y),
    )


def get_drawing_line_segments(
    drawing: dict,
) -> list[dict]:
    """
    Extract usable line segments from a PyMuPDF vector drawing.

    Engineering PDFs normally represent wires as "l" line objects.
    Curved wires are also supported through cubic ("c") and quadratic
    ("qu") path commands.

    This code does not use rack names, lane position, cable-rack labels,
    or fixed drawing layout assumptions.
    """
    segments = []

    for item in drawing.get("items", []):
        if not item:
            continue

        operation = item[0]

        # Straight vector line:
        # ("l", Point(start), Point(end))
        if operation == "l" and len(item) >= 3:
            first = point_tuple(item[1])
            second = point_tuple(item[2])

            segments.append(
                {
                    "start": first,
                    "end": second,
                    "rect": make_rect_from_points(
                        first,
                        second,
                    ),
                }
            )

        # Cubic Bezier:
        # ("c", start, control_1, control_2, end)
        #
        # The end-to-end segment is enough for connectivity matching.
        elif operation == "c" and len(item) >= 5:
            first = point_tuple(item[1])
            second = point_tuple(item[4])

            segments.append(
                {
                    "start": first,
                    "end": second,
                    "rect": make_rect_from_points(
                        first,
                        second,
                    ),
                }
            )

        # Quadratic Bezier:
        # ("qu", start, control, end)
        elif operation == "qu" and len(item) >= 4:
            first = point_tuple(item[1])
            second = point_tuple(item[3])

            segments.append(
                {
                    "start": first,
                    "end": second,
                    "rect": make_rect_from_points(
                        first,
                        second,
                    ),
                }
            )

    return segments


def extract_coloured_segments(
    page,
    log_callback=None,
) -> list[dict]:
    """
    Extract RED and BLUE/CYAN vector wire segments.

    The result is geometry only. No cable-rack or terminal-lane logic is
    used here.
    """
    segments = []

    for drawing in page.get_drawings():
        colour_name = get_colour_name(
            drawing.get("color")
        )

        if not colour_name:
            colour_name = get_colour_name(
                drawing.get("fill")
            )

        if not colour_name:
            continue

        drawing_segments = get_drawing_line_segments(
            drawing
        )

        for segment in drawing_segments:
            segment["colour"] = colour_name
            segments.append(segment)

    colour_counts = defaultdict(int)

    for segment in segments:
        colour_counts[segment["colour"]] += 1

    write_log(
        log_callback,
        "Coloured vector extraction: "
        f"red_segments={colour_counts['RED']}; "
        f"blue_cyan_segments={colour_counts['BLUE/CYAN']}.",
        "DEBUG",
    )

    return segments


# ---------------------------------------------------------------------
# Coloured circuit graph construction
# ---------------------------------------------------------------------

class DisjointSet:
    def __init__(
        self,
        count: int,
    ) -> None:
        self.parent = list(range(count))
        self.rank = [0] * count

    def find(
        self,
        value: int,
    ) -> int:
        if self.parent[value] != value:
            self.parent[value] = self.find(
                self.parent[value]
            )

        return self.parent[value]

    def union(
        self,
        first: int,
        second: int,
    ) -> None:
        first_root = self.find(first)
        second_root = self.find(second)

        if first_root == second_root:
            return

        if self.rank[first_root] < self.rank[second_root]:
            self.parent[first_root] = second_root

        elif self.rank[first_root] > self.rank[second_root]:
            self.parent[second_root] = first_root

        else:
            self.parent[second_root] = first_root
            self.rank[first_root] += 1


def endpoint_grid_key(
    point: tuple[float, float],
    grid_size: float,
) -> tuple[int, int]:
    return (
        int(point[0] // grid_size),
        int(point[1] // grid_size),
    )


def nearby_grid_keys(
    key: tuple[int, int],
) -> list[tuple[int, int]]:
    keys = []

    for x_offset in (-1, 0, 1):
        for y_offset in (-1, 0, 1):
            keys.append(
                (
                    key[0] + x_offset,
                    key[1] + y_offset,
                )
            )

    return keys


def build_coloured_circuit_components(
    page,
    segments: list[dict],
) -> list[dict]:
    """
    Build connected circuits from coloured vector line segments.

    Segments are connected when their endpoints meet or are close enough
    to be joined visually in the drawing.

    Red and BLUE/CYAN circuits are intentionally kept separate. A red
    circuit will never be merged into a nearby cyan circuit merely due to
    visual proximity.
    """
    if not segments:
        return []

    snap_distance = get_snap_distance(page)

    components = []

    for colour_name in ("RED", "BLUE/CYAN"):
        colour_segments = [
            segment
            for segment in segments
            if segment["colour"] == colour_name
        ]

        if not colour_segments:
            continue

        disjoint_set = DisjointSet(
            len(colour_segments)
        )

        endpoint_index = defaultdict(list)

        for index, segment in enumerate(colour_segments):
            for endpoint_name in ("start", "end"):
                point = segment[endpoint_name]

                grid_key = endpoint_grid_key(
                    point,
                    snap_distance,
                )

                for nearby_key in nearby_grid_keys(
                    grid_key
                ):
                    for (
                        existing_index,
                        existing_point,
                    ) in endpoint_index[nearby_key]:
                        if point_distance(
                            point,
                            existing_point,
                        ) <= snap_distance:
                            disjoint_set.union(
                                index,
                                existing_index,
                            )

                endpoint_index[grid_key].append(
                    (index, point)
                )

        grouped_segments = defaultdict(list)

        for index, segment in enumerate(colour_segments):
            root = disjoint_set.find(index)

            grouped_segments[root].append(segment)

        for component_segments in grouped_segments.values():
            x_values = []
            y_values = []
            endpoint_counts = defaultdict(int)

            for segment in component_segments:
                for endpoint_name in ("start", "end"):
                    point = segment[endpoint_name]

                    x_values.append(point[0])
                    y_values.append(point[1])

                    key = (
                        round(point[0] / snap_distance),
                        round(point[1] / snap_distance),
                    )

                    endpoint_counts[key] += 1

            component_rect = fitz.Rect(
                min(x_values),
                min(y_values),
                max(x_values),
                max(y_values),
            )

            loose_endpoints = []

            for key, count in endpoint_counts.items():
                if count != 1:
                    continue

                loose_endpoints.append(
                    (
                        key[0] * snap_distance,
                        key[1] * snap_distance,
                    )
                )

            components.append(
                {
                    "colour": colour_name,
                    "segments": component_segments,
                    "rect": component_rect,
                    "endpoints": loose_endpoints,
                }
            )

    return components


# ---------------------------------------------------------------------
# Terminal and item extraction
# ---------------------------------------------------------------------

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

        prefix = match.group("prefix").upper()
        column = match.group("column").upper()
        terminal_number = match.group("terminal")

        terminal_reference = (
            f"{prefix}.{column}{terminal_number}"
        )

        key = (
            terminal_reference,
            round(float(x0), 1),
            round(float(y0), 1),
        )

        if key in seen:
            continue

        seen.add(key)

        terminals.append(
            {
                "terminal": terminal_reference,
                "rect": fitz.Rect(x0, y0, x1, y1),
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

    return terminals


def extract_item_labels(
    page,
) -> list[dict]:
    page_text = page.get_text(
        "text",
        sort=True,
    )

    item_labels = []
    seen = set()

    for pattern in (
        ITEM_PATTERN,
        FALLBACK_ITEM_PATTERN,
    ):
        for match in pattern.finditer(page_text):
            item_name = (
                f"{match.group('item').upper()}"
                f"({match.group('suffix').upper()})"
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

                item_labels.append(
                    {
                        "item": item_name,
                        "rect": fitz.Rect(rectangle),
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
                    }
                )

        if item_labels:
            break

    return item_labels


# ---------------------------------------------------------------------
# Circuit-to-terminal / circuit-to-item association
# ---------------------------------------------------------------------

def terminal_is_connected_to_component(
    terminal: dict,
    component: dict,
    terminal_distance: float,
) -> bool:
    """
    A terminal is considered part of a coloured circuit if it is close to
    any individual vector segment in that circuit.

    Individual segment testing is important: the bounding rectangle of a
    large circuit can cover unrelated labels inside the rectangle.
    """
    terminal_rect = terminal["rect"]

    for segment in component["segments"]:
        segment_rect = segment["rect"]

        expanded_segment_rect = fitz.Rect(
            segment_rect.x0 - terminal_distance,
            segment_rect.y0 - terminal_distance,
            segment_rect.x1 + terminal_distance,
            segment_rect.y1 + terminal_distance,
        )

        if terminal_rect.intersects(
            expanded_segment_rect
        ):
            return True

        if rect_distance(
            terminal_rect,
            segment_rect,
        ) <= terminal_distance:
            return True

    return False


def get_component_terminals(
    page,
    component: dict,
    terminals: list[dict],
) -> list[dict]:
    terminal_distance = get_terminal_connection_distance(
        page
    )

    connected = []

    for terminal in terminals:
        if terminal_is_connected_to_component(
            terminal,
            component,
            terminal_distance,
        ):
            connected.append(terminal)

    return connected


def get_component_end_terminal_groups(
    page,
    component: dict,
    component_terminals: list[dict],
) -> list[list[dict]]:
    """
    Attempts to identify terminal groups at circuit ends.

    This does not inspect rack identifiers or assume left/middle/right
    cable rack geometry.

    For each physical loose end of the coloured vector path, nearby
    terminal references are collected. This is useful for populating
    Step 6 and Step 7 with the two ends of a coloured test circuit.

    If the PDF has no identifiable loose vector endpoints, callers can
    fall back to the complete Test Terminals list.
    """
    if not component_terminals:
        return []

    terminal_distance = get_terminal_connection_distance(
        page
    )

    endpoint_groups = []

    for endpoint in component["endpoints"]:
        nearby_terminals = []

        for terminal in component_terminals:
            terminal_center = (
                terminal["x_center"],
                terminal["y_center"],
            )

            if point_distance(
                endpoint,
                terminal_center,
            ) <= terminal_distance * 2:
                nearby_terminals.append(terminal)

        if nearby_terminals:
            endpoint_groups.append(
                sorted(
                    nearby_terminals,
                    key=lambda terminal: (
                        terminal["y_center"],
                        terminal["x_center"],
                    ),
                )
            )

    unique_groups = []
    seen = set()

    for group in endpoint_groups:
        key = tuple(
            terminal["terminal"]
            for terminal in group
        )

        if not key or key in seen:
            continue

        seen.add(key)
        unique_groups.append(group)

    return unique_groups


def find_nearest_item_for_component(
    page,
    component: dict,
    item_labels: list[dict],
) -> str:
    """
    Associate a coloured circuit with its nearest CT/TC item label.

    The score strongly prefers labels vertically aligned with the
    coloured circuit but still works for drawings where labels are placed
    above, below, left, or right of the circuit.
    """
    if not item_labels:
        return "REVIEW REQUIRED"

    item_distance = get_item_connection_distance(page)

    selected_item = "REVIEW REQUIRED"
    selected_score = None

    component_center_x = (
        component["rect"].x0
        + component["rect"].x1
    ) / 2

    component_center_y = (
        component["rect"].y0
        + component["rect"].y1
    ) / 2

    for item_label in item_labels:
        item_rect = item_label["rect"]

        direct_distance = rect_distance(
            component["rect"],
            item_rect,
        )

        horizontal_distance = abs(
            component_center_x
            - item_label["x_center"]
        )

        vertical_distance = abs(
            component_center_y
            - item_label["y_center"]
        )

        score = (
            direct_distance * 4
            + vertical_distance * 1.5
            + horizontal_distance * 0.35
        )

        if (
            selected_score is None
            or score < selected_score
        ):
            selected_score = score
            selected_item = item_label["item"]

    if selected_score is None:
        return "REVIEW REQUIRED"

    # Do not force an Item assignment when the closest label is clearly
    # unrelated to the coloured circuit.
    if selected_score > item_distance * 8:
        return "REVIEW REQUIRED"

    return selected_item


# ---------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------

def unique_terminals(
    terminals: list[dict],
) -> list[dict]:
    unique = []
    seen = set()

    for terminal in terminals:
        terminal_name = terminal["terminal"]

        if terminal_name in seen:
            continue

        seen.add(terminal_name)
        unique.append(terminal)

    return unique


def format_terminal_list(
    terminals: list[dict],
) -> str:
    terminals = unique_terminals(terminals)

    if not terminals:
        return ""

    return "\n".join(
        f"- {terminal['terminal']}"
        for terminal in terminals
    )


def create_circuit_id(
    component: dict,
    component_index: int,
) -> str:
    return (
        f"{component['colour']} "
        f"CIRCUIT {component_index:02d}"
    )


# ---------------------------------------------------------------------
# CT test row creation
# ---------------------------------------------------------------------

def create_ct_loop_rows(
    page,
    components: list[dict],
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> list[dict]:
    """
    Create one row per coloured circuit that has identified terminals.

    Important:
    - No cable-rack logic is used.
    - No left/middle/right lane logic is used.
    - No assumptions are made about V0, V1, V2, rack names, or drawing
      orientation.
    - Test circuits are selected only from red / blue / cyan PDF vector
      geometry.
    """
    rows = []

    for component_index, component in enumerate(
        components,
        start=1,
    ):
        component_terminals = get_component_terminals(
            page,
            component,
            terminals,
        )

        component_terminals = unique_terminals(
            component_terminals
        )

        if not component_terminals:
            write_log(
                log_callback,
                f"Circuit {component_index}: skipped; "
                "no terminal reference connected to "
                f"{component['colour']} geometry.",
                "DEBUG",
            )
            continue

        item_name = find_nearest_item_for_component(
            page,
            component,
            item_labels,
        )

        endpoint_groups = get_component_end_terminal_groups(
            page,
            component,
            component_terminals,
        )

        # G1 Step 6 and Step 7 are derived from the two physical ends of
        # the coloured circuit, if endpoints can be identified.
        #
        # The complete terminal list is always retained in Test Terminals
        # for engineering review and traceability.
        step_6_terminals = []
        step_7_terminals = []

        if len(endpoint_groups) >= 2:
            endpoint_groups.sort(
                key=lambda group: (
                    min(
                        terminal["x_center"]
                        for terminal in group
                    ),
                    min(
                        terminal["y_center"]
                        for terminal in group
                    ),
                )
            )

            step_6_terminals = endpoint_groups[0]
            step_7_terminals = endpoint_groups[-1]

        elif len(component_terminals) >= 2:
            # Fallback: choose the two furthest-apart terminal references.
            # This remains geometry-based and does not use rack identity.
            maximum_distance = -1.0
            selected_pair = None

            for first_index, first_terminal in enumerate(
                component_terminals
            ):
                for second_terminal in component_terminals[
                    first_index + 1:
                ]:
                    distance = point_distance(
                        (
                            first_terminal["x_center"],
                            first_terminal["y_center"],
                        ),
                        (
                            second_terminal["x_center"],
                            second_terminal["y_center"],
                        ),
                    )

                    if distance > maximum_distance:
                        maximum_distance = distance
                        selected_pair = (
                            first_terminal,
                            second_terminal,
                        )

            if selected_pair is not None:
                step_6_terminals = [selected_pair[0]]
                step_7_terminals = [selected_pair[1]]

        elif len(component_terminals) == 1:
            step_6_terminals = component_terminals

        review_required = (
            len(step_6_terminals) == 0
            or len(step_7_terminals) == 0
        )

        rows.append(
            {
                "Item": item_name,
                "Circuit ID": create_circuit_id(
                    component,
                    component_index,
                ),
                "Circuit Colour": component["colour"],
                "Test Terminals": format_terminal_list(
                    component_terminals
                ),
                "G1 - Step 6": format_terminal_list(
                    step_6_terminals
                ),
                "G1 - Step 7": format_terminal_list(
                    step_7_terminals
                ),
                "G1 - Step 14": format_terminal_list(
                    step_7_terminals
                ),
                "Review Status": (
                    "REVIEW REQUIRED"
                    if review_required
                    else "Draft"
                ),
                "Engineer Remarks": (
                    "Test candidate created from "
                    f"{component['colour']} circuit geometry. "
                    "No cable-rack or terminal-lane logic used. "
                    "Verify final test points against the "
                    "controlled drawing."
                ),
            }
        )

        write_log(
            log_callback,
            f"Circuit {component_index}: "
            f"colour={component['colour']}; "
            f"item={item_name}; "
            f"terminals={[terminal['terminal'] for terminal in component_terminals]}.",
            "DEBUG",
        )

    return rows


# ---------------------------------------------------------------------
# Page relevance
# ---------------------------------------------------------------------

def page_is_relevant(
    page_text: str,
    log_callback=None,
) -> bool:
    is_ct_loop_page = bool(
        CT_LOOP_PAGE_PATTERN.search(page_text)
    )

    terminal_matches = TERMINAL_PATTERN.findall(
        page_text
    )

    item_matches = (
        ITEM_PATTERN.findall(page_text)
        or FALLBACK_ITEM_PATTERN.findall(page_text)
    )

    write_log(
        log_callback,
        "Page relevance: "
        f"continuous_transmission={is_ct_loop_page}; "
        f"terminal_count={len(terminal_matches)}; "
        f"item_count={len(item_matches)}.",
        "DEBUG",
    )

    return (
        is_ct_loop_page
        and bool(terminal_matches)
    )


# ---------------------------------------------------------------------
# Debug image
# ---------------------------------------------------------------------

def draw_rectangle(
    page,
    rectangle: fitz.Rect,
    colour: tuple[float, float, float],
    width: float = 1.0,
) -> None:
    shape = page.new_shape()
    shape.draw_rect(rectangle)
    shape.finish(
        color=colour,
        width=width,
        stroke_opacity=1.0,
    )
    shape.commit()


def create_debug_image(
    page,
    components: list[dict],
    terminals: list[dict],
) -> bytes:
    """
    Debug colours:
      Green      = extracted RED vector circuit component.
      Purple     = extracted BLUE/CYAN vector circuit component.
      Orange     = terminal reference.
    """
    for component in components:
        if component["colour"] == "RED":
            component_colour = (0.0, 0.75, 0.0)
        else:
            component_colour = (0.55, 0.0, 0.85)

        for segment in component["segments"]:
            rect = fitz.Rect(segment["rect"])

            padded_rect = fitz.Rect(
                rect.x0 - 1.5,
                rect.y0 - 1.5,
                rect.x1 + 1.5,
                rect.y1 + 1.5,
            )

            draw_rectangle(
                page,
                padded_rect,
                component_colour,
                width=0.7,
            )

    for terminal in terminals:
        rect = terminal["rect"]

        padded_rect = fitz.Rect(
            rect.x0 - 2.0,
            rect.y0 - 2.0,
            rect.x1 + 2.0,
            rect.y1 + 2.0,
        )

        draw_rectangle(
            page,
            padded_rect,
            (1.0, 0.55, 0.0),
            width=1.1,
        )

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
    """
    Extract Continuous Transmission CT-loop test candidates.

    Selection logic:
      1. Find pages containing "CONTINUOUS TRANSMISSI".
      2. Extract red and blue/cyan vector circuit wires.
      3. Connect wire segments into coloured physical circuit components.
      4. Find terminal references physically close to each circuit.
      5. Create test rows only for coloured circuits with terminals.

    No cable-rack, lane, rack-ID, V0/V1/V2, left/right, or fixed drawing
    layout logic is used.
    """
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
                    page_text,
                    log_callback=log_callback,
                ):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "not a Continuous Transmission page "
                        "or no terminal references found.",
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

                coloured_segments = extract_coloured_segments(
                    page,
                    log_callback=log_callback,
                )

                if not coloured_segments:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no red or blue/cyan vector circuits found.",
                        "DEBUG",
                    )
                    continue

                components = build_coloured_circuit_components(
                    page,
                    coloured_segments,
                )

                terminals = extract_terminal_references(page)
                item_labels = extract_item_labels(page)

                if not terminals:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no terminal references found.",
                        "WARNING",
                    )
                    continue

                write_log(
                    log_callback,
                    f"Page {page_number}: "
                    f"drawing={drawing_number}; "
                    f"revision={drawing_revision}; "
                    f"location={location or 'NOT FOUND'}; "
                    f"coloured_components={len(components)}; "
                    f"terminals={len(terminals)}; "
                    f"items={len(item_labels)}.",
                    "DEBUG",
                )

                ct_loop_rows = create_ct_loop_rows(
                    page,
                    components,
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                )

                debug_images[page_number] = (
                    create_debug_image(
                        page,
                        components,
                        terminals,
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
                            "Circuit ID": row["Circuit ID"],
                            "Circuit Colour": (
                                row["Circuit Colour"]
                            ),
                            "Test Terminals": (
                                row["Test Terminals"]
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
                "Circuit ID",
                "Circuit Colour",
                "Test Terminals",
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
                "Item",
                "Circuit ID",
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
