import streamlit as st


def get_theme_colours(theme_mode: str) -> dict:
    """
    Returns colours for Light, Dark, and Night appearance modes.
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
        }

    return {
        "app_background": "#F5F7FA",
        "app_background_secondary": "#EEF4F9",
        "surface": "#FFFFFF",
        "surface_secondary": "#FBFCFE",
        "surface_input": "#FFFFFF",
        "navy": "#102A43",
        "blue": "#1769C2",
        "blue_dark": "#0F4FA8",
        "teal": "#008F95",
        "green": "#167A3D",
        "border": "#D9E2EC",
        "text": "#243B53",
        "muted": "#627D98",
    }


def render_global_theme(theme_mode: str):
    """
    Applies the global Light / Dark / Night theme.

    Important:
    Do NOT apply a background directly to [data-testid="stSelectbox"].
    That includes its label and causes the large rectangular blue block.
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
            --surface: {colours["surface"]};
            --surface-secondary: {colours["surface_secondary"]};
            --surface-input: {colours["surface_input"]};
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

        .stApp,
        .stApp p,
        .stApp span,
        .stApp label,
        .stApp h1,
        .stApp h2,
        .stApp h3 {{
            color: {colours["text"]};
        }}

        /* Keep white text in both application headers. */
        .professional-header,
        .professional-header *,
        .extractor-professional-header,
        .extractor-professional-header * {{
            color: #FFFFFF !important;
        }}

        /* Sidebar */
        [data-testid="stSidebar"] {{
            background: {colours["surface"]} !important;
            border-right: 1px solid {colours["border"]} !important;
        }}

        [data-testid="stSidebar"] * {{
            color: {colours["text"]};
        }}

        /* Application cards */
        .information-card,
        .upload-panel,
        .workflow-card,
        .extractor-information-card,
        .extractor-selection-panel,
        .extractor-upload-panel {{
            background: {colours["surface"]} !important;
            border-color: {colours["border"]} !important;
            color: {colours["text"]} !important;
        }}

        .information-card-title,
        .upload-panel-title,
        .workflow-card-title,
        .extractor-card-title {{
            color: {colours["navy"]} !important;
        }}

        .information-card-text,
        .upload-panel-description,
        .workflow-card-description,
        .extractor-card-description,
        .professional-footer,
        .extractor-footer {{
            color: {colours["muted"]} !important;
        }}

        /* ----------------------------------------------------
           Ordinary Streamlit input containers.
           IMPORTANT: stSelectbox is intentionally NOT included.
           ---------------------------------------------------- */
        [data-testid="stFileUploader"],
        [data-testid="stTextInput"],
        [data-testid="stMultiSelect"],
        [data-testid="stDataEditor"] {{
            background: {colours["surface_secondary"]} !important;
            border-color: {colours["border"]} !important;
        }}

        /* Text inputs */
        [data-baseweb="input"] > div {{
            background-color: {colours["surface_input"]} !important;
            color: {colours["text"]} !important;
            border-color: {colours["border"]} !important;
            border-radius: 14px !important;
        }}

        input,
        textarea {{
            color: {colours["text"]} !important;
            background-color: {colours["surface_input"]} !important;
        }}

        /* ----------------------------------------------------
           Rounded Selectbox.
           This styles only the actual select element and not
           the label wrapper above it.
           ---------------------------------------------------- */
        [data-testid="stSelectbox"] [data-baseweb="select"] {{
            border-radius: 18px !important;
        }}

        [data-testid="stSelectbox"] [data-baseweb="select"] > div {{
            background-color: {colours["surface_input"]} !important;
            color: {colours["text"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 18px !important;
            overflow: hidden !important;
            min-height: 46px !important;
        }}

        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        > div
        > div {{
            background-color: transparent !important;
            color: {colours["text"]} !important;
        }}

        [data-testid="stSelectbox"]
        [data-baseweb="select"]
        svg {{
            color: {colours["muted"]} !important;
        }}

        [role="listbox"] {{
            background-color: {colours["surface"]} !important;
            color: {colours["text"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 18px !important;
            overflow: hidden !important;
        }}

        [role="option"] {{
            color: {colours["text"]} !important;
        }}

        /* File upload */
        [data-testid="stFileUploader"] {{
            border-radius: 18px !important;
            overflow: hidden !important;
            padding: 12px !important;
        }}

        [data-testid="stFileUploaderDropzone"] {{
            border-radius: 14px !important;
        }}

        [data-testid="stFileUploaderFile"] {{
            border-radius: 14px !important;
        }}

        /* Expanders */
        [data-testid="stExpander"] {{
            background: {colours["surface"]} !important;
            border-color: {colours["border"]} !important;
            border-radius: 16px !important;
            overflow: hidden !important;
        }}

        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary * {{
            color: {colours["text"]} !important;
        }}

        /* Data editor */
        [data-testid="stDataEditor"] {{
            border: 1px solid {colours["border"]} !important;
            border-radius: 18px !important;
            overflow: hidden !important;
        }}

        /* Metrics */
        [data-testid="stMetric"] {{
            background: {colours["surface"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 14px !important;
        }}

        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] p {{
            color: {colours["muted"]} !important;
        }}

        [data-testid="stMetricValue"],
        [data-testid="stMetricValue"] div {{
            color: {colours["navy"]} !important;
        }}

        /* Messages */
        [data-testid="stAlert"] {{
            border-radius: 14px !important;
        }}

        /* Processing logs code box */
        pre,
        [data-testid="stCode"] {{
            background: {colours["surface_input"]} !important;
            border: 1px solid {colours["border"]} !important;
            border-radius: 14px !important;
            overflow: hidden !important;
        }}

        pre *,
        [data-testid="stCode"] * {{
            color: {colours["text"]} !important;
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
            border-radius: 14px !important;
        }}

        .stDownloadButton > button {{
            background: linear-gradient(
                135deg,
                {colours["green"]},
                #0D5A2C
            ) !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 14px !important;
        }}

        /* Progress bar */
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
