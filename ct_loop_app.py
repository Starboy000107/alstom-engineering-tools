import hashlib
import logging
import tempfile
import time
from pathlib import Path

import fitz
import streamlit as st

from excel_export import build_excel
from extractor import extract_pdf


LOG_FILE_NAME = "ct_loop_extractor.log"
LOG_DIRECTORY = Path("/tmp/alstom_ct_loop_logs")


def configure_logging() -> logging.Logger:
    """
    Configure CT-loop application logging.
    """

    logger = logging.getLogger("ct_loop_extractor")

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    LOG_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    file_handler = logging.FileHandler(
        LOG_DIRECTORY / LOG_FILE_NAME,
        encoding="utf-8",
    )

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger


LOGGER = configure_logging()


def calculate_sha256(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def render_pdf_page(
    pdf_bytes: bytes,
    page_number: int,
) -> bytes:
    with fitz.open(
        stream=pdf_bytes,
        filetype="pdf",
    ) as document:
        page_index = max(
            0,
            min(page_number - 1, len(document) - 1),
        )

        page = document[page_index]

        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(2.0, 2.0),
            alpha=False,
        )

        return pixmap.tobytes("png")


def get_log_file_bytes() -> bytes:
    log_file = LOG_DIRECTORY / LOG_FILE_NAME

    if not log_file.exists():
        return b"No log file has been created yet."

    return log_file.read_bytes()


def initialize_ct_loop_state():
    defaults = {
        "ct_checklist": None,
        "ct_debug_images": {},
        "ct_processing_logs": [],
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_ct_loop_extractor_app():
    initialize_ct_loop_state()

    st.title("CT Loop Drawing Extractor")

    st.warning(
        "This application creates candidate CT-loop checklist entries "
        "using PDF text and visible wire tracing. Every terminal reference "
        "and link must be independently verified against the controlled "
        "drawing before approval or site use."
    )

    uploaded_pdf = st.file_uploader(
        "Upload controlled CT-loop drawing PDF",
        type=["pdf"],
        key="ct_uploaded_pdf",
    )

    if uploaded_pdf is None:
        st.info("Upload a PDF to begin.")
        return

    pdf_bytes = uploaded_pdf.getvalue()

    if not pdf_bytes.startswith(b"%PDF"):
        st.error("The uploaded file is not a valid PDF.")
        return

    document_hash = calculate_sha256(pdf_bytes)

    st.caption(
        f"File: {uploaded_pdf.name} | SHA-256: {document_hash}"
    )

    if st.button(
        "Extract candidate checklist",
        type="primary",
        key="ct_extract_candidate_checklist",
    ):
        st.session_state.ct_processing_logs = []

        progress_bar = st.progress(0)
        log_placeholder = st.empty()

        def add_log(
            message: str,
            level: str = "INFO",
        ):
            timestamp = time.strftime("%H:%M:%S")

            formatted_message = (
                f"{timestamp} | {level} | {message}"
            )

            st.session_state.ct_processing_logs.append(
                formatted_message
            )

            if level == "ERROR":
                LOGGER.error(message)
            elif level == "WARNING":
                LOGGER.warning(message)
            elif level == "DEBUG":
                LOGGER.debug(message)
            else:
                LOGGER.info(message)

            log_placeholder.code(
                "\n".join(
                    st.session_state.ct_processing_logs[-100:]
                ),
                language="text",
            )

        def update_progress(value: float):
            progress_bar.progress(
                max(0, min(100, int(value)))
            )

        temporary_path = None
        start_time = time.perf_counter()

        try:
            add_log("Starting extraction process.")

            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                delete=False,
            ) as temporary_file:
                temporary_file.write(pdf_bytes)
                temporary_path = temporary_file.name

            add_log(
                f"Temporary PDF created: {temporary_path}"
            )

            checklist, debug_images = extract_pdf(
                temporary_path,
                log_callback=add_log,
                progress_callback=update_progress,
            )

            st.session_state.ct_checklist = checklist
            st.session_state.ct_debug_images = debug_images

            elapsed_seconds = (
                time.perf_counter() - start_time
            )

            add_log(
                "Extraction completed in "
                f"{elapsed_seconds:.2f} seconds."
            )

            if checklist.empty:
                add_log(
                    "No checklist rows were created.",
                    level="WARNING",
                )
            else:
                add_log(
                    "Candidate checklist rows created: "
                    f"{len(checklist)}"
                )

            update_progress(100)

        except Exception as error:
            LOGGER.exception(
                "Unhandled CT-loop extraction error."
            )

            add_log(
                f"Unhandled error: {error}",
                level="ERROR",
            )

            st.error(
                "The extraction failed. Download the log file "
                "and check the detailed error."
            )

        finally:
            if temporary_path:
                Path(temporary_path).unlink(
                    missing_ok=True
                )
                add_log("Temporary PDF removed.")

    st.divider()

    with st.expander(
        "Processing logs",
        expanded=True,
    ):
        current_logs = "\n".join(
            st.session_state.ct_processing_logs
        )

        if current_logs:
            st.code(
                current_logs,
                language="text",
            )
        else:
            st.info("No processing logs yet.")

        st.download_button(
            label="Download full CT-loop log",
            data=get_log_file_bytes(),
            file_name="ct_loop_extractor.log",
            mime="text/plain",
            key="ct_download_log",
        )

    checklist = st.session_state.ct_checklist

    if checklist is None:
        return

    if checklist.empty:
        st.error(
            "No CT-loop candidate rows were found. "
            "Check the Processing logs and debug output."
        )
        return

    render_ct_loop_review_area(
        pdf_bytes=pdf_bytes,
        checklist=checklist,
    )


def render_ct_loop_review_area(
    pdf_bytes: bytes,
    checklist,
):
    left_column, right_column = st.columns(
        [1.1, 1.5],
    )

    with left_column:
        st.subheader("Source drawing")

        available_pages = sorted(
            checklist["PDF Page"]
            .dropna()
            .astype(int)
            .unique()
            .tolist()
        )

        selected_page = st.selectbox(
            "PDF page",
            available_pages,
            key="ct_selected_pdf_page",
        )

        page_image = render_pdf_page(
            pdf_bytes,
            selected_page,
        )

        st.image(
            page_image,
            caption=f"Controlled PDF page {selected_page}",
            use_container_width=True,
        )

        show_debug = st.checkbox(
            "Show detected wire components",
            value=False,
            key="ct_show_debug",
        )

        if show_debug:
            debug_image = (
                st.session_state.ct_debug_images.get(
                    selected_page
                )
            )

            if debug_image is None:
                st.info(
                    "No debug image was generated for this page."
                )
            else:
                st.image(
                    debug_image,
                    caption=(
                        "Debug image: coloured components "
                        "and terminal text boxes."
                    ),
                    use_container_width=True,
                )

    with right_column:
        st.subheader("Engineering review")

        page_filter = st.checkbox(
            "Show selected PDF page only",
            value=True,
            key="ct_page_filter",
        )

        if page_filter:
            displayed_data = checklist[
                checklist["PDF Page"] == selected_page
            ].copy()
        else:
            displayed_data = checklist.copy()

        edited_data = st.data_editor(
            displayed_data,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="ct_data_editor",
            column_config={
                "Include": st.column_config.CheckboxColumn(
                    "Include",
                    help="Include this row in Excel output.",
                ),
                "PDF Page": st.column_config.NumberColumn(
                    "PDF Page",
                    min_value=1,
                    step=1,
                ),
                "Review Status": (
                    st.column_config.SelectboxColumn(
                        "Review Status",
                        options=[
                            "Draft",
                            "Checked",
                            "Approved",
                            "Rejected",
                            "REVIEW REQUIRED",
                        ],
                    )
                ),
            },
        )

        if st.button(
            "Save edited rows",
            key="ct_save_edited_rows",
        ):
            if page_filter:
                full_data = (
                    st.session_state.ct_checklist.copy()
                )

                for index in edited_data.index:
                    for column in edited_data.columns:
                        full_data.loc[index, column] = (
                            edited_data.loc[index, column]
                        )

                st.session_state.ct_checklist = full_data

            else:
                st.session_state.ct_checklist = (
                    edited_data.copy()
                )

            st.success(
                "Edited rows saved for this session."
            )

    st.divider()

    approved_only = st.checkbox(
        "Export approved rows only",
        value=False,
        key="ct_approved_only",
    )

    export_data = st.session_state.ct_checklist.copy()

    if approved_only:
        export_data = export_data[
            export_data["Review Status"] == "Approved"
        ].copy()

    excel_bytes = build_excel(export_data)

    st.download_button(
        label="Download CT-loop Excel checklist",
        data=excel_bytes,
        file_name="CT_Loop_Checklist.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        type="primary",
        key="ct_download_excel",
    )