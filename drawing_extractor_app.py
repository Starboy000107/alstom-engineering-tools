import base64
import hashlib
import logging
import tempfile
import textwrap
import time
from pathlib import Path

import fitz
import streamlit as st

from exports.ct_loop_excel_export import (
    build_excel as build_ct_loop_excel,
)
from extractors.ct_loop_extractor import (
    extract_pdf as extract_ct_loop_pdf,
)


APP_NAME = "drawing_extractor"

LOG_FILE_NAME = "drawing_extractor.log"

LOG_DIRECTORY = Path(
    "/tmp/alstom_drawing_extractor_logs"
)

ICON_PATH = (
    Path(__file__).parent
    / "img"
    / "page_icon.png"
)


def configure_logging() -> logging.Logger:
    """
    Configure server-side Drawing Extractor logging.
    """

    logger = logging.getLogger(APP_NAME)

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


def get_drawing_extractor_logo_html() -> str:
    """
    Returns the shared page icon as HTML.

    Uses the same image as the Master Drawing List Generator:
        img/page_icon.png
    """

    if not ICON_PATH.exists():
        return (
            '<div class="extractor-professional-icon">'
            "📐"
            "</div>"
        )

    encoded_icon = base64.b64encode(
        ICON_PATH.read_bytes()
    ).decode("utf-8")

    return f"""
    <img
        src="data:image/png;base64,{encoded_icon}"
        class="extractor-professional-logo"
        alt="Alstom Engineering Tools"
    >
    """


def render_extractor_html(html: str):
    """
    Render HTML after removing Python indentation.

    This prevents Streamlit from displaying HTML as a code block.
    """

    content = textwrap.dedent(html).strip()

    if hasattr(st, "html"):
        st.html(content)
    else:
        st.markdown(
            content,
            unsafe_allow_html=True,
        )


def render_drawing_extractor_styles():
    """
    Drawing Extractor-specific styling.

    Global Light / Dark / Night theme styling is supplied by
    ui_theme.py after the tool page is rendered.
    """

    css = """
    <style>
    .extractor-professional-header {
        position: relative;
        overflow: hidden;
        background: linear-gradient(
            135deg,
            #102A43 0%,
            #163D5A 58%,
            #1C587D 100%
        );
        border-radius: 22px;
        padding: 30px 34px;
        margin-bottom: 26px;
        box-shadow: 0 12px 30px rgba(16,42,67,.22);
        color: white;
    }

    .extractor-professional-header-inner {
        position: relative;
        z-index: 1;
        display: flex;
        align-items: center;
        gap: 20px;
    }

    .extractor-professional-icon,
    .extractor-professional-logo {
        width: 68px;
        height: 68px;
        object-fit: contain;
        background: white;
        padding: 8px;
        border-radius: 16px;
        box-sizing: border-box;
        flex-shrink: 0;
    }

    .extractor-professional-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        color: #102A43 !important;
        font-size: 34px;
    }

    .extractor-professional-header h1 {
        margin: 0;
        color: white !important;
        font-size: 30px;
        font-weight: 750;
    }

    .extractor-professional-header p {
        margin: 8px 0 0;
        color: #D9EAF5 !important;
        font-size: 15px;
    }

    .extractor-header-tag {
        display: inline-block;
        margin-top: 15px;
        padding: 6px 12px;
        border: 1px solid rgba(255,255,255,.25);
        border-radius: 999px;
        color: #EAF5FB !important;
        background: rgba(255,255,255,.10);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: .6px;
        text-transform: uppercase;
    }

    .extractor-information-card,
    .extractor-selection-panel,
    .extractor-upload-panel {
        background: var(--surface, #FFFFFF);
        border: 1px solid var(--border, #D9E2EC);
        border-radius: 18px;
        padding: 22px 25px;
        margin-bottom: 20px;
        box-shadow: 0 8px 25px rgba(16,42,67,.08);
    }

    .extractor-information-card {
        border-top: 4px solid var(--blue, #1769C2);
    }

    .extractor-selection-panel {
        border-top: 4px solid var(--teal, #008F95);
    }

    .extractor-upload-panel {
        border-top: 4px solid var(--green, #167A3D);
    }

    .extractor-card-title {
        color: var(--navy, #102A43);
        font-size: 18px;
        font-weight: 750;
        margin-bottom: 8px;
    }

    .extractor-card-description {
        color: var(--muted, #627D98);
        font-size: 14px;
        line-height: 1.65;
    }
    .extractor-review-heading {
        color: var(--navy, #202124);
        font-size: 20px;
        font-weight: 750;
        margin-bottom: 12px;
        padding-bottom: 10px;
        border-bottom: 2px solid var(--border, #DADCE0);
    }
    .extractor-footer {
        margin-top: 45px;
        padding-top: 18px;
        border-top: 1px solid var(--border, #D9E2EC);
        color: var(--muted, #627D98);
        font-size: 12px;
        line-height: 1.7;
        text-align: center;
    }

    /* ================================================
       Rounded Streamlit controls and containers
       ================================================ */

    /* Select box: Select drawing extraction type */
    [data-testid="stSelectbox"] > div,
    [data-baseweb="select"] > div {
        border-radius: 16px !important;
    }

    [data-baseweb="select"] > div {
        overflow: hidden;
    }

    /* Dropdown menu: allow long PDF-page lists to scroll */
    [role="listbox"] {
        border-radius: 16px !important;
        overflow-x: hidden !important;
        overflow-y: auto !important;
        max-height: 360px !important;
    }

    /* File uploader */
    [data-testid="stFileUploader"] {
        border-radius: 18px !important;
        overflow: hidden;
        padding: 12px !important;
    }

    [data-testid="stFileUploaderDropzone"] {
        border-radius: 14px !important;
    }

    [data-testid="stFileUploaderFile"] {
        border-radius: 14px !important;
    }

    /* Buttons */
    .stButton > button,
    .stDownloadButton > button {
        border-radius: 14px !important;
    }

    /* Progress bar */
    [data-testid="stProgress"] > div {
        border-radius: 999px !important;
        overflow: hidden;
    }

    /* Processing logs and other expanders */
    [data-testid="stExpander"] {
        border-radius: 16px !important;
        overflow: hidden;
    }

    /* Information, warning, error and success messages */
    [data-testid="stAlert"] {
        border-radius: 14px !important;
    }

    /* Data editor */
    [data-testid="stDataEditor"] {
        border-radius: 18px !important;
        overflow: hidden;
    }

    /* Code area inside Processing logs */
    [data-testid="stCode"] {
        border-radius: 14px !important;
        overflow: hidden;
    }

    /* Text, number and text-area input controls */
    [data-testid="stTextInput"] > div,
    [data-testid="stNumberInput"] > div,
    [data-testid="stTextArea"] > div {
        border-radius: 14px !important;
        overflow: hidden;
    }
    </style>
    """

    st.markdown(
        textwrap.dedent(css),
        unsafe_allow_html=True,
    )


def calculate_sha256(file_bytes: bytes) -> str:
    """
    Return a SHA-256 checksum for the uploaded PDF.
    """

    return hashlib.sha256(file_bytes).hexdigest()


def render_pdf_page(
    pdf_bytes: bytes,
    page_number: int,
) -> bytes:
    """
    Render the selected PDF page as a PNG image.
    """

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
    """
    Return the Drawing Extractor server log file.
    """

    log_file = LOG_DIRECTORY / LOG_FILE_NAME

    if not log_file.exists():
        return b"No log file has been created yet."

    return log_file.read_bytes()


def initialize_drawing_extractor_state():
    """
    Initialise Drawing Extractor-specific Streamlit state.
    """

    defaults = {
        "drawing_extractor_checklist": None,
        "drawing_extractor_debug_images": {},
        "drawing_extractor_processing_logs": [],
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_processing_logs():
    """
    Processing logs are hidden by default.

    Logs display only when the user opens this expander.
    """

    with st.expander(
        "Processing logs",
        expanded=False,
    ):
        current_logs = "\n".join(
            st.session_state.drawing_extractor_processing_logs
        )

        if current_logs:
            st.code(
                current_logs,
                language="text",
            )
        else:
            st.info(
                "No processing logs yet."
            )

        st.download_button(
            label="Download full extraction log",
            data=get_log_file_bytes(),
            file_name="drawing_extractor.log",
            mime="text/plain",
            key="drawing_extractor_download_log",
        )


def get_extractor_configuration(extraction_type):
    """
    Return configured functions for the selected extraction type.

    Add future drawing extractor types here.
    """

    extraction_options = {
        "CT Loop": {
            "extract_function": extract_ct_loop_pdf,
            "excel_function": build_ct_loop_excel,
            "review_function": render_ct_loop_review_area,
        },
    }

    return extraction_options.get(
        extraction_type
    )


def render_drawing_extractor_app():
    """
    Main Drawing Extractor application.

    Current supported extraction type:
    - CT Loop
    """

    initialize_drawing_extractor_state()
    render_drawing_extractor_styles()

    logo_html = get_drawing_extractor_logo_html()

    render_extractor_html(
        f"""
        <div class="extractor-professional-header">
            <div class="extractor-professional-header-inner">
                {logo_html}

                <div>
                    <h1>Drawing Extractor</h1>

                    <p>
                        Engineering drawing extraction and review platform
                    </p>

                    <span class="extractor-header-tag">
                        Engineering drawing verification workflow
                    </span>
                </div>
            </div>
        </div>
        """
    )

    render_extractor_html(
        """
        <div class="extractor-information-card">
            <div class="extractor-card-title">
                📘 Engineering drawing data extraction
            </div>

            <div class="extractor-card-description">
                Select the drawing extraction type and upload a controlled
                engineering drawing PDF. All extracted information must be
                independently reviewed against the controlled drawing before
                issue, approval, or site use.
            </div>
        </div>
        """
    )

    render_extractor_html(
        """
        <div class="extractor-selection-panel">
            <div class="extractor-card-title">
                Drawing extraction type
            </div>

            <div class="extractor-card-description">
                CT Loop is currently available. Additional drawing extraction
                formats can be added to this platform later.
            </div>
        </div>
        """
    )

    available_extraction_types = [
        "CT Loop",
    ]

    extraction_type = st.selectbox(
        "Select drawing extraction type",
        options=available_extraction_types,
        key="drawing_extractor_type",
        help=(
            "Select the required drawing extraction workflow. "
            "Additional extraction types will be added later."
        ),
    )

    extractor_configuration = get_extractor_configuration(
        extraction_type
    )

    if extractor_configuration is None:
        st.error(
            "The selected extraction type is not configured."
        )
        return

    render_extractor_html(
        """
        <div class="extractor-upload-panel">
            <div class="extractor-card-title">
                Controlled Drawing Upload
            </div>

            <div class="extractor-card-description">
                Upload the approved or controlled engineering drawing in PDF
                format. The selected extraction workflow will be applied to
                the uploaded document.
            </div>
        </div>
        """
    )

    uploaded_pdf = st.file_uploader(
        "Upload controlled engineering drawing",
        type=["pdf"],
        key="drawing_extractor_uploaded_pdf",
        help=(
            "Upload a controlled engineering drawing in PDF format. "
            "The document will be processed using the selected "
            "drawing extraction workflow."
        ),
    )

    if uploaded_pdf is None:
        st.info(
            "Upload a controlled engineering drawing PDF to begin "
            "the extraction workflow."
        )
        return

    pdf_bytes = uploaded_pdf.getvalue()

    if not pdf_bytes.startswith(b"%PDF"):
        st.error(
            "The uploaded file could not be verified as a valid PDF "
            "document. Please upload a controlled engineering drawing "
            "in PDF format."
        )
        return

    document_hash = calculate_sha256(pdf_bytes)

    st.caption(
        f"File: {uploaded_pdf.name} | "
        f"SHA-256: {document_hash}"
    )

    extract_clicked = st.button(
        "Start Drawing Extraction",
        type="primary",
        key="drawing_extractor_extract_button",
    )

    if extract_clicked:
        st.session_state.drawing_extractor_processing_logs = []

        progress_bar = st.progress(
            0,
            text=(
                f"Preparing {extraction_type} extraction... 0%"
            ),
        )

        status_message = st.empty()

        def add_log(
            message: str,
            level: str = "INFO",
        ):
            """
            Store log messages only.

            No Streamlit display call exists in this function,
            so logs cannot appear during extraction.
            """

            timestamp = time.strftime("%H:%M:%S")

            formatted_message = (
                f"{timestamp} | {level} | {message}"
            )

            st.session_state.drawing_extractor_processing_logs.append(
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

        def update_progress(value: float):
            """
            Update only the visible progress bar percentage.
            """

            safe_value = max(
                0,
                min(100, int(value)),
            )

            progress_bar.progress(
                safe_value,
                text=(
                    f"Extracting {extraction_type} drawing... "
                    f"{safe_value}%"
                ),
            )

        temporary_path = None
        start_time = time.perf_counter()

        try:
            status_message.info(
                f"Extracting {extraction_type} drawing data. "
                "Please wait..."
            )

            add_log(
                f"Starting {extraction_type} extraction."
            )

            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                delete=False,
            ) as temporary_file:
                temporary_file.write(pdf_bytes)
                temporary_path = temporary_file.name

            add_log(
                f"Temporary PDF created: {temporary_path}"
            )

            extract_function = extractor_configuration[
                "extract_function"
            ]

            checklist, debug_images = extract_function(
                temporary_path,
                log_callback=add_log,
                progress_callback=update_progress,
            )

            st.session_state.drawing_extractor_checklist = (
                checklist
            )

            st.session_state.drawing_extractor_debug_images = (
                debug_images
            )

            elapsed_seconds = (
                time.perf_counter() - start_time
            )

            add_log(
                f"{extraction_type} extraction completed in "
                f"{elapsed_seconds:.2f} seconds."
            )

            update_progress(100)

            if checklist.empty:
                add_log(
                    "No candidate rows were created.",
                    level="WARNING",
                )

                status_message.warning(
                    "Extraction completed, but no candidate "
                    "rows were found."
                )

            else:
                add_log(
                    "Candidate rows created: "
                    f"{len(checklist)}"
                )

                status_message.success(
                    "Extraction completed successfully. "
                    f"{len(checklist)} candidate row(s) created."
                )

        except Exception as error:
            LOGGER.exception(
                "Unhandled drawing extraction error."
            )

            add_log(
                f"Unhandled error: {error}",
                level="ERROR",
            )

            status_message.error(
                "The extraction failed. Open Processing logs "
                "for technical details."
            )

        finally:
            if temporary_path:
                Path(temporary_path).unlink(
                    missing_ok=True,
                )

                add_log(
                    "Temporary PDF removed."
                )

    if st.session_state.drawing_extractor_processing_logs:
        st.divider()
        render_processing_logs()

    checklist = st.session_state.drawing_extractor_checklist

    if checklist is None:
        return

    if checklist.empty:
        st.error(
            "No drawing extraction candidate rows were found. "
            "Open Processing logs to review extraction details."
        )
        return

    review_function = extractor_configuration[
        "review_function"
    ]

    excel_function = extractor_configuration[
        "excel_function"
    ]

    review_function(
        pdf_bytes=pdf_bytes,
        checklist=checklist,
        extraction_type=extraction_type,
        excel_function=excel_function,
    )

    render_extractor_html(
        """
        <div class="extractor-footer">
            <strong>Drawing Extractor</strong>
            <br>
            Alstom engineering drawing extraction and review tool
            &nbsp;|&nbsp;
            Verify all extracted information against the controlled
            drawing before use.
        </div>
        """
    )


def render_ct_loop_review_area(
    pdf_bytes,
    checklist,
    extraction_type,
    excel_function,
):
    """
    CT Loop review interface.

    Future drawing types should use their own review function.
    """

    left_column, right_column = st.columns(
        [1.1, 1.5],
    )

    with left_column:
        st.markdown(
            """
            <div class="extractor-review-heading">
                Source Drawing Review
            </div>
            """,
            unsafe_allow_html=True,
        )

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
            key="drawing_extractor_selected_pdf_page",
        )

        page_image = render_pdf_page(
            pdf_bytes,
            selected_page,
        )

        st.image(
            page_image,
            caption=(
                f"Controlled {extraction_type} drawing "
                f"PDF page {selected_page}"
            ),
            use_container_width=True,
        )

        show_debug = st.checkbox(
            "Show detected terminal references",
            value=False,
            key="drawing_extractor_show_debug",
            help=(
                "Shows the source drawing with red boxes around terminal "
                "references detected during CT Loop extraction."
            ),
        )

        if show_debug:
            debug_image = (
                st.session_state.drawing_extractor_debug_images.get(
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
                        "Detection overlay: red boxes show terminal references "
                        "identified during CT Loop extraction."
                    ),
                    use_container_width=True,
                )

    with right_column:
        st.markdown(
            """
            <div class="extractor-review-heading">
                Engineering Review
            </div>
            """,
            unsafe_allow_html=True,
        )

        page_filter = st.checkbox(
            "Show selected PDF page only",
            value=True,
            key="drawing_extractor_page_filter",
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
            key="drawing_extractor_data_editor",
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
            key="drawing_extractor_save_edited_rows",
        ):
            if page_filter:
                full_data = (
                    st.session_state
                    .drawing_extractor_checklist
                    .copy()
                )

                for index in edited_data.index:
                    for column in edited_data.columns:
                        full_data.loc[index, column] = (
                            edited_data.loc[index, column]
                        )

                st.session_state.drawing_extractor_checklist = (
                    full_data
                )

            else:
                st.session_state.drawing_extractor_checklist = (
                    edited_data.copy()
                )

            st.success(
                "Edited rows saved for this session."
            )

    st.divider()

    approved_only = st.checkbox(
        "Export approved rows only",
        value=False,
        key="drawing_extractor_approved_only",
    )

    export_data = (
        st.session_state.drawing_extractor_checklist.copy()
    )

    if approved_only:
        export_data = export_data[
            export_data["Review Status"] == "Approved"
        ].copy()

    excel_bytes = excel_function(export_data)

    st.download_button(
        label=f"Download {extraction_type} Excel checklist",
        data=excel_bytes,
        file_name="CT_Loop_Drawing_Extract.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        type="primary",
        key="drawing_extractor_download_excel",
    )
