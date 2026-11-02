import streamlit as st

from drawing_extractor_app import (
    render_drawing_extractor_app,
)
from master_drawing_list_app import (
    render_master_drawing_list_app,
)
from ui_theme import render_global_theme


st.set_page_config(
    page_title="Alstom Engineering Tools",
    page_icon="🚆",
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

    if selected_tool == "Master Drawing List Generator":
        render_master_drawing_list_app()

    elif selected_tool == "Drawing Extractor":
        render_drawing_extractor_app()

    render_global_theme(selected_theme)


if __name__ == "__main__":
    main()
