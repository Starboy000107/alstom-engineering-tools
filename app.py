import streamlit as st

from master_drawing_list_app import (
    render_master_drawing_list_app,
)
from ct_loop_app import (
    render_ct_loop_extractor_app,
)


st.set_page_config(
    page_title="Alstom Engineering Tools",
    page_icon="🚆",
    layout="wide",
)


def main():
    st.sidebar.title("Alstom Engineering Tools")

    selected_tool = st.sidebar.radio(
        "Select tool",
        options=[
            "Master Drawing List Generator",
            "CT Loop Drawing Extractor",
        ],
        key="engineering_tool_selector",
    )

    if selected_tool == "Master Drawing List Generator":
        render_master_drawing_list_app()

    elif selected_tool == "CT Loop Drawing Extractor":
        render_ct_loop_extractor_app()


if __name__ == "__main__":
    main()