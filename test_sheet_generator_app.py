import textwrap

import streamlit as st


def render_test_sheet_generator_styles():
    """
    Test Sheet Generator-specific styling.

    Global Light/Dark styling is provided by ui_theme.py.
    """
    css = """
    <style>
    .test-sheet-header {
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

    .test-sheet-header h1 {
        margin: 0;
        color: white !important;
        font-size: 30px;
        font-weight: 750;
    }

    .test-sheet-header p {
        margin: 8px 0 0;
        color: #D9EAF5 !important;
        font-size: 15px;
    }

    .test-sheet-card {
        background: var(--surface, #FFFFFF);
        border: 1px solid var(--border, #DADCE0);
        border-radius: 18px;
        padding: 22px 25px;
        margin-bottom: 20px;
        box-shadow: 0 8px 25px rgba(16,42,67,.08);
    }

    .test-sheet-card-title {
        color: var(--navy, #202124);
        font-size: 18px;
        font-weight: 750;
        margin-bottom: 8px;
    }

    .test-sheet-card-description {
        color: var(--muted, #5F6368);
        font-size: 14px;
        line-height: 1.65;
    }
    </style>
    """

    st.markdown(
        textwrap.dedent(css),
        unsafe_allow_html=True,
    )


def render_test_sheet_generator_app():
    """
    Main Test Sheet Generator page.

    Add the test-sheet upload, configuration, Excel generation,
    PDF generation, and review workflow here.
    """
    render_test_sheet_generator_styles()

    st.markdown(
        """
        <div class="test-sheet-header">
            <h1>Test Sheet Generator</h1>
            <p>
                Generate engineering test sheets from reviewed drawing
                extraction data and approved project information.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="test-sheet-card">
            <div class="test-sheet-card-title">
                📋 Test Sheet Generation
            </div>

            <div class="test-sheet-card-description">
                Configure the test sheet source, review the selected data,
                and generate the required engineering test documentation.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.info(
        "Test Sheet Generator is ready for implementation."
    )

    st.markdown(
        """
        <div class="test-sheet-card">
            <div class="test-sheet-card-title">
                Planned workflow
            </div>

            <div class="test-sheet-card-description">
                1. Upload or select an approved checklist.<br>
                2. Select the required test sheet template.<br>
                3. Review generated rows and engineer remarks.<br>
                4. Export the completed test sheet.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
