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

            "info_background": "#202B3B",
            "info_border": "#8AB4F8",
            "info_text": "#D2E3FC",
            "info_icon": "#8AB4F8",

            "success_background": "#1E3A2A",
            "success_border": "#81C995",
            "success_text": "#E6F4EA",
            "success_icon": "#81C995",

            "warning_background": "#4A3B16",
            "warning_border": "#FDD663",
            "warning_text": "#FFF4CE",
            "warning_icon": "#FDD663",

            "error_background": "#4A1F24",
            "error_border": "#F28B82",
            "error_text": "#FCE8E6",
            "error_icon": "#F28B82",
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
        # Professional engineering / Alstom-style colours
        "border_hover": "#0064A6",

        # Main action buttons
        "blue": "#0064A6",
        "blue_dark": "#004B7A",
        "button_hover": "#004B7A",

        # Supporting colours
        "teal": "#007C83",
        "green": "#2E7D32",
        "download_hover": "#1B5E20",

        # Focus / hover shadow
        "focus_shadow": "rgba(0, 100, 166, 0.20)",

        "info_background": "#E8F0FE",
        "info_border": "#AECBFA",
        "info_text": "#174EA6",
        "info_icon": "#1A73E8",

        "success_background": "#E6F4EA",
        "success_border": "#188038",
        "success_text": "#137333",
        "success_icon": "#188038",

        "warning_background": "#FEF7E0",
        "warning_border": "#F9AB00",
        "warning_text": "#7A4F01",
        "warning_icon": "#E37400",

        "error_background": "#FCE8E6",
        "error_border": "#D93025",
        "error_text": "#A50E0E",
        "error_icon": "#D93025",
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

        .stApp {{
            color: {colours["text"]} !important;
        }}

        .stApp > div {{
            color: {colours["text"]};
        }}

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
        SIDEBAR RADIO / TOOL SELECTOR
        Overrides black radio text from Master Drawing List CSS.
        ==================================================== */

        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        label,
        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        label p,
        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        label span,
        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        div[role="radiogroup"]
        label,
        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        div[role="radiogroup"]
        label p,
        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        div[role="radiogroup"]
        label span {{
            color: {colours["text"]} !important;
        }}

        /* Appearance selectbox label */
        [data-testid="stSidebar"]
        [data-testid="stSelectbox"]
        label,
        [data-testid="stSidebar"]
        [data-testid="stSelectbox"]
        label p,
        [data-testid="stSidebar"]
        [data-testid="stSelectbox"]
        label span {{
            color: {colours["text"]} !important;
        }}

        /* Sidebar selected radio indicator */
        [data-testid="stSidebar"]
        [data-testid="stRadio"]
        input[type="radio"] {{
            accent-color: {colours["blue"]} !important;
        }}

        /* Sidebar captions */
        [data-testid="stSidebar"]
        [data-testid="stCaptionContainer"],
        [data-testid="stSidebar"]
        [data-testid="stCaptionContainer"] * {{
            color: {colours["muted"]} !important;
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

        /* ====================================================
        SCROLLABLE SELECTBOX DROPDOWN MENUS
        One scrollbar only for long PDF page lists.
        ==================================================== */

        /* This is the actual BaseWeb list menu. */
        [role="listbox"] {{
            background: {colours["surface"]} !important;
            color: {colours["text"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;

            /* One scrollable container only. */
            max-height: 340px !important;
            overflow-x: hidden !important;
            overflow-y: auto !important;

            overscroll-behavior: contain !important;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18) !important;
        }}

        /* Do not make child elements independently scrollable. */
        [role="listbox"] > div {{
            max-height: none !important;
            overflow: visible !important;
        }}

        /* Chrome / Edge scrollbar styling. */
        [role="listbox"]::-webkit-scrollbar {{
            width: 10px;
        }}

        [role="listbox"]::-webkit-scrollbar-track {{
            background: {colours["surface_secondary"]} !important;
            border-radius: 999px;
        }}

        [role="listbox"]::-webkit-scrollbar-thumb {{
            background: {colours["border"]} !important;
            border: 2px solid {colours["surface_secondary"]} !important;
            border-radius: 999px;
        }}

        [role="listbox"]::-webkit-scrollbar-thumb:hover {{
            background: {colours["border_hover"]} !important;
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
        Professional engineering tool appearance
        ==================================================== */

        /* Main Streamlit buttons */
        .stButton > button {{
            min-height: 42px !important;
            padding: 0.55rem 1rem !important;

            background: {colours["blue"]} !important;
            color: #FFFFFF !important;

            border: 1px solid {colours["blue"]} !important;
            border-radius: 7px !important;

            font-size: 14px !important;
            font-weight: 650 !important;
            letter-spacing: 0.1px !important;

            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.10) !important;

            transition:
                background-color 0.16s ease,
                border-color 0.16s ease,
                box-shadow 0.16s ease,
                transform 0.10s ease !important;
        }}

        .stButton > button:hover {{
            background: {colours["button_hover"]} !important;
            border-color: {colours["button_hover"]} !important;
            color: #FFFFFF !important;

            box-shadow: 0 3px 8px {colours["focus_shadow"]} !important;
        }}

        .stButton > button:focus,
        .stButton > button:focus-visible {{
            outline: none !important;
            border-color: {colours["blue_dark"]} !important;
            box-shadow: 0 0 0 3px {colours["focus_shadow"]} !important;
        }}

        .stButton > button:active {{
            transform: translateY(1px) !important;
            box-shadow: none !important;
        }}
        /* Download buttons */
        .stDownloadButton > button {{
            min-height: 42px !important;
            padding: 0.55rem 1rem !important;

            background: {colours["green"]} !important;
            color: #FFFFFF !important;

            border: 1px solid {colours["green"]} !important;
            border-radius: 7px !important;

            font-size: 14px !important;
            font-weight: 650 !important;
            letter-spacing: 0.1px !important;

            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.10) !important;

            transition:
                background-color 0.16s ease,
                border-color 0.16s ease,
                box-shadow 0.16s ease,
                transform 0.10s ease !important;
        }}

        .stDownloadButton > button:hover {{
            background: {colours["download_hover"]} !important;
            border-color: {colours["download_hover"]} !important;
            color: #FFFFFF !important;

            box-shadow: 0 3px 8px rgba(27, 94, 32, 0.22) !important;
        }}

        .stDownloadButton > button:focus,
        .stDownloadButton > button:focus-visible {{
            outline: none !important;
            box-shadow: 0 0 0 3px rgba(46, 125, 50, 0.22) !important;
        }}

        .stDownloadButton > button:active {{
            transform: translateY(1px) !important;
            box-shadow: none !important;
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
        /* ====================================================
           ENGINEERING REVIEW / DATA EDITOR
           ==================================================== */

        /* Engineering review headings in Drawing Extractor */
        .extractor-review-heading {{
            color: {colours["navy"]} !important;
            font-size: 20px !important;
            font-weight: 700 !important;
            margin: 4px 0 14px 0 !important;
            padding-bottom: 10px !important;
            border-bottom: 2px solid {colours["border"]} !important;
        }}

        /* Main Engineering Review table container */
        [data-testid="stDataEditor"] {{
            background: {colours["surface"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px !important;
            overflow: hidden !important;

            /* Glide Data Grid theme variables */
            --gdg-bg-cell: {colours["surface"]} !important;
            --gdg-bg-cell-medium: {colours["surface_secondary"]} !important;
            --gdg-bg-header: {colours["surface_secondary"]} !important;
            --gdg-bg-header-hovered: {colours["surface_hover"]} !important;
            --gdg-bg-bubble: {colours["surface"]} !important;
            --gdg-bg-search-result: {colours["surface_selected"]} !important;
            --gdg-text-dark: {colours["text"]} !important;
            --gdg-text-medium: {colours["muted"]} !important;
            --gdg-text-light: {colours["muted"]} !important;
            --gdg-border-color: {colours["border"]} !important;
            --gdg-accent-color: {colours["blue"]} !important;
            --gdg-accent-light: {colours["surface_selected"]} !important;
            --gdg-selection-color: {colours["surface_selected"]} !important;
        }}

        /* Review table hover/focus effect */
        [data-testid="stDataEditor"]:hover {{
            border-color: {colours["border_hover"]} !important;
            box-shadow: 0 2px 10px {colours["focus_shadow"]} !important;
        }}

        /* Checkbox labels, including review filters */
        [data-testid="stCheckbox"] label,
        [data-testid="stCheckbox"] label span {{
            color: {colours["text"]} !important;
        }}

        /* Data editor toolbar buttons */
        [data-testid="stDataEditor"] button {{
            background: {colours["surface"]} !important;
            color: {colours["text"]} !important;
            border-color: {colours["border"]} !important;
        }}

        [data-testid="stDataEditor"] button:hover {{
            background: {colours["surface_hover"]} !important;
            border-color: {colours["border_hover"]} !important;
        }}
        
        [data-testid="stProgress"] > div {{
            border-radius: 999px !important;
            overflow: hidden !important;
        }}

        hr {{
            border-color: {colours["border"]} !important;
        }}

        /* ====================================================
        STREAMLIT ALERTS
        Chrome-style st.info() theme styling
        ==================================================== */

        /* Main Streamlit alert container */
        .stApp div[data-testid="stAlert"] {{
            margin-top: 0.75rem !important;
            margin-bottom: 0.75rem !important;

            background-color: {colours["info_background"]} !important;
            border: 1px solid {colours["info_border"]} !important;
            border-left: 3px solid {colours["info_icon"]} !important;

            border-radius: 8px !important;
            box-shadow: none !important;
            overflow: hidden !important;
        }}

        /* Streamlit can add an inner div with its own background */
        .stApp div[data-testid="stAlert"] > div {{
            background-color: transparent !important;
        }}

        /* IMPORTANT:
        This targets the real st.info() text container. */
        .stApp div[data-testid="stAlert"]
        [data-testid="stMarkdownContainer"],
        .stApp div[data-testid="stAlert"]
        [data-testid="stMarkdownContainer"] p,
        .stApp div[data-testid="stAlert"]
        [data-testid="stMarkdownContainer"] span,
        .stApp div[data-testid="stAlert"] p,
        .stApp div[data-testid="stAlert"] span {{
            color: {colours["info_text"]} !important;
            -webkit-text-fill-color: {colours["info_text"]} !important;
        }}

        /* Alert icon */
        .stApp div[data-testid="stAlert"] svg {{
            color: {colours["info_icon"]} !important;
            fill: {colours["info_icon"]} !important;
            stroke: {colours["info_icon"]} !important;
        }}

        /* Some Streamlit SVG icons use paths instead of SVG fill */
        .stApp div[data-testid="stAlert"] svg path {{
            color: {colours["info_icon"]} !important;
            fill: {colours["info_icon"]} !important;
            stroke: {colours["info_icon"]} !important;
        }}
        /* ====================================================
        STREAMLIT TOP HEADER / TOOLBAR
        Fix dark Streamlit Cloud top bar in Light mode
        ==================================================== */

        [data-testid="stHeader"] {{
            background: {colours["surface"]} !important;
            border-bottom: 1px solid {colours["border"]} !important;
            box-shadow: none !important;
        }}

        /* Header inner containers */
        [data-testid="stHeader"] > div,
        [data-testid="stHeader"] [data-testid="stToolbar"],
        [data-testid="stToolbar"] {{
            background: transparent !important;
        }}

        /* Streamlit decoration / top gradient line */
        [data-testid="stDecoration"] {{
            background: {colours["surface"]} !important;
        }}

        /* Header buttons: Share, GitHub, menu, sidebar toggle etc. */
        [data-testid="stHeader"] button,
        [data-testid="stToolbar"] button {{
            background: transparent !important;
            color: {colours["text"]} !important;
            border: 1px solid transparent !important;
            border-radius: 7px !important;
        }}

        /* Header button hover */
        [data-testid="stHeader"] button:hover,
        [data-testid="stToolbar"] button:hover {{
            background: {colours["surface_hover"]} !important;
            border-color: {colours["border"]} !important;
            color: {colours["blue"]} !important;
        }}

        /* Header icons */
        [data-testid="stHeader"] svg,
        [data-testid="stToolbar"] svg {{
            color: {colours["text"]} !important;
            fill: {colours["text"]} !important;
        }}

        /* Header icon hover */
        [data-testid="stHeader"] button:hover svg,
        [data-testid="stToolbar"] button:hover svg {{
            color: {colours["blue"]} !important;
            fill: {colours["blue"]} !important;
        }}

        /* Share text */
        [data-testid="stHeader"] span,
        [data-testid="stHeader"] p,
        [data-testid="stToolbar"] span,
        [data-testid="stToolbar"] p {{
            color: {colours["muted"]} !important;
        }}
        hr {{
            border-color: {colours["border"]} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
