from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd


# ---------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------

# Examples:
# 1252/W/OLY/ALT/R32/501
# 1252/W/KOW/ALT/R32/531
# 1252/W/KOW/ALT/R32/555/A1
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

# Used only if no prefixed CT./TC. Items are found.
FALLBACK_ITEM_PATTERN = re.compile(
    r"\b([A-Z]{2,}\d+)\s*"
    r"\(\s*([A-Z0-9]+)\s*\)",
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
        1252/W/OLY/ALT/R32/501

    Returns:
        OLY

    Location is the segment two places before R<number>.
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
                "page_width": page_width,
                "page_height": page_height,
            }
        )

    return words


def join_title_block_words(
    title_block_words: list[dict],
) -> str:
    return " ".join(
        word["value"]
        for word in title_block_words
    )


def get_drawing_number_from_title_block(
    title_block_words: list[dict],
) -> str:
    title_block_text = join_title_block_words(
        title_block_words
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
        value = word["value"].strip(
            " ,;:()[]{}"
        ).upper()

        if re.fullmatch(r"REV\.?", value):
            revision_labels.append(word)

        elif value == "REVISION":
            revision_labels.append(word)

    if not revision_labels:
        return "REVIEW"

    candidates = []

    for revision_label in revision_labels:
        for word in title_block_words:
            value = word["value"].strip(
                " ,;:()[]{}"
            ).upper()

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
        f"revision={drawing_revision}; "
        f"words={len(title_block_words)}.",
        "DEBUG",
    )

    return drawing_number, drawing_revision


# ---------------------------------------------------------------------
# Terminal extraction
# ---------------------------------------------------------------------

def parse_terminal_number(
    terminal_number: str,
) -> tuple[int, ...]:
    """
    Examples:
        87      -> (87,)
        40.9    -> (40, 9)
        40.10   -> (40, 10)
        12-3    -> (12, 3)
    """
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


def remove_duplicate_terminals(
    terminals: list[dict],
) -> list[dict]:
    unique_terminals = []

    for terminal in terminals:
        duplicate_found = False

        for existing in unique_terminals:
            same_terminal = (
                terminal["terminal"]
                == existing["terminal"]
            )

            same_position = (
                abs(
                    terminal["x_center"]
                    - existing["x_center"]
                ) <= 2
                and abs(
                    terminal["y_center"]
                    - existing["y_center"]
                ) <= 2
            )

            if same_terminal and same_position:
                duplicate_found = True
                break

        if not duplicate_found:
            unique_terminals.append(terminal)

    return unique_terminals


def extract_terminal_references(
    page,
) -> list[dict]:
    terminals = []

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
        terminal_column = match.group(
            "column"
        ).upper()

        terminal_number = match.group(
            "terminal"
        )

        terminals.append(
            {
                "terminal": (
                    f"{rack}."
                    f"{terminal_column}"
                    f"{terminal_number}"
                ),
                "rack": rack,
                "terminal_column": terminal_column,
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
                "x_center": round(
                    (x0 + x1) / 2,
                    2,
                ),
                "y_center": round(
                    (y0 + y1) / 2,
                    2,
                ),
                "page_width": float(
                    page.rect.width
                ),
            }
        )

    return remove_duplicate_terminals(terminals)


# ---------------------------------------------------------------------
# Physical terminal-lane detection
# ---------------------------------------------------------------------

def get_lane_tolerance(
    terminals: list[dict],
) -> float:
    """
    Calculate physical lane tolerance from PDF page width.

    This is not tied to any location, rack ID, terminal number,
    Item name, or drawing number.
    """
    if not terminals:
        return 20.0

    page_width = terminals[0].get(
        "page_width",
        1000.0,
    )

    return max(
        12.0,
        min(page_width * 0.02, 45.0),
    )


def build_physical_terminal_lanes(
    terminals: list[dict],
) -> list[dict]:
    """
    Split terminals into physical left-to-right lanes.

    Example:

        V1 left | V0 middle | V1 right

    The two V1 areas become separate lanes because their physical
    X positions are different.
    """
    if not terminals:
        return []

    tolerance = get_lane_tolerance(terminals)

    ordered_terminals = sorted(
        terminals,
        key=lambda terminal: terminal["x_center"],
    )

    raw_lanes = []

    for terminal in ordered_terminals:
        if not raw_lanes:
            raw_lanes.append(
                {
                    "x_values": [
                        terminal["x_center"]
                    ],
                    "terminals": [terminal],
                }
            )
            continue

        current_lane = raw_lanes[-1]

        lane_x_center = sum(
            current_lane["x_values"]
        ) / len(current_lane["x_values"])

        if abs(
            terminal["x_center"] - lane_x_center
        ) <= tolerance:
            current_lane["x_values"].append(
                terminal["x_center"]
            )

            current_lane["terminals"].append(
                terminal
            )

        else:
            raw_lanes.append(
                {
                    "x_values": [
                        terminal["x_center"]
                    ],
                    "terminals": [terminal],
                }
            )

    lanes = []

    for lane_id, raw_lane in enumerate(raw_lanes):
        rack_counts = defaultdict(int)

        for terminal in raw_lane["terminals"]:
            rack_counts[terminal["rack"]] += 1

        primary_rack = max(
            rack_counts,
            key=rack_counts.get,
        )

        lanes.append(
            {
                "lane_id": lane_id,
                "x_center": round(
                    sum(raw_lane["x_values"])
                    / len(raw_lane["x_values"]),
                    2,
                ),
                "terminals": raw_lane["terminals"],
                "primary_rack": primary_rack,
            }
        )

    return lanes


# ---------------------------------------------------------------------
# Terminal pairing inside physical lanes
# ---------------------------------------------------------------------

def pair_terminals_in_lane(
    lane: dict,
) -> list[dict]:
    """
    Pair consecutive terminals only within one physical lane.

    This prevents the two V1 sides of a drawing from being merged
    simply because they have the same rack name.
    """
    terminals_by_block = defaultdict(list)

    for terminal in lane["terminals"]:
        key = (
            terminal["rack"],
            terminal["terminal_column"],
        )

        terminals_by_block[key].append(
            terminal
        )

    pairs = []

    for block_terminals in terminals_by_block.values():
        ordered = sorted(
            block_terminals,
            key=lambda terminal: (
                terminal["terminal_number_parts"],
                terminal["y_center"],
            ),
        )

        index = 0

        while index < len(ordered):
            first = ordered[index]

            if index + 1 >= len(ordered):
                pairs.append(
                    {
                        "text": (
                            f"{first['terminal']} "
                            "(UNPAIRED - REVIEW)"
                        ),
                        "rack": first["rack"],
                        "column": first[
                            "terminal_column"
                        ],
                        "lane_id": lane["lane_id"],
                        "x_center": first["x_center"],
                        "y_center": first["y_center"],
                        "review_required": True,
                    }
                )

                index += 1
                continue

            second = ordered[index + 1]

            if terminal_numbers_are_consecutive(
                first,
                second,
            ):
                pairs.append(
                    {
                        "text": (
                            f"{first['terminal']} & "
                            f"{second['terminal']}"
                        ),
                        "rack": first["rack"],
                        "column": first[
                            "terminal_column"
                        ],
                        "lane_id": lane["lane_id"],
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
                        "review_required": False,
                    }
                )

                index += 2

            else:
                pairs.append(
                    {
                        "text": (
                            f"{first['terminal']} "
                            "(UNPAIRED - REVIEW)"
                        ),
                        "rack": first["rack"],
                        "column": first[
                            "terminal_column"
                        ],
                        "lane_id": lane["lane_id"],
                        "x_center": first["x_center"],
                        "y_center": first["y_center"],
                        "review_required": True,
                    }
                )

                index += 1

    return pairs


def add_pairs_to_lanes(
    lanes: list[dict],
) -> list[dict]:
    for lane in lanes:
        lane["pairs"] = pair_terminals_in_lane(lane)
        lane["pair_count"] = len(lane["pairs"])

    return lanes


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

    item_labels = []
    seen = set()

    patterns = [
        ITEM_PATTERN,
        FALLBACK_ITEM_PATTERN,
    ]

    for pattern in patterns:
        for match in pattern.finditer(page_text):
            item_name = (
                f"{match.group(1).upper()}"
                f"({match.group(2).upper()})"
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
# Generic in-between topology:
# same outer rack | different middle rack | same outer rack
# ---------------------------------------------------------------------

def select_ct_loop_lanes(
    lanes: list[dict],
    log_callback=None,
) -> tuple[dict | None, list[dict]]:
    """
    Select a generic three-lane topology:

        outer rack A | middle rack B | outer rack A

    For example:

        V1 | V0 | V1
        V2 | V1 | V2
        V5 | V3 | V5

    Output:
        source lane       = middle lane
        destination lanes = [right outer lane, left outer lane]

    The right lane is intentionally first. This gives the desired
    Step 7 order and makes the first Step 7 pair Step 14.
    """
    if len(lanes) < 3:
        write_log(
            log_callback,
            "Cannot select CT-loop lanes: fewer than "
            "three physical terminal lanes were detected.",
            "WARNING",
        )
        return None, []

    candidates = []

    for index in range(len(lanes) - 2):
        left_lane = lanes[index]
        middle_lane = lanes[index + 1]
        right_lane = lanes[index + 2]

        outer_racks_match = (
            left_lane["primary_rack"]
            == right_lane["primary_rack"]
        )

        middle_rack_differs = (
            middle_lane["primary_rack"]
            != left_lane["primary_rack"]
        )

        if not outer_racks_match:
            continue

        if not middle_rack_differs:
            continue

        if not middle_lane["pairs"]:
            continue

        if not left_lane["pairs"]:
            continue

        if not right_lane["pairs"]:
            continue

        left_to_middle = abs(
            middle_lane["x_center"]
            - left_lane["x_center"]
        )

        middle_to_right = abs(
            right_lane["x_center"]
            - middle_lane["x_center"]
        )

        candidates.append(
            {
                "left_lane": left_lane,
                "source_lane": middle_lane,
                "right_lane": right_lane,
                "pair_count": (
                    left_lane["pair_count"]
                    + middle_lane["pair_count"]
                    + right_lane["pair_count"]
                ),
                "geometry_balance": abs(
                    left_to_middle
                    - middle_to_right
                ),
            }
        )

    if not candidates:
        write_log(
            log_callback,
            "No valid in-between topology found. "
            "Expected: same outer rack | different "
            "middle rack | same outer rack.",
            "WARNING",
        )
        return None, []

    candidates.sort(
        key=lambda candidate: (
            -candidate["pair_count"],
            candidate["geometry_balance"],
            candidate["source_lane"]["x_center"],
        )
    )

    selected = candidates[0]

    left_lane = selected["left_lane"]
    source_lane = selected["source_lane"]
    right_lane = selected["right_lane"]

    destination_lanes = [
        right_lane,
        left_lane,
    ]

    write_log(
        log_callback,
        "In-between topology selected: "
        f"left=(lane={left_lane['lane_id']}, "
        f"rack={left_lane['primary_rack']}); "
        f"middle_step6=(lane={source_lane['lane_id']}, "
        f"rack={source_lane['primary_rack']}); "
        f"right_step7=(lane={right_lane['lane_id']}, "
        f"rack={right_lane['primary_rack']}).",
        "DEBUG",
    )

    return source_lane, destination_lanes


# ---------------------------------------------------------------------
# Item grouping
# ---------------------------------------------------------------------

def find_nearest_item(
    pair: dict,
    item_labels: list[dict],
) -> str:
    if not item_labels:
        return "REVIEW REQUIRED"

    selected_item = "REVIEW REQUIRED"
    selected_score = None

    for item_label in item_labels:
        horizontal_distance = abs(
            pair["x_center"]
            - item_label["x_center"]
        )

        vertical_distance = abs(
            pair["y_center"]
            - item_label["y_center"]
        )

        score = (
            vertical_distance * 5
            + horizontal_distance
        )

        if (
            selected_score is None
            or score < selected_score
        ):
            selected_score = score
            selected_item = item_label["item"]

    return selected_item


def group_pairs_by_item(
    pairs: list[dict],
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    grouped = defaultdict(list)

    for pair in pairs:
        item_name = find_nearest_item(
            pair,
            item_labels,
        )

        grouped[item_name].append(pair)

    return dict(grouped)


def group_item_labels_by_name(
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    """
    One final output row per Item name, even if the same Item label
    appears more than once in the PDF.
    """
    grouped = defaultdict(list)

    for item_label in item_labels:
        grouped[item_label["item"]].append(
            item_label
        )

    return dict(grouped)


def remove_duplicate_pairs(
    pairs: list[dict],
) -> list[dict]:
    unique_pairs = []
    seen = set()

    for pair in pairs:
        key = (
            pair["text"],
            pair["rack"],
            pair["column"],
            pair["lane_id"],
            round(pair["x_center"], 1),
            round(pair["y_center"], 1),
        )

        if key in seen:
            continue

        seen.add(key)
        unique_pairs.append(pair)

    return unique_pairs


def distance_to_item_occurrences(
    pair: dict,
    item_occurrences: list[dict],
) -> tuple[float, float]:
    distances = []

    for item_label in item_occurrences:
        distances.append(
            (
                abs(
                    pair["y_center"]
                    - item_label["y_center"]
                ),
                abs(
                    pair["x_center"]
                    - item_label["x_center"]
                ),
            )
        )

    if not distances:
        return 999999.0, 999999.0

    return min(distances)


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
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> list[dict]:
    """
    Create one CT-loop checklist row per unique Item.

    Generic topology:

        Outer lane A | Middle lane B | Outer lane A

    Step 6:
        Terminal pairs in middle lane B.

    Step 7:
        Terminal pairs in both outer lanes.
        Right outer lane is listed before left outer lane.

    Step 14:
        First Step 7 pair, which is the right-most destination pair.

    Example:

        V1 left | V0 middle | V1 right

    Step 6:
        V0.A65 & V0.A66

    Step 7:
        V1.B5 & V1.B6
        V1.A5 & V1.A6

    Step 14:
        V1.B5 & V1.B6
    """
    if not terminals or not item_labels:
        return []

    lanes = build_physical_terminal_lanes(
        terminals
    )

    lanes = add_pairs_to_lanes(lanes)

    source_lane, destination_lanes = (
        select_ct_loop_lanes(
            lanes,
            log_callback=log_callback,
        )
    )

    if source_lane is None or not destination_lanes:
        return []

    source_pairs = source_lane["pairs"]

    destination_pairs = []

    # Right destination lane is added first, then left destination lane.
    for destination_lane in destination_lanes:
        destination_pairs.extend(
            destination_lane["pairs"]
        )

    source_by_item = group_pairs_by_item(
        source_pairs,
        item_labels,
    )

    destination_by_item = group_pairs_by_item(
        destination_pairs,
        item_labels,
    )

    item_labels_by_name = group_item_labels_by_name(
        item_labels
    )

    rows = []

    for item_name, item_occurrences in (
        item_labels_by_name.items()
    ):
        item_source_pairs = remove_duplicate_pairs(
            source_by_item.get(item_name, [])
        )

        item_destination_pairs = remove_duplicate_pairs(
            destination_by_item.get(item_name, [])
        )

        # Step 6 = nearest source/middle pair to Item.
        item_source_pairs = sorted(
            item_source_pairs,
            key=lambda pair: distance_to_item_occurrences(
                pair,
                item_occurrences,
            ),
        )

        step_6_pairs = item_source_pairs[:1]

        # Step 7:
        # Right-most terminal pair first.
        # If X positions are similar, use vertical position.
        step_7_pairs = sorted(
            item_destination_pairs,
            key=lambda pair: (
                -pair["x_center"],
                pair["y_center"],
            ),
        )

        # Step 14 = right-most Step 7 pair.
        step_14_pairs = []

        if step_7_pairs:
            step_14_pairs = [step_7_pairs[0]]

        # Do not create incomplete candidate rows.
        if not step_6_pairs or not step_7_pairs:
            write_log(
                log_callback,
                f"Item={item_name}: skipped; "
                "Step 6 or Step 7 not found.",
                "DEBUG",
            )
            continue

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
                    "Candidate created from dynamic "
                    "outer-middle-outer physical lane "
                    "topology. Verify controlled drawing."
                ),
            }
        )

        write_log(
            log_callback,
            f"CT loop: Item={item_name}; "
            f"Step6={[pair['text'] for pair in step_6_pairs]}; "
            f"Step7={[pair['text'] for pair in step_7_pairs]}; "
            f"Step14={[pair['text'] for pair in step_14_pairs]}.",
            "DEBUG",
        )

    return rows


# ---------------------------------------------------------------------
# Page relevance and debug image
# ---------------------------------------------------------------------

def page_is_relevant(
    page_text: str,
    log_callback=None,
) -> bool:
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
        f"terminals={terminal_matches[:10]}; "
        f"items={item_matches[:10]}.",
        "DEBUG",
    )

    return bool(terminal_matches) and bool(
        item_matches
    )


def create_debug_image(
    page,
    terminals: list[dict],
) -> bytes:
    red_colour = (1.0, 0.0, 0.0)
    padding = 2.5

    shape = page.new_shape()

    for terminal in terminals:
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

    if terminals:
        shape.finish(
            color=red_colour,
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
                    page_text,
                    log_callback=log_callback,
                ):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no Item / terminal pattern.",
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
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        f"terminals={len(terminals)}, "
                        f"items={len(item_labels)}.",
                        "WARNING",
                    )
                    continue

                ct_loop_rows = create_ct_loop_rows(
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                )

                debug_images[page_number] = (
                    create_debug_image(
                        page,
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
