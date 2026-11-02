from pathlib import Path

import streamlit as st

from drawing_extractor_app import (
    render_drawing_extractor_app,
)
from master_drawing_list_app import (
    render_master_drawing_list_app,
)
from ui_theme import render_global_theme


ICON_PATH = (
    Path(__file__).parent
    / "img"
    / "page_icon.png"
)


st.set_page_config(
    page_title="Alstom Engineering Tools",
    page_icon=(
        str(ICON_PATH)
        if ICON_PATH.exists()
        else "📘"
    ),
    layout="wide",
)


def main():
    st.sidebar.title("Alstom Engineering Tools")

    selected_theme = st.sidebar.selectbox(
        "Appearance",
        options=[
            "Light",
            "Dark",
        ],
        index=0,
        key="global_theme_mode",
    )

    st.sidebar.markdown("---")

    selected_tool = st.sidebar.radio(
        "Select tool",
        options=[
            "Master Drawing List Generator",
            "Drawing Extractor",
        ],
        key="engineering_tool_selector",
    )

    st.sidebar.markdown("---")

    st.sidebar.caption(
        "Alstom Engineering Tools Platform"
    )

    # --------------------------------------------------------
    # Apply theme BEFORE rendering a tool.
    #
    # This is important for long processes such as PDF
    # extraction. The Light/Dark theme is already active
    # while the progress bar is updating.
    # --------------------------------------------------------
    render_global_theme(selected_theme)

    if selected_tool == "Master Drawing List Generator":
        render_master_drawing_list_app()

    elif selected_tool == "Drawing Extractor":
        render_drawing_extractor_app()

    # --------------------------------------------------------
    # Apply it once more after the tool.
    #
    # The Master Drawing List has its own CSS. Applying the
    # global theme again ensures Light/Dark mode overrides
    # its hard-coded colours after it is rendered.
    # --------------------------------------------------------
    render_global_theme(selected_theme)


if __name__ == "__main__":
    main()
