import streamlit as st


def get_theme_colours(theme_mode: str) -> dict:
    """
    Returns colours for the selected application appearance.
    """

    if theme_mode == "Dark":
        return {
            "app_background": "#17212B",
            "app_background_secondary": "#1F2D3A",
            "surface": "#243B53",
            "surface_secondary": "#2C4A63",
            "surface_input": "#1E3347",
            "navy": "#EAF3FF",
            "blue": "#4EA3FF",
            "blue_dark": "#2377D1",
            "teal": "#29B6B6",
            "green": "#39A96B",
            "border": "#486581",
            "text": "#F0F4F8",
            "muted": "#BCCCDC",
            "soft_blue": "#1B3E62",
            "soft_teal": "#174C50",
            "table_row": "#2A4359",
            "warning_bg": "#4A3A17",
            "warning_text": "#FFE8A3",
        }

    if theme_mode == "Night":
        return {
            "app_background": "#080D14",
            "app_background_secondary": "#0D1621",
            "surface": "#111E2B",
            "surface_secondary": "#162838",
            "surface_input": "#0D1A27",
            "navy": "#F4F8FC",
            "blue": "#57A6FF",
            "blue_dark": "#2377D1",
            "teal": "#31C6C6",
            "green": "#42C77A",
            "border": "#314A62",
            "text": "#EDF4FA",
            "muted": "#A9BED0",
            "soft_blue": "#102F4A",
            "soft_teal": "#103F43",
            "table_row": "#162838",
            "warning_bg": "#3B3014",
            "warning_text": "#FFE7A1",
        }

    # Default Light appearance.
    return {
        "app_background": "#F5F7FA",
        "app_background_secondary": "#EEF4F9",
        "surface": "#FFFFFF",
        "surface_secondary": "#FBFCFE",
        "surface_input": "#FBFCFE",
        "navy": "#102A43",
        "blue": "#1769C2",
        "blue_dark": "#0F4FA8",
        "teal": "#008F95",
        "green": "#167A3D",
        "border": "#D9E2EC",
        "text": "#243B53",
        "muted": "#627D98",
        "soft_blue": "#EAF3FF",
        "soft_teal": "#E7F8F7",
        "table_row": "#F3F6FA",
        "warning_bg": "#FFF7E6",
        "warning_text": "#6B4D00",
    }


def render_global_theme(theme_mode: str):
    """
    Applies the selected Light / Dark / Night appearance to the
    complete Streamlit application.

    Call this AFTER rendering the selected tool so this stylesheet
    overrides tool-specific styles where needed.
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
            --text: {colours["text"]};
            --muted: {colours["muted"]};
            --soft-blue: {colours["soft_blue"]};
            --soft-teal: {colours["soft_teal"]};
            --surface: {colours["surface"]};
            --surface-secondary: {colours["surface_secondary"]};
        }}

        .stApp {{
            background: linear-gradient(
                180deg,
                {colours["app_background_secondary"]} 0px,
                {colours["app_background"]} 430px,
                {colours["app_background"]} 100%
            ) !important;
            color: {colours["text"]} !important;
        }}

        /* Main text */
        .stApp,
        .stApp p,
        .stApp span,
        .stApp label,
        .stApp div,
        .stApp h1,
        .stApp h2,
        .stApp h3 {{
            color: {colours["text"]};
        }}

        /* Preserve text colour inside professional dark headers. */
        .professional-header,
        .professional-header *,
        .ct-professional-header,
        .ct-professional-header * {{
            color: #FFFFFF !important;
        }}

        /* Streamlit sidebar */
        [data-testid="stSidebar"] {{
            background: {colours["surface"]} !important;
            border-right: 1px solid {colours["border"]};
        }}

        [data-testid="stSidebar"] * {{
            color: {colours["text"]};
        }}

        /* Cards from Master Drawing List and CT Loop */
        .information-card,
        .upload-panel,
        .workflow-card,
        .ct-information-card,
        .ct-upload-panel,
        .ct-review-card,
        .ct-log-card {{
            background: {colours["surface"]} !important;
            border-color: {colours["border"]} !important;
            color: {colours["text"]} !important;
        }}

        .information-card-title,
        .upload-panel-title,
        .workflow-card-title,
        .ct-card-title,
        .ct-section-heading {{
            color: {colours["navy"]} !important;
        }}

        .information-card-text,
        .upload-panel-description,
        .workflow-card-description,
        .ct-card-description,
        .professional-footer,
        .ct-footer {{
            color: {colours["muted"]} !important;
        }}

        /* Inputs */
        [data-testid="stFileUploader"],
        [data-testid="stTextInput"],
        [data-testid="stSelectbox"],
        [data-testid="stMultiSelect"],
        [data-testid="stDataEditor"] {{
            background: {colours["surface_secondary"]} !important;
            border-color: {colours["border"]} !important;
        }}

        [data-testid="stFileUploader"] {{
            background: {colours["surface_secondary"]} !important;
            border-color: {colours["border"]} !important;
        }}

        [data-baseweb="input"] > div,
        [data-baseweb="select"] > div {{
            background-color: {colours["surface_input"]} !important;
            color: {colours["text"]} !important;
            border-color: {colours["border"]} !important;
        }}

        input,
        textarea {{
            color: {colours["text"]} !important;
            background-color: {colours["surface_input"]} !important;
        }}

        /* Expanders */
        [data-testid="stExpander"] {{
            background: {colours["surface"]} !important;
            border-color: {colours["border"]} !important;
        }}

        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary * {{
            color: {colours["text"]} !important;
        }}

        /* Dataframes and data editor */
        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"] {{
            border: 1px solid {colours["border"]} !important;
            border-radius: 10px;
            overflow: hidden;
        }}

        /* Metrics */
        [data-testid="stMetric"] {{
            background: {colours["surface"]} !important;
            border-color: {colours["border"]} !important;
        }}

        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] p {{
            color: {colours["muted"]} !important;
        }}

        [data-testid="stMetricValue"],
        [data-testid="stMetricValue"] div {{
            color: {colours["navy"]} !important;
        }}

        /* Alerts */
        [data-testid="stAlert"] {{
            border-radius: 10px;
        }}

        /* Code and logs */
        pre,
        [data-testid="stCode"] {{
            background: {colours["surface_input"]} !important;
            border: 1px solid {colours["border"]} !important;
        }}

        pre *,
        [data-testid="stCode"] * {{
            color: {colours["text"]} !important;
        }}

        /* General separators */
        hr {{
            border-color: {colours["border"]} !important;
        }}

        /* Buttons */
        .stButton > button {{
            background: linear-gradient(
                135deg,
                {colours["blue"]},
                {colours["blue_dark"]}
            ) !important;
            color: #FFFFFF !important;
            border: none !important;
        }}

        .stDownloadButton > button {{
            background: linear-gradient(
                135deg,
                {colours["green"]},
                #0D5A2C
            ) !important;
            color: #FFFFFF !important;
            border: none !important;
        }}

        /* CT Loop primary export button */
        .ct-primary-action {{
            background: linear-gradient(
                135deg,
                {colours["blue"]},
                {colours["blue_dark"]}
            ) !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
