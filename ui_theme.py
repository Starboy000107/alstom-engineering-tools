import streamlit as st


def get_theme_colours(theme_mode: str) -> dict:
    """
    Chrome-inspired Light and Dark application colours.
    """

    if theme_mode == "Dark":
        return {
            "app_background": "#202124",
            "app_background_secondary": "#292A2D",
            "surface": "#292A2D",
            "surface_secondary": "#303134",
            "surface_input": "#202124",
            "surface_hover": "#3C4043",
            "surface_selected": "#394457",
            "text": "#E8EAED",
            "muted": "#BDC1C6",
            "navy": "#E8EAED",
            "border": "#5F6368",
            "border_hover": "#8AB4F8",
            "blue": "#8AB4F8",
            "blue_dark": "#669DF6",
            "teal": "#78D9EC",
            "green": "#81C995",
            "button_hover": "#669DF6",
            "download_hover": "#5BB974",
            "focus_shadow": "rgba(138, 180, 248, 0.28)",
        }

    return {
        "app_background": "#F8F9FA",
        "app_background_secondary": "#FFFFFF",
        "surface": "#FFFFFF",
        "surface_secondary": "#F8F9FA",
        "surface_input": "#FFFFFF",
        "surface_hover": "#F1F3F4",
        "surface_selected": "#E8F0FE",
        "text": "#202124",
        "muted": "#5F6368",
        "navy": "#202124",
        "border": "#DADCE0",
        "border_hover": "#1A73E8",
        "blue": "#1A73E8",
        "blue_dark": "#1558B0",
        "teal": "#00897B",
        "green": "#188038",
        "button_hover": "#1558B0",
        "download_hover": "#137333",
        "focus_shadow": "rgba(26, 115, 232, 0.20)",
    }


def render_global_theme(theme_mode: str):
    """
    Applies Chrome-inspired Light/Dark styling.

    Call this after rendering the selected application:
        render_global_theme(selected_theme)
    """

    colours = get_theme_colours(theme_mode)

    st.markdown(
        f"""
        <style>
        :root {{
            --navy: {colours["navy"]};
            --blue: {colours["blue"]};
            --blue-dark: {colours["blue_dark"]};
            --teal: {colours["teal"]};
            --green: {colours["green"]};
            --border: {colours["border"]};
            --border-hover: {colours["border_hover"]};
            --text: {colours["text"]};
            --muted: {colours["muted"]};
            --surface: {colours["surface"]};
            --surface-secondary: {colours["surface_secondary"]};
            --surface-input: {colours["surface_input"]};
            --surface-hover: {colours["surface_hover"]};
            --surface-selected: {colours["surface_selected"]};
        }}

        /* ====================================================
           APPLICATION BACKGROUND
           ==================================================== */

        .stApp {{
            background: {colours["app_background"]} !important;
            color: {colours["text"]} !important;
        }}

        .stApp,
        .stApp p,
        .stApp span,
        .stApp label,
        .stApp h1,
        .stApp h2,
        .stApp h3,
        .stApp h4 {{
            color: {colours["text"]};
        }}

        /* Keep tool headers white. */
        .professional-header,
        .professional-header *,
        .extractor-professional-header,
        .extractor-professional-header * {{
            color: #FFFFFF !important;
        }}

        /* ====================================================
           SIDEBAR
           ==================================================== */

        [data-testid="stSidebar"] {{
            background: {colours["surface"]} !important;
            border-right: 1px solid {colours["border"]} !important;
        }}

        [data-testid="stSidebar"] hr {{
            border-color: {colours["border"]} !important;
        }}

        /* ====================================================
           APPLICATION CARDS
           ==================================================== */

        .information-card,
        .upload-panel,
        .workflow-card,
        .extractor-information-card,
        .extractor-selection-panel,
        .extractor-upload-panel,
        .extractor-current-type {{
            background: {colours["surface"]} !important;
            border-color: {colours["border"]} !important;
            color: {colours["text"]} !important;
        }}

        .information-card:hover,
        .upload-panel:hover,
        .workflow-card:hover,
        .extractor-information-card:hover,
        .extractor-selection-panel:hover,
        .extractor-upload-panel:hover,
        .extractor-current-type:hover {{
            border-color: {colours["border_hover"]} !important;
            box-shadow: 0 4px 14px {colours["focus_shadow"]} !important;
            transition: border-color 0.18s ease,
                        box-shadow 0.18s ease;
        }}

        .information-card-title,
        .upload-panel-title,
        .workflow-card-title,
        .extractor-card-title,
        .extractor-current-type-value {{
            color: {colours["navy"]} !important;
        }}

        .information-card-text,
        .upload-panel-description,
        .workflow-card-description,
        .extractor-card-description,
        .extractor-current-type-label,
        .extractor-current-type-note,
        .professional-footer,
        .extractor-footer {{
            color: {colours["muted"]} !important;
        }}

        /* ====================================================
           SELECT BOX
           This is used later when more than one extraction
           type becomes available.
           ==================================================== */

        [data-testid="stSelectbox"] [data-baseweb="select"] {{
            background: transparent !important;
            border-radius: 10px !important;
        }}

        [data-testid="stSelectbox"] [data-baseweb="select"] > div {{
            min-height: 42px !important;
            background: {colours["surface_input"]} !important;
            color: {colours["text"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;
            overflow: hidden !important;
            box-shadow: none !important;
            outline: none !important;
        }}

        [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover {{
            background: {colours["surface_hover"]} !important;
            border-color: {colours["border_hover"]} !important;
        }}

        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        > div:focus,
        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        > div:focus-within {{
            border-color: {colours["blue"]} !important;
            box-shadow: 0 0 0 2px {colours["focus_shadow"]} !important;
        }}

        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        > div
        > div {{
            background: transparent !important;
            color: {colours["text"]} !important;
        }}

        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        span {{
            color: {colours["text"]} !important;
        }}

        /* Remove dark/black dropdown-arrow corner. */
        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        > div
        > div:last-child {{
            background: {colours["surface_input"]} !important;
            border-left: 1px solid {colours["border"]} !important;
            border-radius: 0 9px 9px 0 !important;
        }}

        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        > div
        > div:last-child
        svg {{
            fill: {colours["muted"]} !important;
            color: {colours["muted"]} !important;
        }}

        [role="listbox"] {{
            background: {colours["surface"]} !important;
            color: {colours["text"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18) !important;
        }}

        [role="option"] {{
            background: {colours["surface"]} !important;
            color: {colours["text"]} !important;
        }}

        [role="option"]:hover,
        [role="option"][aria-selected="true"] {{
            background: {colours["surface_selected"]} !important;
        }}

        /* ====================================================
           FILE UPLOADER
           ==================================================== */

        [data-testid="stFileUploader"] {{
            background: {colours["surface_secondary"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 12px !important;
            overflow: hidden !important;
            padding: 12px !important;
        }}

        [data-testid="stFileUploader"]:hover {{
            background: {colours["surface_hover"]} !important;
            border-color: {colours["border_hover"]} !important;
        }}

        [data-testid="stFileUploaderDropzone"] {{
            background: transparent !important;
            border-radius: 8px !important;
        }}

        [data-testid="stFileUploaderFile"] {{
            background: {colours["surface"]} !important;
            border-radius: 8px !important;
        }}

        /* The file uploader Upload/Browse button. */
        [data-testid="stFileUploader"] button {{
            background: {colours["blue"]} !important;
            color: #FFFFFF !important;
            border: 1px solid {colours["blue"]} !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            opacity: 1 !important;
        }}

        [data-testid="stFileUploader"] button:hover {{
            background: {colours["button_hover"]} !important;
            border-color: {colours["button_hover"]} !important;
            color: #FFFFFF !important;
        }}

        [data-testid="stFileUploader"] button *,
        [data-testid="stFileUploader"] button svg {{
            color: #FFFFFF !important;
            fill: #FFFFFF !important;
        }}

        /* ====================================================
           BUTTONS
           ==================================================== */

        .stButton > button {{
            background: {colours["blue"]} !important;
            color: #FFFFFF !important;
            border: 1px solid {colours["blue"]} !important;
            border-radius: 8px !important;
            transition: background 0.18s ease,
                        border-color 0.18s ease,
                        box-shadow 0.18s ease,
                        transform 0.1s ease !important;
        }}

        .stButton > button:hover {{
            background: {colours["button_hover"]} !important;
            border-color: {colours["button_hover"]} !important;
            color: #FFFFFF !important;
            box-shadow: 0 2px 8px {colours["focus_shadow"]} !important;
        }}

        .stButton > button:active {{
            transform: translateY(1px);
        }}

        .stDownloadButton > button {{
            background: {colours["green"]} !important;
            color: #FFFFFF !important;
            border: 1px solid {colours["green"]} !important;
            border-radius: 8px !important;
            transition: background 0.18s ease,
                        border-color 0.18s ease,
                        box-shadow 0.18s ease,
                        transform 0.1s ease !important;
        }}

        .stDownloadButton > button:hover {{
            background: {colours["download_hover"]} !important;
            border-color: {colours["download_hover"]} !important;
            color: #FFFFFF !important;
            box-shadow: 0 2px 8px rgba(24, 128, 56, 0.25) !important;
        }}

        .stDownloadButton > button:active {{
            transform: translateY(1px);
        }}

        /* ====================================================
           TEXT / NUMBER INPUTS
           ==================================================== */

        [data-baseweb="input"] > div,
        [data-testid="stTextArea"] textarea {{
            background: {colours["surface_input"]} !important;
            color: {colours["text"]} !important;
            border-color: {colours["border"]} !important;
            border-radius: 8px !important;
        }}

        [data-baseweb="input"] > div:hover {{
            border-color: {colours["border_hover"]} !important;
        }}

        input,
        textarea {{
            background: {colours["surface_input"]} !important;
            color: {colours["text"]} !important;
        }}

        /* ====================================================
           EXPANDERS, TABLES, ALERTS, LOGS
           ==================================================== */

        [data-testid="stExpander"] {{
            background: {colours["surface"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;
            overflow: hidden !important;
        }}

        [data-testid="stExpander"]:hover {{
            border-color: {colours["border_hover"]} !important;
        }}

        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary * {{
            color: {colours["text"]} !important;
        }}

        [data-testid="stDataEditor"] {{
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;
            overflow: hidden !important;
        }}

        [data-testid="stMetric"] {{
            background: {colours["surface"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;
        }}

        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] p {{
            color: {colours["muted"]} !important;
        }}

        [data-testid="stMetricValue"],
        [data-testid="stMetricValue"] div {{
            color: {colours["navy"]} !important;
        }}

        [data-testid="stAlert"] {{
            border-radius: 8px !important;
        }}

        pre,
        [data-testid="stCode"] {{
            background: {colours["surface_input"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 8px !important;
            overflow: hidden !important;
        }}

        pre *,
        [data-testid="stCode"] * {{
            color: {colours["text"]} !important;
        }}

        [data-testid="stProgress"] > div {{
            border-radius: 999px !important;
            overflow: hidden !important;
        }}

        hr {{
            border-color: {colours["border"]} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
