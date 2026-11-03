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

# Examples supported:
# 1252/W/NAC/ALT/R32/541
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

# Examples supported:
# V0.C89
# V1.D87
# V10.A123
# V1:R40.9
# V1:R40.10
# V2:R23.11
#
# Named groups:
# rack     = V1
# column   = D / R / AB etc.
# terminal = 87 / 40.9 / 40.10 etc.
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
# BW1(UP)
# KP1(DN)
# KX1(BRANCH)
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"([A-Z]{1,}\d+)\s*"
    r"\(\s*([A-Z0-9]+)\s*\)",
    re.IGNORECASE,
)

# Used only when no CT./TC. prefixed Item labels are found.
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
    """
    Send a message to the supplied Streamlit/UI logger if available.
    """
    if log_callback is not None:
        log_callback(message, level)


# ---------------------------------------------------------------------
# Drawing metadata
# ---------------------------------------------------------------------

def normalise_drawing_number(
    drawing_number: str,
) -> str:
    return drawing_number.upper().strip()


def get_location_from_drawing_number(
    drawing_number: str,
) -> str:
    """
    Dynamically derive location from drawing number.

    Example:
    1252/W/KOW/ALT/R32/531
             ^^^
             Returns KOW

    The location is the segment two positions before R<number>.
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
    """
    Return word records from the bottom title-block region only.
    """
    page_height = float(page.rect.height)
    page_width = float(page.rect.width)

    title_block_top = page_height * (1 - bottom_ratio)

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
    """
    Revision examples:
    KA1
    KA2
    KB1
    KX12
    """
    return bool(
        re.fullmatch(
            r"[A-Z]{1,3}\d{1,3}",
            value.strip(" ,;:()[]{}").upper(),
        )
    )


def get_revision_from_title_block(
    title_block_words: list[dict],
) -> str:
    """
    Find REV / REVISION and use the nearest valid revision value
    on the same title-block row.
    """
    revision_labels = []

    for word in title_block_words:
        value = word["value"].strip(" ,;:()[]{}").upper()

        if re.fullmatch(r"REV\.?", value):
            revision_labels.append(word)

        elif value == "REVISION":
            revision_labels.append(word)

    if not revision_labels:
        return "REVIEW"

    candidates = []

    for label in revision_labels:
        for word in title_block_words:
            value = word["value"].strip(" ,;:()[]{}").upper()

            if not is_revision_value(value):
                continue

            if word["x_center"] <= label["x1"]:
                continue

            horizontal_distance = word["x0"] - label["x1"]
            vertical_distance = abs(
                word["y_center"] - label["y_center"]
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
    """
    Extract controlled Drawing No. and Revision from page title block.
    """
    title_block_words = get_bottom_title_block_words(page)

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
        f"word_count={len(title_block_words)}.",
        "DEBUG",
    )

    return drawing_number, drawing_revision


# ---------------------------------------------------------------------
# Terminal extraction and pairing
# ---------------------------------------------------------------------

def parse_terminal_number(
    terminal_number: str,
) -> tuple[int, ...]:
    """
    Convert terminal identifiers to sortable tuple values.

    Examples:
    87      -> (87,)
    88      -> (88,)
    40.9    -> (40, 9)
    40.10   -> (40, 10)
    12-3    -> (12, 3)
    """
    parts = re.split(r"[.-]", str(terminal_number))

    parsed = []

    for part in parts:
        if part.isdigit():
            parsed.append(int(part))

    return tuple(parsed)


def terminal_numbers_are_consecutive(
    first_terminal: dict,
    second_terminal: dict,
) -> bool:
    """
    Generic terminal consecutiveness check.

    Examples:
    87 and 88           -> True
    40.9 and 40.10      -> True
    40.10 and 40.11     -> True
    40.9 and 41.10      -> False
    """
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
        and second_parts[-1] == first_parts[-1] + 1
    )


def remove_duplicate_terminals(
    terminals: list[dict],
) -> list[dict]:
    """
    Remove repeated text caused by duplicate PDF text layers.
    """
    unique_terminals = []

    for terminal in terminals:
        is_duplicate = False

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
                is_duplicate = True
                break

        if not is_duplicate:
            unique_terminals.append(terminal)

    return unique_terminals


def extract_terminal_references(
    page,
) -> list[dict]:
    """
    Extract terminal references with geometric coordinates.
    """
    terminals = []

    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, value = word[:5]

        cleaned_value = value.strip(" ,;:()[]{}")

        match = TERMINAL_PATTERN.fullmatch(
            cleaned_value
        )

        if not match:
            continue

        rack = match.group("rack").upper()
        column = match.group("column").upper()
        terminal_number = match.group("terminal")

        # Canonical output format is always Vx.COLUMNnumber.
        terminal_text = (
            f"{rack}.{column}{terminal_number}"
        )

        terminals.append(
            {
                "terminal": terminal_text,
                "rack": rack,
                "terminal_column": column,
                "terminal_number": terminal_number,
                "terminal_number_parts": (
                    parse_terminal_number(terminal_number)
                ),
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "x_center": round((x0 + x1) / 2, 2),
                "y_center": round((y0 + y1) / 2, 2),
            }
        )

    return remove_duplicate_terminals(terminals)


def pair_terminals_by_rack(
    terminals: list[dict],
) -> list[dict]:
    """
    Pair consecutive terminals within the same rack and terminal column.

    Examples:
    V1.E19 + V1.E20 -> V1.E19 & V1.E20
    V2.B41 + V2.B42 -> V2.B41 & V2.B42
    """
    terminals_by_block = defaultdict(list)

    for terminal in terminals:
        block_key = (
            terminal["rack"],
            terminal["terminal_column"],
        )

        terminals_by_block[block_key].append(terminal)

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
                        "column": first["terminal_column"],
                        "x_center": first["x_center"],
                        "y_center": first["y_center"],
                        "x_min": first["x0"],
                        "x_max": first["x1"],
                        "y_min": first["y0"],
                        "y_max": first["y1"],
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
                        "column": first["terminal_column"],
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
                        "x_min": min(
                            first["x0"],
                            second["x0"],
                        ),
                        "x_max": max(
                            first["x1"],
                            second["x1"],
                        ),
                        "y_min": min(
                            first["y0"],
                            second["y0"],
                        ),
                        "y_max": max(
                            first["y1"],
                            second["y1"],
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
                        "column": first["terminal_column"],
                        "x_center": first["x_center"],
                        "y_center": first["y_center"],
                        "x_min": first["x0"],
                        "x_max": first["x1"],
                        "y_min": first["y0"],
                        "y_max": first["y1"],
                        "review_required": True,
                    }
                )

                index += 1

    return pairs


# ---------------------------------------------------------------------
# Item extraction
# ---------------------------------------------------------------------

def extract_item_labels(
    page,
) -> list[dict]:
    """
    Extract Item labels and coordinates.

    Examples:
    CT.GN1(UP)  -> GN1(UP)
    TC.BW1(REP) -> BW1(REP)
    KP1(DN)     -> KP1(DN)
    """
    page_text = page.get_text("text", sort=True)

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

        # Only use fallback if no properly prefixed Item labels exist.
        if item_labels:
            break

    return item_labels


# ---------------------------------------------------------------------
# Dynamic rack topology
# ---------------------------------------------------------------------

def get_rack_lane_positions(
    terminal_pairs: list[dict],
) -> list[dict]:
    """
    Calculate the physical horizontal position of every rack.

    This is geometry-based, not rack-number-based.

    Example result:
    [
        {"rack": "V0", "x_center": 130.2, ...},
        {"rack": "V1", "x_center": 706.5, ...},
        {"rack": "V2", "x_center": 820.9, ...},
    ]
    """
    pairs_by_rack = defaultdict(list)

    for pair in terminal_pairs:
        pairs_by_rack[pair["rack"]].append(pair)

    rack_lanes = []

    for rack, rack_pairs in pairs_by_rack.items():
        x_values = sorted(
            pair["x_center"]
            for pair in rack_pairs
        )

        middle_index = len(x_values) // 2

        rack_lanes.append(
            {
                "rack": rack,
                "x_center": x_values[middle_index],
                "pair_count": len(rack_pairs),
            }
        )

    return sorted(
        rack_lanes,
        key=lambda rack_lane: rack_lane["x_center"],
    )


def select_ct_loop_racks(
    terminal_pairs: list[dict],
    log_callback=None,
) -> tuple[str, str] | tuple[None, None]:
    """
    Select CT-loop source and destination rack lanes dynamically.

    Rule:
    - identify all physical rack lanes from terminal X coordinates;
    - use the two right-most rack lanes;
    - left of those two = Step 6 source rack;
    - right-most = Step 7 / Step 14 destination rack.

    This avoids treating far-left equipment terminal references as
    CT-loop interface terminals.

    No rack IDs are fixed. V0, V1, V2 etc. are not hardcoded.
    """
    rack_lanes = get_rack_lane_positions(
        terminal_pairs
    )

    if len(rack_lanes) < 2:
        write_log(
            log_callback,
            "Cannot select CT-loop racks: fewer than "
            "two rack lanes were found.",
            "WARNING",
        )
        return None, None

    source_rack = rack_lanes[-2]["rack"]
    destination_rack = rack_lanes[-1]["rack"]

    write_log(
        log_callback,
        "Dynamic rack-lane selection: "
        f"lanes={rack_lanes}; "
        f"source={source_rack}; "
        f"destination={destination_rack}.",
        "DEBUG",
    )

    return source_rack, destination_rack


# ---------------------------------------------------------------------
# Item lane grouping
# ---------------------------------------------------------------------

def find_nearest_item(
    terminal_pair: dict,
    item_labels: list[dict],
) -> str:
    """
    Assign a terminal pair to the nearest Item label.

    Multiple occurrences of an Item label are allowed. For example,
    BD1(UP) may appear in more than one place on a drawing.

    The output remains one row per unique Item name later in
    create_ct_loop_rows().
    """
    if not item_labels:
        return "REVIEW REQUIRED"

    selected_item = "REVIEW REQUIRED"
    selected_score = None

    for item_label in item_labels:
        horizontal_distance = abs(
            terminal_pair["x_center"]
            - item_label["x_center"]
        )

        vertical_distance = abs(
            terminal_pair["y_center"]
            - item_label["y_center"]
        )

        # Vertical alignment is more important because electrical
        # drawing paths normally run horizontally.
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
    terminal_pairs: list[dict],
    item_labels: list[dict],
) -> dict[str, list[dict]]:
    """
    Group terminal pairs by their nearest Item name.

    All physical occurrences of an Item label are used for matching,
    but terminal pairs are grouped under the Item name only.
    """
    grouped = defaultdict(list)

    for pair in terminal_pairs:
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
    Group repeated physical Item-label positions by Item name.

    Example:

    [
        {"item": "BD1(UP)", "x_center": ..., "y_center": ...},
        {"item": "BD1(UP)", "x_center": ..., "y_center": ...},
        {"item": "BD1(REP)", "x_center": ..., "y_center": ...},
    ]

    becomes:

    {
        "BD1(UP)": [label1, label2],
        "BD1(REP)": [label3],
    }

    This guarantees one output row per Item name while preserving
    all Item positions for geometry matching.
    """
    labels_by_name = defaultdict(list)

    for item_label in item_labels:
        labels_by_name[
            item_label["item"]
        ].append(item_label)

    return dict(labels_by_name)


def remove_duplicate_pairs(
    pairs: list[dict],
) -> list[dict]:
    """
    Remove repeated terminal pairs caused by duplicate PDF text layers
    or repeated Item-label matching.
    """
    unique_pairs = []
    seen = set()

    for pair in pairs:
        pair_key = (
            pair["text"],
            pair["rack"],
            pair["column"],
        )

        if pair_key in seen:
            continue

        seen.add(pair_key)
        unique_pairs.append(pair)

    return unique_pairs


def distance_to_item_occurrences(
    terminal_pair: dict,
    item_occurrences: list[dict],
) -> tuple[float, float]:
    """
    Return the best geometric distance from a terminal pair to any
    visible occurrence of an Item label.

    This is important where the same Item is printed in more than one
    physical location on a controlled drawing.
    """
    distances = []

    for item_label in item_occurrences:
        vertical_distance = abs(
            terminal_pair["y_center"]
            - item_label["y_center"]
        )

        horizontal_distance = abs(
            terminal_pair["x_center"]
            - item_label["x_center"]
        )

        distances.append(
            (
                vertical_distance,
                horizontal_distance,
            )
        )

    if not distances:
        return 999999.0, 999999.0

    return min(distances)


def format_pairs(
    pairs: list[dict],
) -> str:
    """
    Format terminal pairs as multiline Excel-cell content.
    """
    return "\n".join(
        f"- {pair['text']}"
        for pair in pairs
    )


# ---------------------------------------------------------------------
# CT loop row creation
# ---------------------------------------------------------------------

def create_ct_loop_rows(
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> list[dict]:
    """
    Create one CT-loop checklist row per unique Item name.

    Dynamic workflow:
    1. Pair consecutive terminals within each rack/column.
    2. Select source and destination rack lanes dynamically.
    3. Match terminal pairs to the nearest Item drawing lane.
    4. Merge repeated physical Item labels into one Item record.
    5. Create one output row per unique Item name.

    No location, Item name, rack ID, terminal number, or drawing
    number is hardcoded.
    """
    if not terminals:
        return []

    if not item_labels:
        write_log(
            log_callback,
            "No Item labels found. "
            "Cannot create Item-based rows.",
            "WARNING",
        )
        return []

    terminals_by_rack = defaultdict(list)

    for terminal in terminals:
        terminals_by_rack[
            terminal["rack"]
        ].append(terminal)

    all_pairs = []

    for rack_terminals in terminals_by_rack.values():
        all_pairs.extend(
            pair_terminals_by_rack(rack_terminals)
        )

    if not all_pairs:
        write_log(
            log_callback,
            "No terminal pairs were created.",
            "WARNING",
        )
        return []

    source_rack, destination_rack = select_ct_loop_racks(
        all_pairs,
        log_callback=log_callback,
    )

    if source_rack is None or destination_rack is None:
        return []

    source_pairs = [
        pair
        for pair in all_pairs
        if pair["rack"] == source_rack
    ]

    destination_pairs = [
        pair
        for pair in all_pairs
        if pair["rack"] == destination_rack
    ]

    # Match pairs using every physical occurrence of each Item label.
    source_by_item = group_pairs_by_item(
        source_pairs,
        item_labels,
    )

    destination_by_item = group_pairs_by_item(
        destination_pairs,
        item_labels,
    )

    # This is the key duplicate-item protection.
    # One key/value group exists per unique Item name.
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

        # Step 6 = the source pair closest to any physical occurrence
        # of this Item label.
        item_source_pairs = sorted(
            item_source_pairs,
            key=lambda pair: distance_to_item_occurrences(
                pair,
                item_occurrences,
            ),
        )

        step_6_pairs = item_source_pairs[:1]

        # Step 7 = destination pairs associated with the Item.
        # Right-most destination pair is listed first and becomes
        # Step 14.
        step_7_pairs = sorted(
            item_destination_pairs,
            key=lambda pair: (
                -pair["x_center"],
                pair["y_center"],
            ),
        )

        step_14_pairs = []

        if step_7_pairs:
            step_14_pairs = [step_7_pairs[0]]

        # Do not create blank rows.
        if not step_6_pairs and not step_7_pairs:
            continue

        review_required = (
            not step_6_pairs
            or not step_7_pairs
            or any(
                pair["review_required"]
                for pair in (
                    step_6_pairs
                    + step_7_pairs
                )
            )
        )

        write_log(
            log_callback,
            f"CT-loop row: Item={item_name}; "
            f"Step6={[pair['text'] for pair in step_6_pairs]}; "
            f"Step7={[pair['text'] for pair in step_7_pairs]}; "
            f"Step14={[pair['text'] for pair in step_14_pairs]}.",
            "DEBUG",
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
                    "Candidate created by dynamic rack-lane "
                    "and Item-lane geometry. Verify against "
                    "controlled drawing."
                ),
            }
        )

    return rows


# ---------------------------------------------------------------------
# Page qualification and debug image
# ---------------------------------------------------------------------

def page_is_relevant(
    page_text: str,
    log_callback=None,
) -> bool:
    """
    A relevant page requires:
    - at least one recognised terminal reference;
    - at least one Item label.
    """
    terminal_matches = TERMINAL_PATTERN.findall(
        page_text
    )

    item_matches = (
        ITEM_PATTERN.findall(page_text)
        or FALLBACK_ITEM_PATTERN.findall(page_text)
    )

    write_log(
        log_callback,
        "Page relevance check: "
        f"terminal_matches={terminal_matches[:10]}; "
        f"item_matches={item_matches[:10]}.",
        "DEBUG",
    )

    return bool(terminal_matches) and bool(item_matches)


def create_debug_image(
    page,
    terminals: list[dict],
) -> bytes:
    """
    Return a PNG debug image with red boxes around detected terminals.
    """
    red_colour = (1.0, 0.0, 0.0)
    padding = 2.5

    shape = page.new_shape()

    for terminal in terminals:
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
# Main extraction entry point
# ---------------------------------------------------------------------

def extract_pdf(
    pdf_path: str | Path,
    log_callback=None,
    progress_callback=None,
) -> tuple[pd.DataFrame, dict[int, bytes]]:
    """
    Extract CT-loop checklist candidate rows from a controlled PDF.

    Returns:
        dataframe,
        {
            pdf_page_number: png_debug_image_bytes
        }
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

            write_log(
                log_callback,
                f"----- Processing page "
                f"{page_number}/{total_pages} -----",
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
                        "no recognised terminal/item pattern.",
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
                        "Drawing Number not found in title block.",
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

                if not terminals:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no terminal references extracted.",
                        "WARNING",
                    )
                    continue

                if not item_labels:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no Item labels extracted.",
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
                            "A11 CoV Work Procedure Appendix": "G1",
                            "G1 - Step 6": row["G1 - Step 6"],
                            "G1 - Step 7": row["G1 - Step 7"],
                            "G1 - Step 14": row["G1 - Step 14"],
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

    if not dataframe.empty:
        # Sort using the numeric sheet part after /R<number>/.
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
