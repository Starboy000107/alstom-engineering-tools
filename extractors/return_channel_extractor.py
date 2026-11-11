from __future__ import annotations

import colorsys
import math
import re
import time
from collections import defaultdict
from pathlib import Path

import cv2
import fitz
import numpy as np
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
FUSE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "img"
    / "fuse_wave_template.png"
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
# Raster / image fuse-shape detection
# ---------------------------------------------------------------------

def render_page_for_fuse_detection(
    page,
    zoom: float = 3.0,
) -> tuple[np.ndarray, float]:
    """
    Render a PDF page for image-based fuse-shape detection.

    Returns:
        image: BGR OpenCV image
        scale: PDF points -> raster pixels scale factor
    """
    matrix = fitz.Matrix(
        zoom,
        zoom,
    )

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False,
        colorspace=fitz.csRGB,
    )

    image = np.frombuffer(
        pixmap.samples,
        dtype=np.uint8,
    )

    image = image.reshape(
        pixmap.height,
        pixmap.width,
        3,
    )

    # PyMuPDF pixmap is RGB. OpenCV expects BGR.
    image = cv2.cvtColor(
        image,
        cv2.COLOR_RGB2BGR,
    )

    return image, zoom

def get_cyan_mask(
    image_bgr: np.ndarray,
) -> np.ndarray:
    """
    Return a binary mask for cyan/turquoise engineering circuitwork.

    The template and Return Channel fuse symbols are cyan. This avoids
    matching black straight lines, red revision boxes, text, and other
    non-cyan drawing features.
    """
    hsv_image = cv2.cvtColor(
        image_bgr,
        cv2.COLOR_BGR2HSV,
    )

    # OpenCV Hue range is 0-179.
    #
    # Cyan / turquoise commonly falls around 75-105 in OpenCV HSV.
    # Saturation/value conditions reject black, grey, white and red.
    lower_cyan = np.array(
        [70, 80, 80],
        dtype=np.uint8,
    )

    upper_cyan = np.array(
        [105, 255, 255],
        dtype=np.uint8,
    )

    return cv2.inRange(
        hsv_image,
        lower_cyan,
        upper_cyan,
    )


def load_fuse_wave_template() -> np.ndarray | None:
    """
    Load and prepare the cyan wave-fuse image template.

    Expected file:

        img/fuse_wave_template.png

    The template should contain only:

        cyan terminal dot + cyan wave + cyan terminal dot

    Do not include red crop borders, #number, X board, B24, 100mA,
    or surrounding circuit text.
    """
    if not FUSE_TEMPLATE_PATH.exists():
        return None

    template_bgr = cv2.imread(
        str(FUSE_TEMPLATE_PATH),
        cv2.IMREAD_COLOR,
    )

    if template_bgr is None:
        return None

    cyan_mask = get_cyan_mask(template_bgr)

    # Remove tiny isolated cyan noise.
    cyan_mask = cv2.morphologyEx(
        cyan_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (3, 3),
        ),
    )

    non_zero = cv2.findNonZero(cyan_mask)

    if non_zero is None:
        return None

    x, y, width, height = cv2.boundingRect(
        non_zero
    )

    # Tight crop around detected cyan fuse symbol.
    cyan_mask = cyan_mask[
        y:y + height,
        x:x + width,
    ]

    if cyan_mask.shape[0] < 4:
        return None

    if cyan_mask.shape[1] < 10:
        return None

    return cyan_mask

def log_fuse_template_status(
    log_callback=None,
):
    if FUSE_TEMPLATE_PATH.exists():
        write_log(
            log_callback,
            "Fuse-wave template loaded: "
            f"{FUSE_TEMPLATE_PATH}",
            "DEBUG",
        )
    else:
        write_log(
            log_callback,
            "Fuse-wave template NOT FOUND: "
            f"{FUSE_TEMPLATE_PATH}",
            "ERROR",
        )

def rectangles_overlap(
    first: fitz.Rect,
    second: fitz.Rect,
) -> bool:
    """
    True when two PDF rectangles overlap.
    """
    return not (
        first.x1 < second.x0
        or second.x1 < first.x0
        or first.y1 < second.y0
        or second.y1 < first.y0
    )

def get_fuse_wave_image_candidates(
    page,
) -> list[dict]:
    """
    Detect cyan wave-fuse symbols using the supplied fuse template.

    Detection uses cyan-mask template matching. Therefore it rejects:

    - black straight fuses;
    - black cable lines;
    - red revision boundaries;
    - black terminal blocks;
    - ordinary text;
    - non-cyan curved symbols.

    The detected physical fuse-wave shape is then used later to read
    nearby #number, X board and 100mA text.
    """
    template_mask = load_fuse_wave_template()

    if template_mask is None:
        return []

    page_image, scale = render_page_for_fuse_detection(
        page,
        zoom=4.0,
    )

    page_cyan_mask = get_cyan_mask(
        page_image
    )

    # Clean small raster/render noise but preserve the fuse wave.
    page_cyan_mask = cv2.morphologyEx(
        page_cyan_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (3, 3),
        ),
    )

    template_height, template_width = (
        template_mask.shape[:2]
    )

    if template_width < 10 or template_height < 4:
        return []

    raw_candidates = []

    # Different PDF exports / sheet scales can alter the rendered
    # visual size of the same fuse symbol.
    for template_scale in [
        0.55,
        0.65,
        0.75,
        0.85,
        0.95,
        1.00,
        1.10,
        1.20,
        1.35,
        1.50,
        1.70,
    ]:
        resized_width = max(
            10,
            int(template_width * template_scale),
        )

        resized_height = max(
            4,
            int(template_height * template_scale),
        )

        resized_template = cv2.resize(
            template_mask,
            (
                resized_width,
                resized_height,
            ),
            interpolation=cv2.INTER_NEAREST,
        )

        if (
            resized_template.shape[0]
            >= page_cyan_mask.shape[0]
        ):
            continue

        if (
            resized_template.shape[1]
            >= page_cyan_mask.shape[1]
        ):
            continue

        # CCORR_NORMED is suitable for binary cyan masks.
        match_result = cv2.matchTemplate(
            page_cyan_mask,
            resized_template,
            cv2.TM_CCORR_NORMED,
        )

        # Strict enough to reject plain cyan straight wiring.
        # Lower to 0.76 only if real fuse waves are missed.
        y_positions, x_positions = np.where(
            match_result >= 0.82
        )

        for y_pixel, x_pixel in zip(
            y_positions,
            x_positions,
        ):
            score = float(
                match_result[y_pixel, x_pixel]
            )

            rectangle = fitz.Rect(
                x_pixel / scale,
                y_pixel / scale,
                (
                    x_pixel
                    + resized_template.shape[1]
                ) / scale,
                (
                    y_pixel
                    + resized_template.shape[0]
                ) / scale,
            )

            raw_candidates.append(
                {
                    "rect": rectangle,
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
                    "template_score": score,
                    "curve_count": 0,
                    "detection_source": (
                        "CYAN_FUSE_WAVE_TEMPLATE"
                    ),
                }
            )

    # Non-maximum suppression:
    # one physical fuse can match multiple template scales.
    raw_candidates.sort(
        key=lambda candidate: (
            -candidate["template_score"],
            candidate["point"].y,
            candidate["point"].x,
        )
    )

    selected = []

    for candidate in raw_candidates:
        duplicate_found = False

        for existing in selected:
            existing_rect = fitz.Rect(
                existing["rect"]
            )

            # Merge visually overlapping detections.
            existing_rect.x0 -= 10
            existing_rect.y0 -= 10
            existing_rect.x1 += 10
            existing_rect.y1 += 10

            if rectangles_overlap(
                candidate["rect"],
                existing_rect,
            ):
                duplicate_found = True
                break

        if not duplicate_found:
            selected.append(candidate)

    selected.sort(
        key=lambda candidate: (
            candidate["point"].y,
            candidate["point"].x,
        )
    )

    return selected




def detect_fuse_wave_symbols(
    page,
) -> list[dict]:
    """
    Detect only the real visual wave-fuse symbol.

    This intentionally uses image-template matching, not generic
    curve/line/contour matching.

    Therefore it rejects:
      - straight fuse links;
      - terminal blocks;
      - dashed revision boxes;
      - ordinary circuit lines;
      - random Bezier curves;
      - cable markers.
    """
    return get_fuse_wave_image_candidates(page)


def words_near_fuse_symbol(
    words: list[dict],
    fuse_symbol: dict,
    page,
) -> list[dict]:
    """
    Return text in the immediate local neighbourhood of a validated
    fuse wave.

    A narrow local region prevents text from a nearby straight fuse,
    terminal block or separate circuit from being assigned to this
    wave symbol.
    """
    page_scale = max(
        float(page.rect.width),
        float(page.rect.height),
    )

    horizontal_padding = max(
        25.0,
        min(page_scale * 0.045, 75.0),
    )

    vertical_padding = max(
        20.0,
        min(page_scale * 0.035, 55.0),
    )

    search_rect = fitz.Rect(
        fuse_symbol["rect"].x0 - horizontal_padding,
        fuse_symbol["rect"].y0 - vertical_padding,
        fuse_symbol["rect"].x1 + horizontal_padding,
        fuse_symbol["rect"].y1 + vertical_padding,
    )

    nearby_words = []

    for word in words:
        if search_rect.contains(word["point"]):
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

        rating_found = local_fuse_has_current_rating(
            nearby_words
        )

        # A valid fuse candidate must be the actual wave symbol and have
        # local fuse number, local X board reference, and local current
        # rating. This prevents detection of straight/ordinary links.
        if not rating_found:
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

        # All accepted candidates have:
        # S-wave geometry + current rating + #/F number + X board.
        confidence = 4

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
                "template_score": fuse_symbol.get(
                    "template_score",
                    0.0,
                ),
                "detection_source": fuse_symbol.get(
                    "detection_source",
                    "UNKNOWN",
                ),
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
    Logs only visual template-matched wave-fuse symbols and resulting
    local-text fuse candidates.
    """
    wave_symbols = detect_fuse_wave_symbols(page)

    wave_diagnostics = []

    for symbol in wave_symbols:
        wave_diagnostics.append(
            {
                "x": round(symbol["point"].x, 1),
                "y": round(symbol["point"].y, 1),
                "width": round(
                    symbol["rect"].width,
                    1,
                ),
                "height": round(
                    symbol["rect"].height,
                    1,
                ),
                "score": round(
                    symbol.get(
                        "template_score",
                        0.0,
                    ),
                    3,
                ),
                "source": symbol.get(
                    "detection_source",
                    "UNKNOWN",
                ),
            }
        )

    candidate_diagnostics = []

    for candidate in fuse_candidates:
        candidate_diagnostics.append(
            {
                "value": candidate["value"],
                "fuse_label": candidate["fuse_label"],
                "x_label": candidate["x_label"],
                "rating_found": candidate[
                    "rating_found"
                ],
                "source": candidate.get(
                    "detection_source",
                    "UNKNOWN",
                ),
                "score": round(
                    candidate.get(
                        "template_score",
                        0.0,
                    ),
                    3,
                ),
            }
        )

    write_log(
        log_callback,
        "Fuse image diagnostic: "
        f"wave_symbols={wave_diagnostics}; "
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
    excluded_candidate_ids: set[int],
) -> tuple[int, dict] | tuple[None, None]:
    """
    Select a physical fuse candidate nearest an equipment area.

    Candidates are excluded by their physical list index, not fuse
    value. This permits two physically separate fuse symbols to have
    the same fuse reference where the controlled drawing shows that.
    """
    available = [
        (index, candidate)
        for index, candidate in enumerate(
            fuse_candidates
        )
        if index not in excluded_candidate_ids
    ]

    if not available:
        return None, None

    if equipment_label is None:
        available.sort(
            key=lambda pair: (
                -pair[1].get(
                    "confidence",
                    0,
                ),
                pair[1]["point"].y,
                pair[1]["point"].x,
            )
        )

        return available[0]

    available.sort(
        key=lambda pair: (
            point_distance(
                pair[1]["point"],
                equipment_label["point"],
            ),
            -pair[1].get(
                "confidence",
                0,
            ),
            pair[1]["point"].y,
            pair[1]["point"].x,
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

        used_fuse_candidate_ids = set()

        ns_fuse_index, ns_rack_fuse = (
            select_fuse_for_equipment(
                fuse_candidates,
                ns_rack_label,
                used_fuse_candidate_ids,
            )
        )

        if ns_fuse_index is not None:
            used_fuse_candidate_ids.add(
                ns_fuse_index
            )

        ixl_fuse_index, ixl_fuse = (
            select_fuse_for_equipment(
                fuse_candidates,
                ixl_label,
                used_fuse_candidate_ids,
            )
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
    selected_fuses: list[dict] | None = None,
) -> bytes:
    """
    Debug overlay.

    Red boxes:
        selected terminal references.

    Orange boxes:
        detected fuse-wave symbol regions.
    """
    selected_fuses = selected_fuses or []

    terminal_padding = 2.5
    fuse_padding = 4.0

    terminal_shape = page.new_shape()
    fuse_shape = page.new_shape()

    # Terminal boxes: red.
    for terminal in selected_terminals:
        rectangle = fitz.Rect(
            max(
                0,
                terminal["x0"]
                - terminal_padding,
            ),
            max(
                0,
                terminal["y0"]
                - terminal_padding,
            ),
            min(
                page.rect.width,
                terminal["x1"]
                + terminal_padding,
            ),
            min(
                page.rect.height,
                terminal["y1"]
                + terminal_padding,
            ),
        )

        terminal_shape.draw_rect(rectangle)

    if selected_terminals:
        terminal_shape.finish(
            color=(1.0, 0.0, 0.0),
            width=1.25,
            stroke_opacity=1.0,
        )

        terminal_shape.commit()

    # Fuse boxes: orange/red-orange.
    for fuse in selected_fuses:
        fuse_rect = fuse.get("rect")

        if fuse_rect is None:
            continue

        rectangle = fitz.Rect(
            max(
                0,
                fuse_rect.x0
                - fuse_padding,
            ),
            max(
                0,
                fuse_rect.y0
                - fuse_padding,
            ),
            min(
                page.rect.width,
                fuse_rect.x1
                + fuse_padding,
            ),
            min(
                page.rect.height,
                fuse_rect.y1
                + fuse_padding,
            ),
        )

        fuse_shape.draw_rect(rectangle)

    if selected_fuses:
        fuse_shape.finish(
            color=(1.0, 0.0, 0.0),
            width=1.6,
            stroke_opacity=1.0,
        )

        fuse_shape.commit()

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

            if page_number == 1:
                log_fuse_template_status(
                    log_callback=log_callback,
                )

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

                # A valid Return Channel checklist requires two physical fuse
                # symbol groups: one associated with each required equipment side.
                #
                # Do not create a candidate row merely because a page title says
                # RETURN CHANNEL and it contains terminals.
                if len(fuse_candidates) < 2:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        f"only {len(fuse_candidates)} valid fuse "
                        "symbol candidate(s) found; two are required.",
                        "WARNING",
                    )
                    continue

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
                            fuse_candidates,
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
