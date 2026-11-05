from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

import fitz
import pandas as pd


LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[float], None]
Point = tuple[float, float]


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

TERMINAL_PATTERN = re.compile(
    r"\b"
    r"(?P<prefix>[A-Z]{1,6}\d{0,4})"
    r"\s*[:.]\s*"
    r"(?P<column>[A-Z]{1,6})"
    r"(?P<terminal>\d+(?:[.-]\d+)*)"
    r"\b",
    re.IGNORECASE,
)

ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"(?P<item>[A-Z][A-Z0-9-]*)\s*"
    r"\(\s*(?P<suffix>[A-Z0-9-]+)\s*\)",
    re.IGNORECASE,
)

FALLBACK_ITEM_PATTERN = re.compile(
    r"\b"
    r"(?P<item>[A-Z]{2,}[A-Z0-9-]*)"
    r"\s*"
    r"\(\s*(?P<suffix>[A-Z0-9-]+)\s*\)",
    re.IGNORECASE,
)

CT_LOOP_PAGE_PATTERN = re.compile(
    r"CONTINUOUS\s+TRANSMISSI",
    re.IGNORECASE,
)

REVISION_VALUE_PATTERN = re.compile(
    r"(?:[A-Z]{1,3}|\d{1,3}|[A-Z]{1,3}[-.]?\d{1,3})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Colour and geometry settings
# ---------------------------------------------------------------------

RED_MIN = 0.45
RED_DOMINANCE = 0.16
BLUE_MIN = 0.40
BLUE_DOMINANCE = 0.10
CYAN_MIN = 0.45
CYAN_RED_MAX = 0.45

MIN_SNAP_DISTANCE = 3.0
MAX_SNAP_DISTANCE = 12.0
MIN_TERMINAL_DISTANCE = 8.0
MAX_TERMINAL_DISTANCE = 30.0
MIN_ITEM_DISTANCE = 15.0
MAX_ITEM_DISTANCE = 110.0

MIN_SEGMENT_LENGTH = 0.50
BEZIER_SUBDIVISIONS = 12
MAX_WORD_WINDOW = 5


# ---------------------------------------------------------------------
# Logging and generic helpers
# ---------------------------------------------------------------------

def write_log(
    log_callback: LogCallback | None,
    message: str,
    level: str = "INFO",
) -> None:
    if log_callback is not None:
        log_callback(message, level)


def normalise_drawing_number(drawing_number: str) -> str:
    return drawing_number.upper().strip()


def get_location_from_drawing_number(drawing_number: str) -> str:
    parts = drawing_number.upper().split("/")
    for index, part in enumerate(parts):
        if re.fullmatch(r"R\d+", part) and index >= 2:
            return parts[index - 2]
    return ""


def point_distance(first: Point, second: Point) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def rect_distance(first: fitz.Rect, second: fitz.Rect) -> float:
    horizontal_gap = max(first.x0 - second.x1, second.x0 - first.x1, 0)
    vertical_gap = max(first.y0 - second.y1, second.y0 - first.y1, 0)
    return math.hypot(horizontal_gap, vertical_gap)


def make_rect_from_points(
    first: Point,
    second: Point,
    padding: float = 0.0,
) -> fitz.Rect:
    return fitz.Rect(
        min(first[0], second[0]) - padding,
        min(first[1], second[1]) - padding,
        max(first[0], second[0]) + padding,
        max(first[1], second[1]) + padding,
    )


def expand_rect(rectangle: fitz.Rect, amount: float) -> fitz.Rect:
    return fitz.Rect(
        rectangle.x0 - amount,
        rectangle.y0 - amount,
        rectangle.x1 + amount,
        rectangle.y1 + amount,
    )


def union_rectangles(rectangles: Iterable[fitz.Rect]) -> fitz.Rect:
    rectangles = list(rectangles)
    if not rectangles:
        return fitz.Rect()
    result = fitz.Rect(rectangles[0])
    for rectangle in rectangles[1:]:
        result.include_rect(rectangle)
    return result


def get_snap_distance(page: fitz.Page) -> float:
    return max(
        MIN_SNAP_DISTANCE,
        min(float(page.rect.width) * 0.004, MAX_SNAP_DISTANCE),
    )


def get_terminal_connection_distance(page: fitz.Page) -> float:
    return max(
        MIN_TERMINAL_DISTANCE,
        min(float(page.rect.width) * 0.020, MAX_TERMINAL_DISTANCE),
    )


def get_item_connection_distance(page: fitz.Page) -> float:
    return max(
        MIN_ITEM_DISTANCE,
        min(float(page.rect.width) * 0.075, MAX_ITEM_DISTANCE),
    )


def point_tuple(point: Any) -> Point:
    return float(point.x), float(point.y)


def point_to_segment_distance(point: Point, start: Point, end: Point) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-12:
        return point_distance(point, start)
    projection = ((px - x1) * dx + (py - y1) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    nearest = x1 + projection * dx, y1 + projection * dy
    return point_distance(point, nearest)


def orientation(first: Point, second: Point, third: Point) -> float:
    return (
        (second[0] - first[0]) * (third[1] - first[1])
        - (second[1] - first[1]) * (third[0] - first[0])
    )


def segment_intersection_kind(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
    tolerance: float,
) -> str:
    """Return 'endpoint', 'crossing', or '' for two straight segments."""
    for first_point in (first_start, first_end):
        if point_to_segment_distance(first_point, second_start, second_end) <= tolerance:
            return "endpoint"
    for second_point in (second_start, second_end):
        if point_to_segment_distance(second_point, first_start, first_end) <= tolerance:
            return "endpoint"

    o1 = orientation(first_start, first_end, second_start)
    o2 = orientation(first_start, first_end, second_end)
    o3 = orientation(second_start, second_end, first_start)
    o4 = orientation(second_start, second_end, first_end)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return "crossing"
    return ""


def distance_point_to_rect(point: Point, rectangle: fitz.Rect) -> float:
    x = min(max(point[0], rectangle.x0), rectangle.x1)
    y = min(max(point[1], rectangle.y0), rectangle.y1)
    return point_distance(point, (x, y))


def segment_to_rect_distance(start: Point, end: Point, rectangle: fitz.Rect) -> float:
    if rectangle.contains(fitz.Point(*start)) or rectangle.contains(fitz.Point(*end)):
        return 0.0

    corners = [
        (rectangle.x0, rectangle.y0),
        (rectangle.x1, rectangle.y0),
        (rectangle.x1, rectangle.y1),
        (rectangle.x0, rectangle.y1),
    ]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    for edge_start, edge_end in edges:
        if segment_intersection_kind(start, end, edge_start, edge_end, 1e-6):
            return 0.0

    distances = [
        distance_point_to_rect(start, rectangle),
        distance_point_to_rect(end, rectangle),
    ]
    distances.extend(
        point_to_segment_distance(corner, start, end)
        for corner in corners
    )
    return min(distances)


# ---------------------------------------------------------------------
# Title block metadata
# ---------------------------------------------------------------------

def get_bottom_title_block_words(
    page: fitz.Page,
    bottom_ratio: float = 0.18,
) -> list[dict]:
    title_block_top = float(page.rect.height) * (1 - bottom_ratio)
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
                "x_center": round((x0 + x1) / 2, 2),
                "y_center": round((y0 + y1) / 2, 2),
            }
        )
    return words


def get_drawing_number_from_title_block(title_block_words: list[dict]) -> str:
    title_block_text = " ".join(word["value"] for word in title_block_words)
    matches = list(DRAWING_PATTERN.finditer(title_block_text))
    return matches[-1].group(0).upper() if matches else ""


def is_revision_value(value: str) -> bool:
    cleaned = value.strip(" ,;:()[]{}").upper()
    return bool(REVISION_VALUE_PATTERN.fullmatch(cleaned))


def get_revision_from_title_block(title_block_words: list[dict]) -> str:
    revision_labels = [
        word
        for word in title_block_words
        if re.fullmatch(r"REV\.?", word["value"].upper())
        or word["value"].upper() == "REVISION"
    ]
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
            horizontal_distance = word["x0"] - revision_label["x1"]
            vertical_distance = abs(word["y_center"] - revision_label["y_center"])
            if vertical_distance > 20:
                continue
            candidates.append(
                {
                    "value": value,
                    "score": horizontal_distance + vertical_distance * 5,
                }
            )
    if not candidates:
        return "REVIEW"
    candidates.sort(key=lambda candidate: candidate["score"])
    return candidates[0]["value"]


def get_title_block_metadata(
    page: fitz.Page,
    log_callback: LogCallback | None = None,
) -> tuple[str, str]:
    title_block_words = get_bottom_title_block_words(page)
    drawing_number = get_drawing_number_from_title_block(title_block_words)
    drawing_revision = get_revision_from_title_block(title_block_words)
    write_log(
        log_callback,
        "Title-block extraction: "
        f"drawing={drawing_number or 'NOT FOUND'}; revision={drawing_revision}.",
        "DEBUG",
    )
    return drawing_number, drawing_revision


# ---------------------------------------------------------------------
# Colour detection
# ---------------------------------------------------------------------

def colour_is_red(colour: Any) -> bool:
    if not colour or len(colour) < 3:
        return False
    red, green, blue = colour[:3]
    return (
        red >= RED_MIN
        and red > green + RED_DOMINANCE
        and red > blue + RED_DOMINANCE
    )


def colour_is_blue_or_cyan(colour: Any) -> bool:
    if not colour or len(colour) < 3:
        return False
    red, green, blue = colour[:3]
    is_blue = (
        blue >= BLUE_MIN
        and blue > red + BLUE_DOMINANCE
        and blue > green * 0.60
    )
    is_cyan = green >= CYAN_MIN and blue >= CYAN_MIN and red <= CYAN_RED_MAX
    return is_blue or is_cyan


def get_colour_name(colour: Any) -> str:
    if colour_is_red(colour):
        return "RED"
    if colour_is_blue_or_cyan(colour):
        return "BLUE/CYAN"
    return ""


# ---------------------------------------------------------------------
# Vector segment extraction
# ---------------------------------------------------------------------

def append_segment(segments: list[dict], start: Point, end: Point) -> None:
    if point_distance(start, end) < MIN_SEGMENT_LENGTH:
        return
    segments.append(
        {
            "start": start,
            "end": end,
            "rect": make_rect_from_points(start, end),
        }
    )


def cubic_bezier_point(
    start: Point,
    control_1: Point,
    control_2: Point,
    end: Point,
    t: float,
) -> Point:
    one_minus_t = 1.0 - t
    return (
        one_minus_t**3 * start[0]
        + 3 * one_minus_t**2 * t * control_1[0]
        + 3 * one_minus_t * t**2 * control_2[0]
        + t**3 * end[0],
        one_minus_t**3 * start[1]
        + 3 * one_minus_t**2 * t * control_1[1]
        + 3 * one_minus_t * t**2 * control_2[1]
        + t**3 * end[1],
    )


def quadratic_bezier_point(
    start: Point,
    control: Point,
    end: Point,
    t: float,
) -> Point:
    one_minus_t = 1.0 - t
    return (
        one_minus_t**2 * start[0]
        + 2 * one_minus_t * t * control[0]
        + t**2 * end[0],
        one_minus_t**2 * start[1]
        + 2 * one_minus_t * t * control[1]
        + t**2 * end[1],
    )


def get_drawing_line_segments(drawing: dict) -> list[dict]:
    segments: list[dict] = []
    for item in drawing.get("items", []):
        if not item:
            continue
        operation = item[0]
        if operation == "l" and len(item) >= 3:
            append_segment(segments, point_tuple(item[1]), point_tuple(item[2]))
        elif operation == "c" and len(item) >= 5:
            start = point_tuple(item[1])
            control_1 = point_tuple(item[2])
            control_2 = point_tuple(item[3])
            end = point_tuple(item[4])
            points = [
                cubic_bezier_point(start, control_1, control_2, end, index / BEZIER_SUBDIVISIONS)
                for index in range(BEZIER_SUBDIVISIONS + 1)
            ]
            for first, second in zip(points, points[1:]):
                append_segment(segments, first, second)
        elif operation == "qu" and len(item) >= 4:
            start = point_tuple(item[1])
            control = point_tuple(item[2])
            end = point_tuple(item[3])
            points = [
                quadratic_bezier_point(start, control, end, index / BEZIER_SUBDIVISIONS)
                for index in range(BEZIER_SUBDIVISIONS + 1)
            ]
            for first, second in zip(points, points[1:]):
                append_segment(segments, first, second)
    return segments


def extract_coloured_segments(
    page: fitz.Page,
    log_callback: LogCallback | None = None,
) -> list[dict]:
    segments = []
    for drawing in page.get_drawings():
        colour_name = get_colour_name(drawing.get("color"))
        if not colour_name:
            continue
        for segment in get_drawing_line_segments(drawing):
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
# Circuit graph construction
# ---------------------------------------------------------------------

class DisjointSet:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))
        self.rank = [0] * count

    def find(self, value: int) -> int:
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, first: int, second: int) -> None:
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


def endpoint_grid_key(point: Point, grid_size: float) -> tuple[int, int]:
    return int(point[0] // grid_size), int(point[1] // grid_size)


def nearby_grid_keys(
    key: tuple[int, int],
    radius: int = 1,
) -> list[tuple[int, int]]:
    return [
        (key[0] + x_offset, key[1] + y_offset)
        for x_offset in range(-radius, radius + 1)
        for y_offset in range(-radius, radius + 1)
    ]


def segment_grid_keys(segment: dict, grid_size: float) -> list[tuple[int, int]]:
    rectangle = segment["rect"]
    x_min = int(rectangle.x0 // grid_size)
    x_max = int(rectangle.x1 // grid_size)
    y_min = int(rectangle.y0 // grid_size)
    y_max = int(rectangle.y1 // grid_size)
    return [
        (x_index, y_index)
        for x_index in range(x_min, x_max + 1)
        for y_index in range(y_min, y_max + 1)
    ]


def cluster_component_endpoints(
    component_segments: list[dict],
    snap_distance: float,
) -> list[dict]:
    endpoints: list[Point] = []
    for segment in component_segments:
        endpoints.extend([segment["start"], segment["end"]])

    endpoint_set = DisjointSet(len(endpoints))
    endpoint_index: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    for index, point in enumerate(endpoints):
        key = endpoint_grid_key(point, snap_distance)
        for nearby_key in nearby_grid_keys(key):
            for existing_index in endpoint_index[nearby_key]:
                if point_distance(point, endpoints[existing_index]) <= snap_distance:
                    endpoint_set.union(index, existing_index)
        endpoint_index[key].append(index)

    grouped: defaultdict[int, list[Point]] = defaultdict(list)
    for index, point in enumerate(endpoints):
        grouped[endpoint_set.find(index)].append(point)

    clusters = []
    for points in grouped.values():
        clusters.append(
            {
                "point": (
                    sum(point[0] for point in points) / len(points),
                    sum(point[1] for point in points) / len(points),
                ),
                "incidence": len(points),
            }
        )
    return clusters


def build_coloured_circuit_components(
    page: fitz.Page,
    segments: list[dict],
) -> list[dict]:
    if not segments:
        return []

    snap_distance = get_snap_distance(page)
    components = []

    for colour_name in ("RED", "BLUE/CYAN"):
        colour_segments = [
            segment for segment in segments if segment["colour"] == colour_name
        ]
        if not colour_segments:
            continue

        disjoint_set = DisjointSet(len(colour_segments))
        segment_index: defaultdict[tuple[int, int], list[int]] = defaultdict(list)

        for index, segment in enumerate(colour_segments):
            candidate_indices: set[int] = set()
            search_rect = expand_rect(segment["rect"], snap_distance)
            search_segment = {"rect": search_rect}
            for grid_key in segment_grid_keys(search_segment, snap_distance * 2.0):
                candidate_indices.update(segment_index.get(grid_key, ()))

            for existing_index in candidate_indices:
                existing = colour_segments[existing_index]
                kind = segment_intersection_kind(
                    segment["start"],
                    segment["end"],
                    existing["start"],
                    existing["end"],
                    snap_distance,
                )
                # Endpoint contact and endpoint-to-segment contact are connected.
                # Pure interior crossings are deliberately not connected because
                # electrical drawings may show unjoined wire crossings.
                if kind == "endpoint":
                    disjoint_set.union(index, existing_index)

            for grid_key in segment_grid_keys(segment, snap_distance * 2.0):
                segment_index[grid_key].append(index)

        grouped_segments: defaultdict[int, list[dict]] = defaultdict(list)
        for index, segment in enumerate(colour_segments):
            grouped_segments[disjoint_set.find(index)].append(segment)

        for component_segments in grouped_segments.values():
            all_points = [
                point
                for segment in component_segments
                for point in (segment["start"], segment["end"])
            ]
            component_rect = fitz.Rect(
                min(point[0] for point in all_points),
                min(point[1] for point in all_points),
                max(point[0] for point in all_points),
                max(point[1] for point in all_points),
            )
            endpoint_clusters = cluster_component_endpoints(
                component_segments,
                snap_distance,
            )
            loose_endpoints = [
                cluster["point"]
                for cluster in endpoint_clusters
                if cluster["incidence"] == 1
            ]
            components.append(
                {
                    "colour": colour_name,
                    "segments": component_segments,
                    "rect": component_rect,
                    "endpoints": loose_endpoints,
                }
            )

    components.sort(
        key=lambda component: (
            component["colour"],
            round(component["rect"].y0, 1),
            round(component["rect"].x0, 1),
            round(component["rect"].y1, 1),
            round(component["rect"].x1, 1),
        )
    )
    return components


# ---------------------------------------------------------------------
# Word-based terminal and item extraction
# ---------------------------------------------------------------------

def get_structured_words(page: fitz.Page) -> list[dict]:
    words = []
    for raw_word in page.get_text("words", sort=True):
        x0, y0, x1, y1, text, block, line, word_number = raw_word[:8]
        words.append(
            {
                "text": str(text),
                "rect": fitz.Rect(x0, y0, x1, y1),
                "block": int(block),
                "line": int(line),
                "word_number": int(word_number),
            }
        )
    return words


def contiguous_word_windows(
    words: list[dict],
    maximum_size: int = MAX_WORD_WINDOW,
) -> Iterable[list[dict]]:
    grouped: defaultdict[tuple[int, int], list[dict]] = defaultdict(list)
    for word in words:
        grouped[(word["block"], word["line"])].append(word)

    for line_words in grouped.values():
        line_words.sort(key=lambda word: word["word_number"])
        for start_index in range(len(line_words)):
            for size in range(1, min(maximum_size, len(line_words) - start_index) + 1):
                yield line_words[start_index : start_index + size]


def join_word_window(window: list[dict]) -> tuple[str, fitz.Rect]:
    text = "".join(word["text"] for word in window)
    rectangle = union_rectangles(word["rect"] for word in window)
    return text, rectangle


def extract_terminal_references(page: fitz.Page) -> list[dict]:
    terminals = []
    seen = set()
    words = get_structured_words(page)

    for window in contiguous_word_windows(words):
        candidate_text, rectangle = join_word_window(window)
        cleaned_value = candidate_text.strip(" ,;()[]{}")
        match = TERMINAL_PATTERN.fullmatch(cleaned_value)
        if not match:
            continue
        prefix = match.group("prefix").upper()
        column = match.group("column").upper()
        terminal_number = match.group("terminal")
        terminal_reference = f"{prefix}.{column}{terminal_number}"
        key = (
            terminal_reference,
            round(rectangle.x0, 1),
            round(rectangle.y0, 1),
        )
        if key in seen:
            continue
        seen.add(key)
        terminals.append(
            {
                "terminal": terminal_reference,
                "rect": rectangle,
                "x_center": round((rectangle.x0 + rectangle.x1) / 2, 2),
                "y_center": round((rectangle.y0 + rectangle.y1) / 2, 2),
            }
        )
    return terminals


def extract_item_labels(page: fitz.Page) -> list[dict]:
    words = get_structured_words(page)
    primary_labels = []
    fallback_labels = []
    primary_seen = set()
    fallback_seen = set()

    for window in contiguous_word_windows(words):
        candidate_text, rectangle = join_word_window(window)
        candidate_text = candidate_text.strip(" ,;")

        primary_match = ITEM_PATTERN.fullmatch(candidate_text)
        fallback_match = None if primary_match else FALLBACK_ITEM_PATTERN.fullmatch(candidate_text)
        match = primary_match or fallback_match
        if not match:
            continue

        source = "PRIMARY" if primary_match else "FALLBACK"
        item_name = f"{match.group('item').upper()}({match.group('suffix').upper()})"
        key = (item_name, round(rectangle.x0, 1), round(rectangle.y0, 1))
        seen = primary_seen if primary_match else fallback_seen
        if key in seen:
            continue
        seen.add(key)

        label = {
            "item": item_name,
            "rect": rectangle,
            "x_center": round((rectangle.x0 + rectangle.x1) / 2, 2),
            "y_center": round((rectangle.y0 + rectangle.y1) / 2, 2),
            "source": source,
        }
        if primary_match:
            primary_labels.append(label)
        else:
            fallback_labels.append(label)

    return primary_labels if primary_labels else fallback_labels


# ---------------------------------------------------------------------
# Spatial index helpers
# ---------------------------------------------------------------------

class RectGridIndex:
    """A lightweight uniform-grid spatial index for PDF geometry."""

    def __init__(self, cell_size: float) -> None:
        self.cell_size = max(float(cell_size), 1.0)
        self.cells: defaultdict[tuple[int, int], list[int]] = defaultdict(list)

    def _keys(self, rectangle: fitz.Rect) -> Iterable[tuple[int, int]]:
        x_min = int(math.floor(rectangle.x0 / self.cell_size))
        x_max = int(math.floor(rectangle.x1 / self.cell_size))
        y_min = int(math.floor(rectangle.y0 / self.cell_size))
        y_max = int(math.floor(rectangle.y1 / self.cell_size))
        for x_index in range(x_min, x_max + 1):
            for y_index in range(y_min, y_max + 1):
                yield x_index, y_index

    def add(self, index: int, rectangle: fitz.Rect) -> None:
        for key in self._keys(rectangle):
            self.cells[key].append(index)

    def query(self, rectangle: fitz.Rect) -> set[int]:
        result: set[int] = set()
        for key in self._keys(rectangle):
            result.update(self.cells.get(key, ()))
        return result


def build_terminal_grid_index(
    page: fitz.Page,
    terminals: list[dict],
) -> tuple[RectGridIndex, float]:
    terminal_distance = get_terminal_connection_distance(page)
    index = RectGridIndex(max(terminal_distance * 2.0, 24.0))
    for terminal_index, terminal in enumerate(terminals):
        index.add(terminal_index, terminal["rect"])
    return index, terminal_distance


def get_component_terminals_indexed(
    component: dict,
    terminals: list[dict],
    terminal_index: RectGridIndex,
    terminal_distance: float,
) -> list[dict]:
    candidate_indices: set[int] = set()
    for segment in component["segments"]:
        candidate_indices.update(
            terminal_index.query(expand_rect(segment["rect"], terminal_distance))
        )

    connected = []
    for index in sorted(candidate_indices):
        terminal = terminals[index]
        if terminal_is_connected_to_component(
            terminal,
            component,
            terminal_distance,
        ):
            connected.append(terminal)
    return connected

# ---------------------------------------------------------------------
# Component association
# ---------------------------------------------------------------------

def terminal_is_connected_to_component(
    terminal: dict,
    component: dict,
    terminal_distance: float,
) -> bool:
    terminal_rect = terminal["rect"]
    for segment in component["segments"]:
        if segment_to_rect_distance(
            segment["start"],
            segment["end"],
            terminal_rect,
        ) <= terminal_distance:
            return True
    return False


def get_component_terminals(
    page: fitz.Page,
    component: dict,
    terminals: list[dict],
) -> list[dict]:
    terminal_distance = get_terminal_connection_distance(page)
    return [
        terminal
        for terminal in terminals
        if terminal_is_connected_to_component(
            terminal,
            component,
            terminal_distance,
        )
    ]


def get_component_end_terminal_groups(
    page: fitz.Page,
    component: dict,
    component_terminals: list[dict],
) -> list[list[dict]]:
    if not component_terminals:
        return []

    terminal_distance = get_terminal_connection_distance(page)
    endpoint_groups = []
    for endpoint in component["endpoints"]:
        nearby_terminals = [
            terminal
            for terminal in component_terminals
            if distance_point_to_rect(endpoint, terminal["rect"])
            <= terminal_distance * 2
        ]
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
        key = tuple(terminal["terminal"] for terminal in group)
        if key and key not in seen:
            seen.add(key)
            unique_groups.append(group)
    return unique_groups


def find_nearest_item_for_component(
    page: fitz.Page,
    component: dict,
    item_labels: list[dict],
) -> str:
    if not item_labels:
        return "REVIEW REQUIRED"

    item_distance = get_item_connection_distance(page)
    selected_item = "REVIEW REQUIRED"
    selected_score: float | None = None
    component_center_x = (component["rect"].x0 + component["rect"].x1) / 2
    component_center_y = (component["rect"].y0 + component["rect"].y1) / 2

    for item_label in item_labels:
        direct_distance = rect_distance(component["rect"], item_label["rect"])
        horizontal_distance = abs(component_center_x - item_label["x_center"])
        vertical_distance = abs(component_center_y - item_label["y_center"])
        fallback_penalty = item_distance if item_label.get("source") == "FALLBACK" else 0.0
        score = (
            direct_distance * 4
            + vertical_distance * 1.5
            + horizontal_distance * 0.35
            + fallback_penalty
        )
        if selected_score is None or score < selected_score:
            selected_score = score
            selected_item = item_label["item"]

    if selected_score is None or selected_score > item_distance * 8:
        return "REVIEW REQUIRED"
    return selected_item


# ---------------------------------------------------------------------
# Output formatting and row creation
# ---------------------------------------------------------------------

def unique_terminals(terminals: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for terminal in terminals:
        terminal_name = terminal["terminal"]
        if terminal_name in seen:
            continue
        seen.add(terminal_name)
        unique.append(terminal)
    return unique


def format_terminal_list(terminals: list[dict]) -> str:
    terminals = unique_terminals(terminals)
    return "\n".join(f"- {terminal['terminal']}" for terminal in terminals)


def create_circuit_id(component: dict, component_index: int) -> str:
    return f"{component['colour']} CIRCUIT {component_index:02d}"


def create_ct_loop_rows(
    page: fitz.Page,
    components: list[dict],
    terminals: list[dict],
    item_labels: list[dict],
    log_callback: LogCallback | None = None,
    terminal_index: RectGridIndex | None = None,
    terminal_distance: float | None = None,
) -> list[dict]:
    rows = []
    if terminal_index is None or terminal_distance is None:
        terminal_index, terminal_distance = build_terminal_grid_index(page, terminals)
    for component_index, component in enumerate(components, start=1):
        component_terminals = unique_terminals(
            get_component_terminals_indexed(
                component,
                terminals,
                terminal_index,
                terminal_distance,
            )
        )
        if not component_terminals:
            write_log(
                log_callback,
                f"Circuit {component_index}: skipped; no terminal reference "
                f"connected to {component['colour']} geometry.",
                "DEBUG",
            )
            continue

        item_name = find_nearest_item_for_component(page, component, item_labels)
        endpoint_groups = get_component_end_terminal_groups(
            page,
            component,
            component_terminals,
        )

        step_6_terminals: list[dict] = []
        step_7_terminals: list[dict] = []
        used_furthest_pair_fallback = False

        if len(endpoint_groups) >= 2:
            endpoint_groups.sort(
                key=lambda group: (
                    min(terminal["x_center"] for terminal in group),
                    min(terminal["y_center"] for terminal in group),
                )
            )
            step_6_terminals = endpoint_groups[0]
            step_7_terminals = endpoint_groups[-1]
        elif len(component_terminals) >= 2:
            used_furthest_pair_fallback = True
            maximum_distance = -1.0
            selected_pair = None
            for first_index, first_terminal in enumerate(component_terminals):
                for second_terminal in component_terminals[first_index + 1 :]:
                    distance = point_distance(
                        (first_terminal["x_center"], first_terminal["y_center"]),
                        (second_terminal["x_center"], second_terminal["y_center"]),
                    )
                    if distance > maximum_distance:
                        maximum_distance = distance
                        selected_pair = first_terminal, second_terminal
            if selected_pair is not None:
                step_6_terminals = [selected_pair[0]]
                step_7_terminals = [selected_pair[1]]
        elif len(component_terminals) == 1:
            step_6_terminals = component_terminals

        review_reasons = []
        if not step_6_terminals:
            review_reasons.append("Step 6 terminal not identified")
        if not step_7_terminals:
            review_reasons.append("Step 7 terminal not identified")
        if item_name == "REVIEW REQUIRED":
            review_reasons.append("item label not confidently associated")
        if used_furthest_pair_fallback:
            review_reasons.append("terminal ends selected by furthest-pair fallback")
        if len(endpoint_groups) > 2:
            review_reasons.append("more than two terminal-bearing endpoints found")

        review_required = bool(review_reasons)
        remarks = (
            f"Test candidate created from {component['colour']} circuit geometry. "
            "No cable-rack or terminal-lane logic used. "
            "Verify final test points against the controlled drawing."
        )
        if review_reasons:
            remarks += " Review reasons: " + "; ".join(review_reasons) + "."

        rows.append(
            {
                "Item": item_name,
                "Circuit ID": create_circuit_id(component, component_index),
                "Circuit Colour": component["colour"],
                "Test Terminals": format_terminal_list(component_terminals),
                "G1 - Step 6": format_terminal_list(step_6_terminals),
                "G1 - Step 7": format_terminal_list(step_7_terminals),
                # Confirm this mapping against the controlled G1 procedure.
                "G1 - Step 14": format_terminal_list(step_7_terminals),
                "Review Status": "REVIEW REQUIRED" if review_required else "Draft",
                "Engineer Remarks": remarks,
            }
        )
        write_log(
            log_callback,
            f"Circuit {component_index}: colour={component['colour']}; "
            f"item={item_name}; terminals="
            f"{[terminal['terminal'] for terminal in component_terminals]}.",
            "DEBUG",
        )
    return rows


# ---------------------------------------------------------------------
# Page relevance and debug rendering
# ---------------------------------------------------------------------

def page_is_relevant(
    page_text: str,
    log_callback: LogCallback | None = None,
) -> bool:
    is_ct_loop_page = bool(CT_LOOP_PAGE_PATTERN.search(page_text))
    terminal_matches = TERMINAL_PATTERN.findall(page_text)
    item_matches = ITEM_PATTERN.findall(page_text) or FALLBACK_ITEM_PATTERN.findall(page_text)
    write_log(
        log_callback,
        "Page relevance: "
        f"continuous_transmission={is_ct_loop_page}; "
        f"terminal_count={len(terminal_matches)}; item_count={len(item_matches)}.",
        "DEBUG",
    )
    # Split PDF words may not appear in page_text as one reference, so the
    # definitive terminal check happens after structured word extraction.
    return is_ct_loop_page


def draw_rectangle_on_page(
    page: fitz.Page,
    rectangle: fitz.Rect,
    colour: tuple[float, float, float],
    width: float = 1.0,
) -> None:
    shape = page.new_shape()
    shape.draw_rect(rectangle)
    shape.finish(color=colour, width=width, stroke_opacity=1.0)
    shape.commit()


def create_debug_image_from_document(
    pdf_path: str | Path,
    page_index: int,
    components: list[dict],
    terminals: list[dict],
) -> bytes:
    # Open a separate document so annotations never modify the extraction page.
    with fitz.open(pdf_path) as debug_document:
        page = debug_document[page_index]
        for component in components:
            component_colour = (
                (0.0, 0.75, 0.0)
                if component["colour"] == "RED"
                else (0.55, 0.0, 0.85)
            )
            for segment in component["segments"]:
                draw_rectangle_on_page(
                    page,
                    expand_rect(segment["rect"], 1.5),
                    component_colour,
                    width=0.7,
                )
        for terminal in terminals:
            draw_rectangle_on_page(
                page,
                expand_rect(terminal["rect"], 2.0),
                (1.0, 0.55, 0.0),
                width=1.1,
            )
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return pixmap.tobytes("png")


# ---------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------

def extract_pdf(
    pdf_path: str | Path,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    create_debug_images: bool = False,
) -> tuple[pd.DataFrame, dict[int, bytes]]:
    start_time = time.perf_counter()
    pdf_path = Path(pdf_path)
    records = []
    debug_images: dict[int, bytes] = {}

    write_log(log_callback, f"Opening PDF: {pdf_path}")
    with fitz.open(pdf_path) as document:
        total_pages = len(document)
        write_log(
            log_callback,
            f"PDF opened successfully. Total pages: {total_pages}.",
        )

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_start_time = time.perf_counter()
            if progress_callback is not None:
                progress_callback((page_index / max(total_pages, 1)) * 100)

            try:
                page_text = page.get_text("text", sort=True)
                if not page_is_relevant(page_text, log_callback=log_callback):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; not a Continuous Transmission page.",
                        "DEBUG",
                    )
                    continue

                drawing_number, drawing_revision = get_title_block_metadata(
                    page,
                    log_callback=log_callback,
                )
                if not drawing_number:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; Drawing Number missing.",
                        "WARNING",
                    )
                    continue

                drawing_number = normalise_drawing_number(drawing_number)
                drawing_revision = drawing_revision.upper().strip()
                location = get_location_from_drawing_number(drawing_number)

                coloured_segments = extract_coloured_segments(
                    page,
                    log_callback=log_callback,
                )
                if not coloured_segments:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; no red or blue/cyan "
                        "vector circuits found.",
                        "DEBUG",
                    )
                    continue

                components = build_coloured_circuit_components(page, coloured_segments)
                terminals = extract_terminal_references(page)
                item_labels = extract_item_labels(page)
                if not terminals:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; no terminal references found.",
                        "WARNING",
                    )
                    continue

                write_log(
                    log_callback,
                    f"Page {page_number}: drawing={drawing_number}; "
                    f"revision={drawing_revision}; location={location or 'NOT FOUND'}; "
                    f"coloured_components={len(components)}; "
                    f"terminals={len(terminals)}; items={len(item_labels)}.",
                    "DEBUG",
                )

                terminal_index, terminal_distance = build_terminal_grid_index(
                    page,
                    terminals,
                )
                ct_loop_rows = create_ct_loop_rows(
                    page,
                    components,
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                    terminal_index=terminal_index,
                    terminal_distance=terminal_distance,
                )

                if create_debug_images:
                    debug_images[page_number] = create_debug_image_from_document(
                        pdf_path,
                        page_index,
                        components,
                        terminals,
                    )

                for row in ct_loop_rows:
                    records.append(
                        {
                            "Include": True,
                            "No.": 0,
                            "Location": location,
                            "Item": row["Item"],
                            "Drawing No. Check list": drawing_number,
                            "Drawing Rev.": drawing_revision,
                            "A11 CoV Work Procedure Appendix": "G1",
                            "Circuit ID": row["Circuit ID"],
                            "Circuit Colour": row["Circuit Colour"],
                            "Test Terminals": row["Test Terminals"],
                            "G1 - Step 6": row["G1 - Step 6"],
                            "G1 - Step 7": row["G1 - Step 7"],
                            "G1 - Step 14": row["G1 - Step 14"],
                            "PDF Page": page_number,
                            "Review Status": row["Review Status"],
                            "Engineer Remarks": row["Engineer Remarks"],
                        }
                    )

                page_elapsed = time.perf_counter() - page_start_time
                write_log(
                    log_callback,
                    f"Page {page_number}: created {len(ct_loop_rows)} row(s) "
                    f"in {page_elapsed:.2f}s.",
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
            by=["_drawing_sort", "PDF Page", "Item", "Circuit ID"],
            ascending=True,
            kind="stable",
        ).reset_index(drop=True)
        dataframe = dataframe.drop(columns=["_drawing_sort"])
        dataframe["No."] = range(1, len(dataframe) + 1)

    elapsed = time.perf_counter() - start_time
    write_log(
        log_callback,
        f"Extraction complete. Rows created: {len(dataframe)}. "
        f"Total time: {elapsed:.2f}s.",
    )
    if progress_callback is not None:
        progress_callback(100)
    return dataframe, debug_images
