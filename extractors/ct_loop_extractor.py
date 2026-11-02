from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd


# Controlled drawing number, for example:
# 1252/W/NAC/ALT/R32/541
DRAWING_PATTERN = re.compile(
    r"\b\d+/(?:[A-Z0-9-]+/)+R\d+/\d+\b",
    re.IGNORECASE,
)

# Terminal reference examples:
# V0.C89
# V1.D87
# V2.B83
# V10.A123
TERMINAL_PATTERN = re.compile(
    r"\b(V\d+)\.([A-Z]+)(\d+)\b",
    re.IGNORECASE,
)

# Item examples:
# CT.BW1(UP)
# TC.BW1(REP)
# BW1(UP)
# KP1(DN)
# KX1(BRANCH)
# VW1(Br)
ITEM_PATTERN = re.compile(
    r"\b(?:CT|TC)\.?\s*"
    r"([A-Z]{1,}\d+)\s*"
    r"\(\s*([A-Z0-9]+)\s*\)",
    re.IGNORECASE,
)

# Used only if an Item label has no CT./TC. prefix.
FALLBACK_ITEM_PATTERN = re.compile(
    r"\b([A-Z]{2,}\d+)\s*"
    r"\(\s*([A-Z0-9]+)\s*\)",
    re.IGNORECASE,
)

# Revision examples:
# KA1
# KA2
# KB1
# KX1
REVISION_VALUE_PATTERN = re.compile(
    r"\b[A-Z]{1,3}\d{1,3}\b",
    re.IGNORECASE,
)

# Title-block labels.
DRAWING_NUMBER_LABEL_PATTERN = re.compile(
    r"\b(?:DRAWING\s*(?:NO\.?|NUMBER)?|"
    r"DWG\.?\s*NO\.?|"
    r"DRAWING\s*NR\.?|"
    r"DOC(?:UMENT)?\s*NO\.?)\b",
    re.IGNORECASE,
)

REVISION_LABEL_PATTERN = re.compile(
    r"\b(?:REV\.?|REVISION)\b",
    re.IGNORECASE,
)


def write_log(
    log_callback,
    message: str,
    level: str = "INFO",
):
    """
    Write messages to the Streamlit log panel if supplied.
    """
    if log_callback is not None:
        log_callback(
            message,
            level,
        )


def normalise_drawing_number(
    drawing_number: str,
) -> str:
    """
    Return an uppercase controlled drawing number.
    """
    return drawing_number.upper().strip()


def get_location_from_drawing_number(
    drawing_number: str,
) -> str:
    """
    Derive the location from a drawing number.

    Example:

    1252/W/NAC/ALT/R32/541
             ^
             Returns NAC

    This is dynamic and does not hardcode a station/location.
    """
    parts = drawing_number.upper().split("/")

    for index, part in enumerate(parts):
        if re.fullmatch(r"R\d+", part):
            if index >= 2:
                return parts[index - 2]

    return ""


def rack_number(
    rack_name: str,
) -> int:
    """
    Convert V0, V1, V2, V10 etc. to a number.
    """
    match = re.search(
        r"(\d+)$",
        rack_name,
    )

    if not match:
        return 999999

    return int(match.group(1))


def get_bottom_title_block_words(
    page,
    bottom_ratio: float = 0.18,
) -> list[dict]:
    """
    Return words in the bottom title-block area.

    The controlled Drawing No. and Rev. are expected in this area.

    bottom_ratio=0.18 means only the bottom 18 percent of the page
    is searched. If some drawings have a taller title block, change
    this value to 0.22 or 0.25.
    """
    page_height = float(page.rect.height)
    page_width = float(page.rect.width)

    title_block_top = page_height * (
        1 - bottom_ratio
    )

    title_block_words = []

    for word in page.get_text(
        "words",
        sort=True,
    ):
        x0, y0, x1, y1, value = word[:5]

        y_center = (y0 + y1) / 2

        if y_center < title_block_top:
            continue

        title_block_words.append(
            {
                "value": value.strip(
                    " ,;:()[]{}"
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
                "page_width": page_width,
                "page_height": page_height,
            }
        )

    return title_block_words


def join_title_block_words(
    title_block_words: list[dict],
) -> str:
    """
    Join words from the bottom title block into searchable text.
    """
    return " ".join(
        word["value"]
        for word in title_block_words
    )


def get_drawing_number_from_title_block(
    title_block_words: list[dict],
) -> str:
    """
    Get the controlled Drawing No. only from the bottom title block.

    Example:
    1252/W/NAC/ALT/R32/541
    """
    title_block_text = join_title_block_words(
        title_block_words
    )

    matches = list(
        DRAWING_PATTERN.finditer(
            title_block_text
        )
    )

    if not matches:
        return ""

    # Usually there is one controlled drawing number in the title block.
    # If there are several, select the last occurrence.
    return matches[-1].group(0).upper()


def is_revision_value(
    value: str,
) -> bool:
    """
    Check whether a word looks like a revision value.

    Examples:
    KA1
    KA2
    KB1

    This only validates the pattern. The actual selection is controlled
    by location next to the REV / REVISION title-block label.
    """
    cleaned_value = value.strip(
        " ,;:()[]{}"
    ).upper()

    return bool(
        re.fullmatch(
            r"[A-Z]{1,3}\d{1,3}",
            cleaned_value,
        )
    )


def get_revision_from_title_block(
    title_block_words: list[dict],
) -> str:
    """
    Extract Drawing Revision from the value next to the REV label.

    Expected title-block layout:

    DRAWING NO. 1252/W/NAC/ALT/R32/541 | REV. KA1

    This function does NOT simply select a code from the right side
    of the title block. It specifically finds:

        REV / REV. / REVISION
            ↓
        nearest revision value to the right on the same row

    This prevents BU1, BV1, BW1, KX1, KY1 etc. from being used as
    Drawing Revision.
    """
    if not title_block_words:
        return "REVIEW"

    revision_labels = []

    for word in title_block_words:
        cleaned_value = word["value"].strip(
            " ,;:()[]{}"
        ).upper()

        if re.fullmatch(
            r"REV\.?",
            cleaned_value,
        ):
            revision_labels.append(word)
            continue

        if cleaned_value == "REVISION":
            revision_labels.append(word)

    if not revision_labels:
        return "REVIEW"

    candidates = []

    for revision_label in revision_labels:
        label_x1 = revision_label["x1"]
        label_y_center = revision_label["y_center"]

        # Find values directly to the right of REV.
        # The tolerance is based on text-row geometry, not Item names.
        for word in title_block_words:
            cleaned_value = word["value"].strip(
                " ,;:()[]{}"
            ).upper()

            if not is_revision_value(cleaned_value):
                continue

            # Revision value must be positioned to the right of REV.
            if word["x_center"] <= label_x1:
                continue

            vertical_distance = abs(
                word["y_center"]
                - label_y_center
            )

            # Normally the revision is on the same title-block row.
            # A slightly larger tolerance supports different PDF fonts.
            if vertical_distance > 18:
                continue

            horizontal_distance = (
                word["x0"] - label_x1
            )

            candidates.append(
                {
                    "value": cleaned_value,
                    "horizontal_distance": (
                        horizontal_distance
                    ),
                    "vertical_distance": (
                        vertical_distance
                    ),
                    "score": (
                        horizontal_distance
                        + (vertical_distance * 5)
                    ),
                }
            )

    if candidates:
        candidates = sorted(
            candidates,
            key=lambda candidate: candidate["score"],
        )

        return candidates[0]["value"]

    # Fallback:
    # Search in a small rectangle immediately to the right and slightly
    # below the REV label. This supports title blocks where KA1 is printed
    # below the REV heading rather than on the exact same row.
    fallback_candidates = []

    for revision_label in revision_labels:
        label_x1 = revision_label["x1"]
        label_y_center = revision_label["y_center"]

        for word in title_block_words:
            cleaned_value = word["value"].strip(
                " ,;:()[]{}"
            ).upper()

            if not is_revision_value(cleaned_value):
                continue

            horizontal_distance = (
                word["x_center"] - label_x1
            )

            vertical_distance = abs(
                word["y_center"]
                - label_y_center
            )

            # Restrict fallback to the revision cell area:
            # right of label, not too far away, near same row.
            if horizontal_distance < 0:
                continue

            if horizontal_distance > 100:
                continue

            if vertical_distance > 45:
                continue

            fallback_candidates.append(
                {
                    "value": cleaned_value,
                    "score": (
                        horizontal_distance
                        + (vertical_distance * 5)
                    ),
                }
            )

    if fallback_candidates:
        fallback_candidates = sorted(
            fallback_candidates,
            key=lambda candidate: candidate["score"],
        )

        return fallback_candidates[0]["value"]

    return "REVIEW"

def get_title_block_metadata(
    page,
    log_callback=None,
) -> tuple[str, str]:
    """
    Get controlled Drawing No. and Drawing Rev. strictly from the
    bottom title block.

    The main drawing area is intentionally ignored for this metadata,
    because it can contain cross-references to other drawings and Item
    labels that look like revision codes.
    """
    title_block_words = get_bottom_title_block_words(
        page,
        bottom_ratio=0.18,
    )

    title_block_text = join_title_block_words(
        title_block_words
    )

    drawing_number = get_drawing_number_from_title_block(
        title_block_words
    )

    revision = get_revision_from_title_block(
        title_block_words
    )

    write_log(
        log_callback,
        "Bottom title-block extraction: "
        f"drawing={drawing_number or 'NOT FOUND'}; "
        f"revision={revision}; "
        f"words={len(title_block_words)}; "
        f"text={title_block_text[:300]}",
        "DEBUG",
    )

    return drawing_number, revision


def remove_duplicate_terminals(
    terminals: list[dict],
) -> list[dict]:
    """
    Remove repeated terminal labels caused by duplicate PDF text layers.
    """
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
    """
    Extract terminal labels and PDF coordinates.

    Example terminal result:

    {
        "terminal": "V1.D87",
        "rack": "V1",
        "terminal_column": "D",
        "terminal_number": 87,
        "x_center": 123.4,
        "y_center": 567.8,
    }
    """
    terminals = []

    for word in page.get_text(
        "words",
        sort=True,
    ):
        x0, y0, x1, y1, value = word[:5]

        cleaned_value = value.strip(
            " ,;:()[]{}"
        )

        match = TERMINAL_PATTERN.fullmatch(
            cleaned_value
        )

        if not match:
            continue

        terminals.append(
            {
                "terminal": cleaned_value.upper(),
                "rack": match.group(1).upper(),
                "terminal_column": (
                    match.group(2).upper()
                ),
                "terminal_number": int(
                    match.group(3)
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
            }
        )

    return remove_duplicate_terminals(
        terminals
    )


def extract_item_labels(
    page,
) -> list[dict]:
    """
    Extract Item labels and their positions.

    Examples:
    CT.BW1(UP) -> BW1(UP)
    TC.BW1(REP) -> BW1(REP)
    KP1(DN) -> KP1(DN)
    """
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

            rectangles = page.search_for(
                source_text,
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
                            )
                            / 2,
                            2,
                        ),
                        "y_center": round(
                            (
                                rectangle.y0
                                + rectangle.y1
                            )
                            / 2,
                            2,
                        ),
                    }
                )

        # Do not apply the looser fallback if the proper Item pattern
        # has already found Item labels.
        if item_labels:
            break

    return item_labels


def pair_terminals_by_rack(
    terminals: list[dict],
) -> list[dict]:
    """
    Pair consecutive terminal numbers in the same cable rack and
    terminal column.

    Example:
    V1.D87 + V1.D88 -> V1.D87 & V1.D88
    """
    terminals_by_block = defaultdict(list)

    for terminal in terminals:
        block_key = (
            terminal["rack"],
            terminal["terminal_column"],
        )

        terminals_by_block[block_key].append(
            terminal
        )

    terminal_pairs = []

    for _, block_terminals in terminals_by_block.items():
        ordered_terminals = sorted(
            block_terminals,
            key=lambda terminal: (
                terminal["terminal_number"],
                terminal["y_center"],
            ),
        )

        index = 0

        while index < len(ordered_terminals):
            first_terminal = ordered_terminals[index]

            if index + 1 >= len(ordered_terminals):
                terminal_pairs.append(
                    {
                        "text": (
                            f"{first_terminal['terminal']} "
                            "(UNPAIRED - REVIEW)"
                        ),
                        "rack": first_terminal["rack"],
                        "column": first_terminal[
                            "terminal_column"
                        ],
                        "x_center": first_terminal[
                            "x_center"
                        ],
                        "y_center": first_terminal[
                            "y_center"
                        ],
                        "review_required": True,
                    }
                )

                index += 1
                continue

            second_terminal = ordered_terminals[
                index + 1
            ]

            consecutive_numbers = (
                second_terminal["terminal_number"]
                == first_terminal["terminal_number"] + 1
            )

            if consecutive_numbers:
                terminal_pairs.append(
                    {
                        "text": (
                            f"{first_terminal['terminal']} & "
                            f"{second_terminal['terminal']}"
                        ),
                        "rack": first_terminal["rack"],
                        "column": first_terminal[
                            "terminal_column"
                        ],
                        "x_center": round(
                            (
                                first_terminal["x_center"]
                                + second_terminal[
                                    "x_center"
                                ]
                            )
                            / 2,
                            2,
                        ),
                        "y_center": round(
                            (
                                first_terminal["y_center"]
                                + second_terminal[
                                    "y_center"
                                ]
                            )
                            / 2,
                            2,
                        ),
                        "review_required": False,
                    }
                )

                index += 2
            else:
                terminal_pairs.append(
                    {
                        "text": (
                            f"{first_terminal['terminal']} "
                            "(UNPAIRED - REVIEW)"
                        ),
                        "rack": first_terminal["rack"],
                        "column": first_terminal[
                            "terminal_column"
                        ],
                        "x_center": first_terminal[
                            "x_center"
                        ],
                        "y_center": first_terminal[
                            "y_center"
                        ],
                        "review_required": True,
                    }
                )

                index += 1

    return terminal_pairs


def format_pairs(
    pairs: list[dict],
) -> str:
    """
    Format terminal pairs into an Excel-cell multiline value.
    """
    if not pairs:
        return ""

    return "\n".join(
        f"- {pair['text']}"
        for pair in pairs
    )


def find_nearest_item(
    source_pair: dict,
    item_labels: list[dict],
) -> str:
    """
    Select the Item label closest to a Step 6 source pair.

    Vertical position is weighted more strongly because link groups
    normally follow a horizontal drawing lane.
    """
    if not item_labels:
        return "REVIEW REQUIRED"

    selected_item = "REVIEW REQUIRED"
    shortest_distance = None

    for item_label in item_labels:
        horizontal_distance = abs(
            source_pair["x_center"]
            - item_label["x_center"]
        )

        vertical_distance = abs(
            source_pair["y_center"]
            - item_label["y_center"]
        )

        weighted_distance = (
            horizontal_distance
            + (vertical_distance * 3)
        )

        if (
            shortest_distance is None
            or weighted_distance < shortest_distance
        ):
            shortest_distance = weighted_distance
            selected_item = item_label["item"]

    return selected_item


def make_balanced_capacities(
    source_count: int,
    destination_count: int,
) -> list[int]:
    """
    Calculate the target number of destination pairs for each source pair.

    Examples:
    2 source pairs / 4 destination pairs -> [2, 2]
    3 source pairs / 6 destination pairs -> [2, 2, 2]
    2 source pairs / 5 destination pairs -> [3, 2]
    """
    if source_count <= 0:
        return []

    base_capacity = destination_count // source_count
    remainder = destination_count % source_count

    capacities = []

    for index in range(source_count):
        capacity = base_capacity

        if index < remainder:
            capacity += 1

        capacities.append(capacity)

    return capacities


def assign_destination_pairs(
    source_pairs: list[dict],
    destination_pairs: list[dict],
) -> dict[int, list[dict]]:
    """
    Assign every destination terminal pair to only one source pair.

    Assignment is based on nearest vertical drawing lane, while using
    balanced capacities. This is what prevents all destination pairs
    being copied into every Item row.
    """
    assignments = {
        source_index: []
        for source_index in range(
            len(source_pairs)
        )
    }

    capacities = make_balanced_capacities(
        len(source_pairs),
        len(destination_pairs),
    )

    available_capacity = {
        source_index: capacities[source_index]
        for source_index in range(
            len(source_pairs)
        )
    }

    candidate_matches = []

    for destination_index, destination_pair in enumerate(
        destination_pairs
    ):
        for source_index, source_pair in enumerate(
            source_pairs
        ):
            vertical_distance = abs(
                destination_pair["y_center"]
                - source_pair["y_center"]
            )

            candidate_matches.append(
                (
                    vertical_distance,
                    destination_index,
                    source_index,
                )
            )

    candidate_matches.sort(
        key=lambda value: value[0]
    )

    assigned_destinations = set()

    for (
        _,
        destination_index,
        source_index,
    ) in candidate_matches:
        if destination_index in assigned_destinations:
            continue

        if available_capacity[source_index] <= 0:
            continue

        assignments[source_index].append(
            destination_pairs[destination_index]
        )

        assigned_destinations.add(
            destination_index
        )

        available_capacity[source_index] -= 1

    # Assign any remaining unusual/unbalanced destination pair to
    # the closest source pair.
    for destination_index, destination_pair in enumerate(
        destination_pairs
    ):
        if destination_index in assigned_destinations:
            continue

        nearest_source_index = min(
            range(len(source_pairs)),
            key=lambda source_index: abs(
                source_pairs[source_index]["y_center"]
                - destination_pair["y_center"]
            ),
        )

        assignments[nearest_source_index].append(
            destination_pair
        )

    return assignments


def create_ct_loop_rows(
    terminals: list[dict],
    item_labels: list[dict],
    log_callback=None,
) -> list[dict]:
    """
    Create CT-loop checklist candidate rows.

    Generic logic:
    - Lowest-numbered V rack is Step 6 source rack.
    - Highest-numbered V rack is Step 7/14 destination rack.
    - Every source terminal pair becomes one separate row.
    - Destination pairs are allocated to one source row only.
    - Step 14 uses the right-most Step 7 destination pair.

    No station name, Item name, rack ID, or terminal number is hardcoded.
    """
    terminals_by_rack = defaultdict(list)

    for terminal in terminals:
        terminals_by_rack[
            terminal["rack"]
        ].append(terminal)

    rack_names = sorted(
        terminals_by_rack.keys(),
        key=rack_number,
    )

    if len(rack_names) < 2:
        write_log(
            log_callback,
            "Less than two cable racks found; "
            "cannot create CT-loop row.",
            "WARNING",
        )
        return []

    source_rack = rack_names[0]
    destination_rack = rack_names[-1]

    source_pairs = pair_terminals_by_rack(
        terminals_by_rack[source_rack]
    )

    destination_pairs = pair_terminals_by_rack(
        terminals_by_rack[destination_rack]
    )

    if not source_pairs:
        write_log(
            log_callback,
            f"No source pairs created in rack "
            f"{source_rack}.",
            "WARNING",
        )
        return []

    if not destination_pairs:
        write_log(
            log_callback,
            f"No destination pairs created in rack "
            f"{destination_rack}.",
            "WARNING",
        )
        return []

    source_pairs = sorted(
        source_pairs,
        key=lambda pair: pair["y_center"],
    )

    destination_assignments = assign_destination_pairs(
        source_pairs,
        destination_pairs,
    )

    write_log(
        log_callback,
        "Lane allocation: "
        f"source rack={source_rack}; "
        f"destination rack={destination_rack}; "
        f"source pairs={len(source_pairs)}; "
        f"destination pairs={len(destination_pairs)}.",
        "DEBUG",
    )

    rows = []

    for source_index, source_pair in enumerate(
        source_pairs
    ):
        linked_destination_pairs = (
            destination_assignments[source_index]
        )

        # Right-most destination terminal group first.
        linked_destination_pairs = sorted(
            linked_destination_pairs,
            key=lambda pair: pair["x_center"],
            reverse=True,
        )

        item_name = find_nearest_item(
            source_pair,
            item_labels,
        )

        step_14_pairs = []

        if linked_destination_pairs:
            step_14_pairs = [
                linked_destination_pairs[0]
            ]

        review_required = (
            item_name == "REVIEW REQUIRED"
            or source_pair["review_required"]
            or not linked_destination_pairs
            or any(
                pair["review_required"]
                for pair in linked_destination_pairs
            )
        )

        write_log(
            log_callback,
            f"Row candidate: Item={item_name}; "
            f"Step 6={source_pair['text']}; "
            f"Step 7="
            f"{[pair['text'] for pair in linked_destination_pairs]}; "
            f"Step 14="
            f"{[pair['text'] for pair in step_14_pairs]}.",
            "DEBUG",
        )

        rows.append(
            {
                "Item": item_name,
                "G1 - Step 6": format_pairs(
                    [source_pair]
                ),
                "G1 - Step 7": format_pairs(
                    linked_destination_pairs
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
                    "Candidate created by terminal-pair and "
                    "drawing-lane grouping. Verify against "
                    "controlled drawing."
                ),
            }
        )

    return rows


def page_is_relevant(
    page_text: str,
) -> bool:
    """
    Process pages that contain at least:
    - one cable-rack terminal reference; and
    - one Item label.
    """
    has_terminal = bool(
        TERMINAL_PATTERN.search(page_text)
    )

    has_item = bool(
        ITEM_PATTERN.search(page_text)
        or FALLBACK_ITEM_PATTERN.search(page_text)
    )

    return has_terminal and has_item


def create_debug_image(
    page,
    terminals: list[dict],
) -> bytes:
    """
    Create a diagnostic image.

    Red boxes identify terminal references detected by the
    CT Loop extraction process.

    The page exists only in memory while the PDF is processed,
    so drawing these boxes does not modify the uploaded PDF.
    """

    red_colour = (
        1.0,
        0.0,
        0.0,
    )

    box_padding = 2.5

    shape = page.new_shape()

    for terminal in terminals:
        x0 = max(
            0,
            terminal["x0"] - box_padding,
        )

        y0 = max(
            0,
            terminal["y0"] - box_padding,
        )

        x1 = min(
            page.rect.width,
            terminal["x1"] + box_padding,
        )

        y1 = min(
            page.rect.height,
            terminal["y1"] + box_padding,
        )

        terminal_box = fitz.Rect(
            x0,
            y0,
            x1,
            y1,
        )

        shape.draw_rect(terminal_box)

    # Draw all boxes in a single commit for better performance.
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


def extract_pdf(
    pdf_path: str | Path,
    log_callback=None,
    progress_callback=None,
) -> tuple[pd.DataFrame, dict[int, bytes]]:
    """
    Extract CT-loop checklist candidate rows from a controlled PDF.

    Returns:
        DataFrame,
        {pdf_page_number: PNG_debug_image}
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
            f"Total pages: {total_pages}",
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

                if not page_is_relevant(page_text):
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no terminal/item pattern.",
                        "DEBUG",
                    )
                    continue

                (
                    drawing_number,
                    drawing_revision,
                ) = get_title_block_metadata(
                    page,
                    log_callback=log_callback,
                )

                if not drawing_number:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "Drawing No. not found in bottom "
                        "title block.",
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

                if not terminals:
                    write_log(
                        log_callback,
                        f"Page {page_number}: skipped; "
                        "no terminal references found.",
                        "WARNING",
                    )
                    continue

                if not item_labels:
                    write_log(
                        log_callback,
                        f"Page {page_number}: no Item labels "
                        "found; Item will be REVIEW REQUIRED.",
                        "WARNING",
                    )

                ct_loop_rows = create_ct_loop_rows(
                    terminals,
                    item_labels,
                    log_callback=log_callback,
                )
                write_log(
                    log_callback,
                    f"Page {page_number}: creating debug image with "
                    f"{len(terminals)} red terminal-reference boxes.",
                    "DEBUG",
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
                    f"{len(ct_loop_rows)} candidate row(s) "
                    f"in {page_elapsed:.2f}s.",
                )

            except Exception as error:
                write_log(
                    log_callback,
                    f"Page {page_number}: ERROR: "
                    f"{str(error)}",
                    "ERROR",
                )

    dataframe = pd.DataFrame(records)

    if not dataframe.empty:
        # Sort by final numeric part of Drawing No. Check list.
        # PDF Page is intentionally NOT used for sorting.
        dataframe["_drawing_sort"] = (
            dataframe["Drawing No. Check list"]
            .astype(str)
            .str.extract(r"/(\d+)\s*$")[0]
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
