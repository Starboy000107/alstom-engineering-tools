import base64
import io
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib
import logging
import os
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from openpyxl import load_workbook
from docx import Document
import pandas as pd
import streamlit as st

import requests

# ============================================================
# PAGE CONFIGURATION
# ============================================================

ICON_PATH = Path(__file__).parent / "img" / "page_icon.png"
# ============================================================
# APPLICATION LOGGING
# ============================================================

APP_NAME = "alstom_master_drawing_list"

# Streamlit Cloud has an ephemeral filesystem.
# /tmp is writable and prevents writing inside the Git repository.
LOG_DIRECTORY = Path(
    os.getenv(
        "ALSTOM_LOG_DIRECTORY",
        "/tmp/alstom_master_drawing_logs",
    )
)

LOG_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True,
)

# Use one stable rotating log file for the running app.
# Every log line still contains a timestamp.
LOG_FILE = LOG_DIRECTORY / (
    f"{APP_NAME}.log"
)


class UTCFormatter(logging.Formatter):
    """
    Formats log timestamps in UTC.
    """

    converter = time.gmtime


def configure_logging():
    """
    Configures application logging only once.

    Streamlit reruns the script frequently, so handlers must
    not be added repeatedly.
    """

    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = UTCFormatter(
        fmt=(
            "%(asctime)s UTC | "
            "%(levelname)s | "
            "session=%(session_id)s | "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )

    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler(
        sys.stdout
    )

    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    logger.info(
        "Application logging started",
        extra={
            "session_id": "system",
        },
    )

    return logger


LOGGER = configure_logging()


def get_session_id():
    """
    Returns one session identifier for the current browser session.
    """

    if "application_session_id" not in st.session_state:
        st.session_state.application_session_id = (
            uuid.uuid4().hex[:12]
        )

    return st.session_state.application_session_id


def log_info(message, **kwargs):
    """
    Writes an informational log message.
    """

    LOGGER.info(
        message,
        extra={
            "session_id": get_session_id(),
            **kwargs,
        },
    )


def log_warning(message, **kwargs):
    """
    Writes a warning log message.
    """

    LOGGER.warning(
        message,
        extra={
            "session_id": get_session_id(),
            **kwargs,
        },
    )


def log_error(message, error=None, **kwargs):
    """
    Writes an error log message with an optional traceback.
    """

    LOGGER.error(
        message,
        exc_info=error is not None,
        extra={
            "session_id": get_session_id(),
            **kwargs,
        },
    )


def create_error_id():
    return (
        f"ERR-"
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid.uuid4().hex[:8].upper()}"
    )


def show_application_error(
    user_message,
    error=None,
    error_id=None,
    show_details=True,
):
    """
    Logs the technical error and displays a friendly
    message on the website.
    """

    if error_id is None:
        error_id = create_error_id()

    LOGGER.error(
        f"{user_message} | error_id={error_id}",
        exc_info=error is not None,
        extra={
            "session_id": get_session_id(),
        },
    )

    st.error(
        f"{user_message} "
        f"Reference: {error_id}"
    )

    if show_details and error is not None:
        with st.expander(
            "Technical error details",
            expanded=False,
        ):
            st.code(
                f"{type(error).__name__}: {error}"
            )

    return error_id


def read_application_log():
    """
    Returns the current application log as text.
    """

    try:
        if not LOG_FILE.exists():
            return "No application log is available."

        return LOG_FILE.read_text(
            encoding="utf-8",
            errors="replace",
        )

    except Exception as error:
        LOGGER.error(
            "Could not read application log",
            exc_info=True,
            extra={
                "session_id": get_session_id(),
            },
        )

        return (
            "The application log could not be read:\n"
            f"{type(error).__name__}: {error}"
        )

# ============================================================
# GLOBAL UNCAUGHT ERROR HANDLER
# ============================================================

def handle_uncaught_exception(
    exception_type,
    exception_value,
    exception_traceback,
):
    """
    Logs uncaught Python exceptions.

    Streamlit normally displays its own exception page, but
    this ensures that the error is also written to the log.
    """

    if issubclass(
        exception_type,
        KeyboardInterrupt,
    ):
        sys.__excepthook__(
            exception_type,
            exception_value,
            exception_traceback,
        )
        return

    LOGGER.error(
        "Uncaught application exception",
        exc_info=(
            exception_type,
            exception_value,
            exception_traceback,
        ),
        extra={
            "session_id": "unknown",
        },
    )


sys.excepthook = handle_uncaught_exception

class LatestMasterConflictError(Exception):
    """
    Raised when another user has updated the shared latest
    Master Drawing List.
    """
# ============================================================
# GITHUB LOG STORAGE
# ============================================================

def get_github_secret(name, default=""):
    """
    Safely reads a value from Streamlit Secrets.
    """

    try:
        value = st.secrets.get(name, default)

        if value is None:
            return default

        return str(value).strip()

    except Exception as error:
        LOGGER.error(
            "Could not read GitHub configuration",
            exc_info=True,
            extra={
                "session_id": get_session_id(),
            },
        )

        return default

LATEST_MASTER_PATH = get_github_secret(
    "LATEST_MASTER_PATH",
    "latest/Master_Drawing_List_latest.xlsx",
)


def get_github_api_headers():
    token = get_github_secret("GITHUB_TOKEN")

    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not configured in Streamlit Secrets."
        )

    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def get_latest_master_updated_time(
    owner,
    repository,
    branch,
    file_path,
):
    """
    Returns the latest GitHub commit time for the specified file
    formatted in Hong Kong time.
    """

    commits_url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repository}/commits"
    )

    response = requests.get(
        commits_url,
        headers=get_github_api_headers(),
        params={
            "path": file_path,
            "sha": branch,
            "per_page": 1,
        },
        timeout=30,
    )

    if response.status_code != 200:
        log_warning(
            "Could not read latest GitHub file update time",
            http_status=response.status_code,
            file_path=file_path,
        )
        return ""

    commits = response.json()

    if not commits:
        return ""

    latest_commit = commits[0]

    commit_data = (
        latest_commit.get("commit", {})
        or {}
    )

    author_data = (
        commit_data.get("author", {})
        or {}
    )

    committer_data = (
        commit_data.get("committer", {})
        or {}
    )

    # Prefer the committer date because it represents when
    # the commit was actually created in the repository.
    timestamp_text = (
        committer_data.get("date")
        or author_data.get("date")
        or ""
    )

    if not timestamp_text:
        return ""

    try:
        parsed_timestamp = datetime.fromisoformat(
            timestamp_text.replace("Z", "+00:00")
        )

        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(
                tzinfo=timezone.utc
            )

        hong_kong_time = parsed_timestamp.astimezone(
            ZoneInfo("Asia/Hong_Kong")
        )

        return hong_kong_time.strftime(
            "%Y-%m-%d %H:%M:%S HKT"
        )

    except Exception as error:
        log_warning(
            "Could not format GitHub update time",
            error_type=type(error).__name__,
            timestamp=timestamp_text,
        )

        return ""


def get_latest_master_file():
    """
    Reads the shared latest Master Drawing List from GitHub.

    Returns:
        None if the file does not exist.

        Otherwise:
        {
            "bytes": bytes,
            "github_sha": str,
            "file_sha256": str,
            "path": str,
            "html_url": str,
            "commit_sha": str,
        }
    """

    owner = get_github_secret("GITHUB_OWNER")
    repository = get_github_secret("GITHUB_REPOSITORY")
    branch = get_github_secret("GITHUB_BRANCH", "main")

    if not owner or not repository:
        raise RuntimeError(
            "GITHUB_OWNER and GITHUB_REPOSITORY must be configured."
        )

    updated_time_hkt = get_latest_master_updated_time(
        owner=owner,
        repository=repository,
        branch=branch,
        file_path=LATEST_MASTER_PATH,
    )

    api_url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repository}/contents/"
        f"{LATEST_MASTER_PATH}"
    )

    response = requests.get(
        api_url,
        headers=get_github_api_headers(),
        params={"ref": branch},
        timeout=30,
    )

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        raise RuntimeError(
            "Could not read the latest Master Drawing List. "
            f"HTTP {response.status_code}: {response.text}"
        )

    payload = response.json()

    # The Contents API normally includes base64 content for small files.
    # For larger files, use the download URL.
    encoded_content = payload.get("content", "")

    if encoded_content:
        file_bytes = base64.b64decode(
            encoded_content.replace("\n", "")
        )
    else:
        download_url = payload.get("download_url")

        if not download_url:
            raise RuntimeError(
                "GitHub did not provide content or a download URL."
            )

        download_response = requests.get(
            download_url,
            headers=get_github_api_headers(),
            timeout=60,
        )

        if download_response.status_code != 200:
            raise RuntimeError(
                "Could not download the latest Master Drawing List. "
                f"HTTP {download_response.status_code}: "
                f"{download_response.text}"
            )

        file_bytes = download_response.content

    commit_sha = (
        payload.get("commit", {}) or {}
    ).get("sha", "")

    return {
        "bytes": file_bytes,
        "github_sha": payload.get("sha", ""),
        "file_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "path": payload.get("path", LATEST_MASTER_PATH),
        "html_url": payload.get("html_url", ""),
        "commit_sha": commit_sha,
        "updated_time_hkt": updated_time_hkt,
    }


def publish_latest_master_file(
    file_bytes,
    expected_github_sha=None,
    message="Publish latest Master Drawing List",
):
    """
    Publishes the latest workbook using GitHub optimistic locking.

    expected_github_sha must be the SHA of the file that the user
    originally downloaded or used.

    If another user has updated the file, GitHub rejects the update.
    """

    owner = get_github_secret("GITHUB_OWNER")
    repository = get_github_secret("GITHUB_REPOSITORY")
    branch = get_github_secret("GITHUB_BRANCH", "main")

    if not owner or not repository:
        raise RuntimeError(
            "GITHUB_OWNER and GITHUB_REPOSITORY must be configured."
        )

    api_url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repository}/contents/"
        f"{LATEST_MASTER_PATH}"
    )

    request_body = {
        "message": message,
        "content": base64.b64encode(file_bytes).decode("utf-8"),
        "branch": branch,
    }

    # For an existing file, sha is mandatory.
    # This makes the update conditional on the expected version.
    if expected_github_sha:
        request_body["sha"] = expected_github_sha

    response = requests.put(
        api_url,
        headers=get_github_api_headers(),
        json=request_body,
        timeout=60,
    )

    # GitHub may return 409 or 422 when the file changed
    # or already exists during a concurrent create.
    if response.status_code == 409:
        raise LatestMasterConflictError(
            "Another user published a newer Master Drawing List "
            "before this workbook could be published."
        )

    if response.status_code == 422:
        try:
            error_details = response.json()
        except Exception:
            error_details = response.text

        error_text = str(error_details).lower()

        if (
            "sha" in error_text
            or "reference update failed" in error_text
            or "already exists" in error_text
        ):
            raise LatestMasterConflictError(
                "Another user published a newer Master Drawing List."
            )

        raise RuntimeError(
            "GitHub rejected the publication request. "
            f"Details: {error_details}"
        )

    if response.status_code not in {200, 201}:
        raise RuntimeError(
            "Could not publish the latest Master Drawing List. "
            f"HTTP {response.status_code}: {response.text}"
        )

    payload = response.json()

    return {
        "path": LATEST_MASTER_PATH,
        "html_url": (
            payload.get("content", {}) or {}
        ).get("html_url", ""),
        "commit_sha": (
            payload.get("commit", {}) or {}
        ).get("sha", ""),
        "github_sha": (
            payload.get("content", {}) or {}
        ).get("sha", ""),
        "file_sha256": hashlib.sha256(file_bytes).hexdigest(),
    }


def upload_log_to_github(
    log_text=None,
    session_id=None,
):
    """
    Uploads the current application log to GitHub.

    A new timestamped file is created for each upload.
    This avoids needing to retrieve and update an existing file,
    which is safer when multiple Streamlit users are active.
    """

    token = get_github_secret(
        "GITHUB_TOKEN"
    )

    owner = get_github_secret(
        "GITHUB_OWNER"
    )

    repository = get_github_secret(
        "GITHUB_REPOSITORY"
    )

    branch = get_github_secret(
        "GITHUB_BRANCH",
        "main",
    )

    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not configured in Streamlit Secrets."
        )

    if not owner:
        raise RuntimeError(
            "GITHUB_OWNER is not configured in Streamlit Secrets."
        )

    if not repository:
        raise RuntimeError(
            "GITHUB_REPOSITORY is not configured in "
            "Streamlit Secrets."
        )

    if not branch:
        branch = "main"

    if log_text is None:
        log_text = read_application_log()

    if not log_text.strip():
        raise RuntimeError(
            "The application log is empty."
        )

    if session_id is None:
        session_id = get_session_id()

    timestamp = datetime.utcnow()

    date_folder = timestamp.strftime(
        "%Y-%m-%d"
    )

    timestamp_text = timestamp.strftime(
        "%Y%m%d_%H%M%S"
    )

    remote_file_path = (
        f"logs/{date_folder}/"
        f"session_{session_id}_{timestamp_text}.log"
    )

    api_url = (
        "https://api.github.com/repos/"
        f"{owner}/{repository}/contents/"
        f"{remote_file_path}"
    )

    encoded_content = base64.b64encode(
        log_text.encode("utf-8")
    ).decode("utf-8")

    request_body = {
        "message": (
            "Add Streamlit application log "
            f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        ),
        "content": encoded_content,
        "branch": branch,
    }

    request_headers = {
        "Accept": (
            "application/vnd.github+json"
        ),
        "Authorization": (
            f"Bearer {token}"
        ),
        "X-GitHub-Api-Version": "2022-11-28",
    }

    log_info(
        "Uploading application log to GitHub",
        github_path=remote_file_path,
    )

    response = requests.put(
        api_url,
        headers=request_headers,
        json=request_body,
        timeout=30,
    )

    if response.status_code not in {
        200,
        201,
    }:
        try:
            error_details = response.json()
        except Exception:
            error_details = response.text

        raise RuntimeError(
            "GitHub log upload failed. "
            f"HTTP {response.status_code}: "
            f"{error_details}"
        )

    response_data = response.json()

    html_url = (
        response_data.get("content", {})
        .get("html_url", "")
    )


    return {
        "path": remote_file_path,
        "url": html_url,
        "status_code": response.status_code,
    }


# ============================================================
# CONFIGURATION
# ============================================================

SUPPORTED_EXCEL_EXTENSIONS = ["xlsx", "xls", "xlsm"]
SUPPORTED_TRANSMITTAL_EXTENSIONS = [
    "docx",
    "docm",
]

TRANSMITTAL_COLUMN_SUFFIX = (
    "Latest Transmittal Form Version"
)

DRAWING_NUMBER_ALIASES = [
    "DRAWING NO.",
    "DRAWING NO",
    "DRAWING NUMBER",
    "AFFECTED DRAWING",
    "C1252 DRAWING",
    "C1252 DRAWING NO",
    "C1252 DRAWING NUMBER",
    "DRAWING",
]

DRAWING_REF_ALIASES = [
    "DRAWING REF",
    "DRAWING REFERENCE",
    "RAW DRAWING REF",
]

DESCRIPTION_ALIASES = [
    "SUBJECT",
    "DESCRIPTION",
    "DRAWING DESCRIPTION",
    "DRAWING TITLE",
    "TITLE",
]
STATUS_ALIASES = [
    "STATUS",
    "+STATUS",
    "APPROVAL STATUS",
    "DOCUMENT STATUS",
]

APPROVAL_DATE_ALIASES = [
    "APPROVAL DATE",
    "DATE APPROVED",
    "APPROVED DATE",
    "APPROVAL",
    "STATUS DATE",
    "DATE",
]

PHASE_ALIASES = [
    "PHASE",
    "PROJECT PHASE",
    "MIGRATION PHASE",
]

REVISION_ALIASES = [
    "REVISION",
    "REV",
    "REVISION NO.",
    "REVISION NO",
    "REVISION NUMBER",
]

MODIFICATION_SHEET_NO_ALIASES = [
    "MODIFICATION SHEET NO.",
    "MODIFICATION SHEET NO",
    "MOD SHEET NO.",
    "MOD SHEET NO",
    "MODIFICATION NUMBER",
    "MOD NO.",
    "MOD NO",
]

DIH_GROUPS = {
    "A": {
        "name": "A2 Stage",
        "order": 10,
        "include_mod": True,
        "include_az": True,
    },
    "B": {
        "name": "A3 Stage 1",
        "order": 20,
        "include_mod": True,
        "include_az": True,
    },
    "C": {
        "name": "A4 Stage",
        "order": 30,
        "include_mod": True,
        "include_az": True,
    },
    "EA": {
        "name": "A3 Stage 2",
        "order": 40,
        "include_mod": True,
        "include_az": True,
    },
    "E": {
        "name": "A3 Stage 2",
        "order": 40,
        "include_mod": True,
        "include_az": True,
    },
    "D": {
        "name": "B4 Stage",
        "order": 50,
        "include_mod": True,
        "include_az": True,
    },
    "F": {
        "name": "B5 Stage",
        "order": 60,
        "include_mod": True,
        "include_az": True,
    },
    "G": {
        "name": "B7 Stage",
        "order": 70,
        "include_mod": True,
        "include_az": True,
    },
    "H": {
        "name": "C3 Stage",
        "order": 80,
        "include_mod": True,
        "include_az": True,
    },
    "HA": {
        "name": "C3 Stage",
        "order": 80,
        "include_mod": True,
        "include_az": True,
    },
    "JA": {
        "name": "C5 Stage 1",
        "order": 90,
        "include_mod": True,
        "include_az": True,
    },
    "JB": {
        "name": "C5 Stage 1",
        "order": 90,
        "include_mod": True,
        "include_az": True,
    },
    "JC": {
        "name": "C5 Stage 2",
        "order": 100,
        "include_mod": True,
        "include_az": True,
    },
    "JD": {
        "name": "C5 Stage 2",
        "order": 100,
        "include_mod": True,
        "include_az": True,
    },
    "JE": {
        "name": "C5 Stage 3",
        "order": 110,
        "include_mod": True,
        "include_az": True,
    },
    "JF": {
        "name": "C5 Stage 3",
        "order": 110,
        "include_mod": True,
        "include_az": True,
    },
    "JG": {
        "name": "C5 Stage 4",
        "order": 120,
        "include_mod": True,
        "include_az": True,
    },
    "KA": {
        "name": "A11 Stage",
        "order": 130,
        "include_mod": True,
        "include_az": True,
    },
    "L": {
        "name": "A7 Stage",
        "order": 140,
        "include_mod": True,
        "include_az": True,
    },
    "T": {
        "name": "C5 Stage",
        "order": 150,
        "include_mod": True,
        "include_az": True,
    },
}

STANDARD_DIH_GROUP_NAMES = {
    "A2 STAGE",
    "A3 STAGE 1",
    "A4 STAGE",
    "A3 STAGE 2",
    "B4 STAGE",
    "B5 STAGE",
    "B7 STAGE",
    "C3 STAGE",
    "C5 STAGE 1",
    "C5 STAGE 2",
    "C5 STAGE 3",
    "C5 STAGE 4",
    "A11 STAGE",
    "A7 STAGE",
    "C5 STAGE",
}

DIH_SPECIAL_CATEGORIES = {
    "CSP": {
        "order": 160,
        "keywords": ["CSP"],
    },
    "PDC Existing": {
        "order": 170,
        "keywords": ["EXISTING PDC", "PDC EXISTING"],
    },
    "PDC New": {
        "order": 180,
        "keywords": ["NEW PDC", "PDC NEW"],
    },
    "PDC Additional New": {
        "order": 190,
        "keywords": [
            "ADDITIONAL NEW PDC",
            "ADDITIONAL PDC",
        ],
    },
}

REQUIRED_PHASE_ORDER = {
    "A2 STAGE": 10,
    "A3 STAGE 1": 20,
    "A4 STAGE": 30,
    "A3 STAGE 2": 40,
    "B4 STAGE": 50,
    "B5 STAGE": 60,
    "B7 STAGE": 70,
    "C3 STAGE": 80,
    "C5 STAGE 1": 90,
    "C5 STAGE 2": 100,
    "C5 STAGE 3": 110,
    "C5 STAGE 4": 120,
    "A11 STAGE": 130,
    "A7 STAGE": 140,
    "C5 STAGE": 150,
    "CSP": 160,
    "PDC EXISTING": 170,
    "PDC NEW": 180,
    "PDC ADDITIONAL NEW": 190,
    "LIFTING BARRIER": 200,
    "UFWL": 210,
    "VARIATION ORDERS": 220,
    "WTT-RE-OPEN": 230,
}


# ============================================================
# BASIC HELPERS
# ============================================================

HKT_TIMEZONE = ZoneInfo("Asia/Hong_Kong")


def get_current_hkt():
    """
    Returns the current Hong Kong time.
    """
    return datetime.now(HKT_TIMEZONE)


def get_current_hkt_text():
    """
    Returns the current Hong Kong time as display text.
    """
    return get_current_hkt().strftime(
        "%Y-%m-%d %H:%M:%S HKT"
    )


def get_current_hkt_filename_text():
    """
    Returns the current Hong Kong time for file names.
    """
    return get_current_hkt().strftime(
        "%Y%m%d_%H%M%S"
    )

# ============================================================
# STATION WORKSHEET EXCEPTIONS
# ============================================================

STATION_WORKSHEET_EXCEPTIONS = {
    # AIR worksheet contains both AIR and AWE drawings.
    "AIR": "AIR",
    "AWE": "AIR",

    # SUN & YOT worksheet contains both SUN and YOT drawings.
    "SUN": "SUN & YOT",
    "YOT": "SUN & YOT",

    # TUC worksheet contains both TUC and YOY drawings.
    "TUC": "TUC",
    "YOY": "TUC",

    # TCE worksheet contains both TCE and TCETUC drawings.
    "TCE": "TCE",
    "TCETUC": "TCE",

    # SHD worksheet contains AB11, AB3 and MB drawings.
    "SHD": "SHD",
    "AB11": "SHD",
    "AB3": "SHD",
    "MB": "SHD",
}


def get_station_worksheet_name(station_name):
    """
    Returns the worksheet name for a drawing station.

    Exceptional grouping rules:
        AIR + AWE -> AIR
        SUN + YOT -> SUN & YOT
        TUC + YOY -> TUC
        TCE + TCETUC -> TCE
        SHD + AB11 + AB3 + MB = SHD

    All other stations keep their own worksheet name.
    """

    station = clean_value(station_name)
    station_key = normalize_header(station)

    if not station_key:
        return ""

    return STATION_WORKSHEET_EXCEPTIONS.get(
        station_key,
        station,
    )
    
def normalize_station_name(value):
    """
    Normalizes station worksheet names consistently.
    """

    text = clean_value(value)

    text = re.sub(
        r"[\[\]\:\*\?\/\\]",
        "_",
        text,
    )

    return normalize_header(text[:31])

def validate_existing_file_is_latest(
    uploaded_file,
    latest_master,
):
    """
    Ensures the uploaded existing workbook is based on the
    currently shared latest workbook.
    """

    if latest_master is None:
        return

    uploaded_bytes = uploaded_file.getvalue()

    uploaded_hash = hashlib.sha256(
        uploaded_bytes
    ).hexdigest()

    latest_hash = latest_master["file_sha256"]

    if uploaded_hash != latest_hash:
        raise LatestMasterConflictError(
            "The uploaded existing Master Drawing List is not "
            "the current shared latest version. Please download "
            "the latest workbook from the top-right of the page "
            "and use it as the starting file."
        )

def normalize_stage_text(value):
    """
    Normalizes stage text so that:
        A3 Stage-1
        A3 Stage 1
        A3 STAGE 1

    are treated consistently.
    """
    text = normalize_header(value)
    text = text.replace("-", " ")
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()

def create_modification_duplicate_key(
    drawing_number,
    modification_phase,
    modification_sheet_no,
    modification_revision,
):
    """
    A modification record is considered duplicate only when
    all of these values are the same:

        Drawing Number
        Modification Stage
        Modification Sheet No.
        Modification Revision
    """

    drawing_key = normalize_drawing_number(
        drawing_number
    )

    phase_key = normalize_stage_text(
        modification_phase
    )

    sheet_no_key = normalize_header(
        modification_sheet_no
    )

    revision_key = normalize_header(
        modification_revision
    )

    return (
        drawing_key,
        phase_key,
        sheet_no_key,
        revision_key,
    )

def get_stage_display_name(stage_key):
    """
    Converts REQUIRED_PHASE_ORDER keys into the actual
    Master Drawing List header group names.
    """

    stage_key = normalize_stage_text(stage_key)

    for group_info in DIH_GROUPS.values():
        if (
            normalize_stage_text(
                group_info["name"]
            )
            == stage_key
        ):
            return group_info["name"]

    special_stage_names = {
        "CSP": "CSP",
        "PDC EXISTING": "PDC Existing",
        "PDC NEW": "PDC New",
        "PDC ADDITIONAL NEW":
            "PDC Additional New",
        "LIFTING BARRIER": "LIFTING BARRIER",
        "UFWL": "UFWL",
        "VARIATION ORDERS": "VARIATION ORDERS",
        "WTT RE OPEN": "WTT-RE-OPEN",
    }

    return special_stage_names.get(
        stage_key,
        clean_value(stage_key),
    )


def detect_stage_from_description(description):
    """
    Detects the exact Master Drawing List group from a
    transmittal description.

    Examples:

        THW Existing PDC As Built Modification
            -> PDC Existing

        THW New PDC Modification
            -> PDC New

        A3 Stage 1 THW Drawing
            -> A3 Stage 1

    Special categories are checked first using all configured
    aliases, including:

        EXISTING PDC
        PDC EXISTING
        NEW PDC
        PDC NEW
    """

    text = normalize_stage_text(
        description
    )

    if not text:
        return ""

    # --------------------------------------------------------
    # 1. Check special categories first.
    #
    # This is important because:
    #
    #     EXISTING PDC
    #
    # must be detected as PDC Existing even though the
    # description may contain other words before it.
    # --------------------------------------------------------
    special_categories = sorted(
        DIH_SPECIAL_CATEGORIES.items(),
        key=lambda item: max(
            len(
                normalize_stage_text(keyword)
            )
            for keyword in item[1]["keywords"]
        ),
        reverse=True,
    )

    for category, config in special_categories:
        for keyword in config["keywords"]:
            normalized_keyword = (
                normalize_stage_text(keyword)
            )

            if (
                normalized_keyword
                and normalized_keyword in text
            ):
                return clean_value(category)

    # --------------------------------------------------------
    # 2. Check configured standard stage names.
    # --------------------------------------------------------
    standard_stage_keys = sorted(
        [
            stage_key
            for stage_key in REQUIRED_PHASE_ORDER
            if stage_key
            not in {
                "CSP",
                "PDC EXISTING",
                "PDC NEW",
                "PDC ADDITIONAL NEW",
            }
        ],
        key=lambda value: len(
            normalize_stage_text(value)
        ),
        reverse=True,
    )

    for stage_key in standard_stage_keys:
        normalized_stage = normalize_stage_text(
            stage_key
        )

        if (
            normalized_stage
            and normalized_stage in text
        ):
            return get_stage_display_name(
                stage_key
            )

    # --------------------------------------------------------
    # 3. Detect common short stage aliases such as:
    #
    #     B4
    #     A4
    #     A2
    #     B5
    #     B7
    #     A3 Stage 1
    #     A3 Stage 2
    # --------------------------------------------------------
    detected_stage = find_transmittal_stage(
        text
    )

    if detected_stage:
        return detected_stage

    return ""
def get_first_revision_columns(df):
    """
    Calculates the first worksheet column for each revision.

    The first occurrence is based strictly on the worksheet
    column order. This mapping is calculated once per worksheet.
    """

    first_revision_columns = {}

    if df is None or df.empty:
        return first_revision_columns

    excluded_columns = {
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
        "Normalized Drawing Number",
        "_Transmittal Scope Status",
    }

    for column in df.columns:
        if column in excluded_columns:
            continue

        if is_transmittal_column(column):
            continue

        if normalize_header(column).endswith(" MOD"):
            continue

        revision = get_subheader_revision_value(column)

        if not revision:
            continue

        if revision not in first_revision_columns:
            first_revision_columns[revision] = column

    return first_revision_columns


def render_html(content):
    if hasattr(st, "html"):
        st.html(content)
    else:
        st.markdown(
            content,
            unsafe_allow_html=True,
        )


def is_blank(value):
    if value is None:
        return True

    try:
        result = pd.isna(value)

        if isinstance(result, bool) and result:
            return True

    except Exception:
        pass

    return str(value).strip() == ""


def clean_value(value):
    if is_blank(value):
        return ""

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")

    text = str(value)
    text = text.replace("\r\n", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = text.replace("\t", " ")

    return re.sub(r"\s+", " ", text).strip()


def clean_description(value):
    if is_blank(value):
        return ""

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")

    text = str(value)
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace("\t", " ")
    text = re.sub(r"[ ]*\n[ ]*", "\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)

    return text.strip()


def normalize_header(value):
    return clean_value(value).upper()


def normalize_drawing_number(value):
    """
    Normalizes a C1252 drawing number.

    A valid C1252 drawing number has exactly six sections:

        1252 / W / STATION / ALT / RXX / NUMBER

    Example:

        1252/W/TUT/ALT/R32/105

    Any additional slash-separated text, such as a revision
    appended by a transmittal form, is excluded.

    Example:

        1252/W/TUT/ALT/R32/105/D3
            -> 1252/W/TUT/ALT/R32/105
    """

    if is_blank(value):
        return ""

    text = str(value)
    text = text.replace("_", "/")
    text = text.replace('"', "")
    text = text.replace("'", "")
    text = text.replace("\r", "")
    text = text.replace("\n", "")
    text = text.replace("\t", "")
    text = text.replace(" ", "")

    text = re.sub(r"/+", "/", text)
    text = text.strip().rstrip(
        ".,;:)]}>"
    ).upper()

    parts = text.split("/")

    # A normal C1252 drawing number contains:
    #
    # 1252/W/TUT/ALT/R32/105
    #
    # Therefore retain only the first six segments.
    if (
        len(parts) >= 6
        and parts[0] == "1252"
    ):
        return "/".join(parts[:6])

    return text


def extract_drawing_ref(value):
    value = normalize_drawing_number(value)

    if not value:
        return ""

    parts = value.split("/")

    return (
        "/".join(parts[-2:])
        if len(parts) >= 2
        else value
    )


def extract_area_from_drawing_number(value):
    value = normalize_drawing_number(value)

    if not value:
        return ""

    parts = value.split("/")

    return parts[2] if len(parts) >= 3 else ""


def first_non_blank(values):
    for value in values:
        value = clean_value(value)

        if value:
            return value

    return ""

def first_non_blank_description(values):
    """
    Returns the first non-blank description without combining
    descriptions from multiple DIH rows or files.
    """

    for value in values:
        description = clean_description(value)

        if description:
            return description

    return ""

def combine_unique_values(
    values,
    separator=" | ",
):
    result = []
    seen = set()

    for value in values:
        value = clean_value(value)

        if not value:
            continue

        key = value.upper()

        if key not in seen:
            seen.add(key)
            result.append(value)

    return separator.join(result)


def combine_unique_descriptions(values):
    result = []
    seen = set()

    for value in values:
        value = clean_description(value)

        if not value:
            continue

        key = value.upper()

        if key not in seen:
            seen.add(key)
            result.append(value)

    return "\n".join(result)


def make_unique_column_names(columns):
    result = []
    counts = {}

    for column in columns:
        name = clean_value(column) or "Unnamed Column"

        if name not in counts:
            counts[name] = 1
            result.append(name)
        else:
            counts[name] += 1
            result.append(
                f"{name}_{counts[name]}"
            )

    return result

# ============================================================
# FILE VALIDATION
# ============================================================

MAX_EXCEL_FILE_SIZE_MB = 100
MAX_TRANSMITTAL_FILE_SIZE_MB = 25


def validate_uploaded_file(
    uploaded_file,
    allowed_extensions,
    maximum_size_mb,
):
    if uploaded_file is None:
        return False

    file_name = clean_value(
        getattr(uploaded_file, "name", "")
    )

    if not file_name:
        raise ValueError(
            "The uploaded file has no valid file name."
        )

    extension = Path(
        file_name
    ).suffix.lower().lstrip(".")

    allowed_extensions = {
        extension.lower().lstrip(".")
        for extension in allowed_extensions
    }

    if extension not in allowed_extensions:
        raise ValueError(
            f"Unsupported file type: .{extension}. "
            f"Supported types are: "
            f"{', '.join(sorted(allowed_extensions))}."
        )

    file_size_mb = (
        len(uploaded_file.getvalue())
        / 1024
        / 1024
    )

    if file_size_mb > maximum_size_mb:
        raise ValueError(
            f"'{file_name}' is {file_size_mb:.1f} MB. "
            f"The maximum allowed size is "
            f"{maximum_size_mb} MB."
        )

    log_info(
        "Uploaded file validated",
        file_name=file_name,
        file_size_mb=round(file_size_mb, 2),
    )

    return True

# ============================================================
# DRAWING NUMBER EXTRACTION
# ============================================================

DRAWING_NUMBER_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9])"
    r"1252(?:/[A-Z0-9.-]+){5}"
    r"(?![A-Z0-9.-])"
)

TRANSMITTAL_DRAWING_WITH_OPTIONAL_REVISION_PATTERN = (
    re.compile(
        r"(?i)(?<![A-Z0-9])"
        r"(?P<drawing>"
        r"1252(?:/[A-Z0-9.-]+){5}"
        r")"
        r"(?:/"
        r"(?P<revision>"
        r"AZ|[A-Z]{1,3}\d+[A-Z]?"
        r")"
        r")?"
        r"(?![A-Z0-9./-])"
    )
)

def extract_transmittal_drawing_revision_pairs(
    value,
):
    """
    Extracts drawing numbers from a transmittal Drawing / Doc No.
    cell and detects an optional revision appended after the drawing
    number.

    Examples:

        1252/W/TUT/ALT/R32/105
            -> (
                "1252/W/TUT/ALT/R32/105",
                ""
            )

        1252/W/TUT/ALT/R32/105/D3
            -> (
                "1252/W/TUT/ALT/R32/105",
                "D3"
            )

        1252/W/THW/ALT/R05/001A
            -> (
                "1252/W/THW/ALT/R05/001A",
                ""
            )

    The drawing number always retains exactly six segments.
    The optional seventh segment is used only when it is a valid
    revision marker.
    """

    if is_blank(value):
        return []

    text = str(value)
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace("\t", " ")

    results = []
    seen = set()

    for match in (
        TRANSMITTAL_DRAWING_WITH_OPTIONAL_REVISION_PATTERN
        .finditer(text)
    ):
        drawing_number = normalize_drawing_number(
            match.group("drawing")
        )

        appended_revision = (
            normalize_transmittal_revision(
                match.group("revision") or ""
            )
        )

        if not drawing_number:
            continue

        key = (
            drawing_number,
            appended_revision,
        )

        if key in seen:
            continue

        seen.add(key)

        results.append(
            {
                "Drawing Number": drawing_number,
                "Appended Revision": appended_revision,
            }
        )

    return results
    
def extract_embedded_drawing_numbers(value):
    if is_blank(value):
        return []

    text = str(value)
    text = text.replace("\r\n", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = text.replace("\t", " ")
    text = text.replace(",", " ")
    text = text.replace(";", " ")
    text = text.replace("|", " ")

    result = []
    seen = set()

    for match in DRAWING_NUMBER_PATTERN.findall(text):
        drawing_number = normalize_drawing_number(match)
        drawing_number = drawing_number.rstrip(
            ".,;:)]}>"
        )

        parts = drawing_number.split("/")

        if len(parts) != 6:
            continue

        if parts[0] != "1252":
            continue

        if drawing_number not in seen:
            seen.add(drawing_number)
            result.append(drawing_number)

    return result


def select_drawing_matching_ref(
    drawing_value,
    drawing_ref_value,
):
    candidates = extract_embedded_drawing_numbers(
        drawing_value
    )

    target_ref = normalize_drawing_number(
        drawing_ref_value
    )

    if target_ref:
        target_parts = target_ref.split("/")

        if len(target_parts) >= 2:
            target_ref = "/".join(
                target_parts[-2:]
            ).upper()

        for candidate in candidates:
            candidate_ref = extract_drawing_ref(
                candidate
            ).upper()

            if candidate_ref == target_ref:
                return candidate, len(candidates)

        # Do not guess when Drawing Ref is supplied.
        return "", len(candidates)

    if len(candidates) == 1:
        return candidates[0], 1

    if len(candidates) > 1:
        return "", len(candidates)

    fallback = normalize_drawing_number(
        drawing_value
    )

    if re.fullmatch(
        r"1252(?:/[A-Z0-9.-]+){5}",
        fallback,
        flags=re.IGNORECASE,
    ):
        return fallback, 1

    return "", 0


# ============================================================
# SORTING HELPERS
# ============================================================

def natural_sort_tokens(value):
    text = clean_value(value).upper()

    if not text:
        return []

    tokens = []

    for part in re.split(r"(\d+)", text):
        if not part:
            continue

        if part.isdigit():
            tokens.append((1, int(part)))
        else:
            tokens.append((0, part))

    return tokens


def sort_by_drawing_ref(df):
    if df.empty or "Drawing Ref" not in df.columns:
        return df.reset_index(drop=True)

    sortable = []

    for position, value in enumerate(
        df["Drawing Ref"].tolist()
    ):
        text = clean_value(value)

        sortable.append(
            (
                1 if not text else 0,
                natural_sort_tokens(text),
                position,
            )
        )

    positions = [
        item[2]
        for item in sorted(
            sortable,
            key=lambda item: (
                item[0],
                item[1],
                item[2],
            ),
        )
    ]

    return df.iloc[positions].reset_index(
        drop=True
    )


# ============================================================
# EXCEL READING
# ============================================================

def get_excel_engines(file_name):
    extension = Path(file_name).suffix.lower()

    if extension == ".xls":
        return ["calamine", "xlrd"]

    if extension in [".xlsx", ".xlsm"]:
        return ["calamine", "openpyxl"]

    raise ValueError(
        f"Unsupported Excel extension: {extension}"
    )


def get_sheet_names(file_bytes, file_name):
    errors = []

    for engine in get_excel_engines(file_name):
        try:
            excel_file = pd.ExcelFile(
                io.BytesIO(file_bytes),
                engine=engine,
            )

            return excel_file.sheet_names

        except Exception as error:
            errors.append(
                f"{engine}: "
                f"{type(error).__name__}: {error}"
            )

    raise ValueError(
        f"Could not read Excel file "
        f"'{file_name}'.\n\n"
        + "\n".join(errors)
    )


def read_excel_sheet(
    file_bytes,
    file_name,
    sheet_name,
    header=None,
    nrows=None,
):
    errors = []

    for engine in get_excel_engines(file_name):
        try:
            return pd.read_excel(
                io.BytesIO(file_bytes),
                sheet_name=sheet_name,
                header=header,
                nrows=nrows,
                engine=engine,
            )

        except Exception as error:
            errors.append(
                f"{engine}: "
                f"{type(error).__name__}: {error}"
            )

    raise ValueError(
        f"Could not read worksheet '{sheet_name}' "
        f"from '{file_name}'.\n\n"
        + "\n".join(errors)
    )


# ============================================================
# COLUMN DETECTION
# ============================================================

def find_exact_column(df, aliases):
    columns = {
        normalize_header(column): column
        for column in df.columns
    }

    for alias in aliases:
        alias = normalize_header(alias)

        if alias in columns:
            return columns[alias]

    return None


def find_contains_column(df, aliases):
    for column in df.columns:
        column_text = normalize_header(column)

        for alias in aliases:
            if normalize_header(alias) in column_text:
                return column

    return None


def find_column(df, aliases):
    result = find_exact_column(df, aliases)

    if result is not None:
        return result

    return find_contains_column(df, aliases)


def score_header_row(row_values, aliases):
    score = 0

    for value in row_values:
        value = normalize_header(value)

        for alias in aliases:
            alias = normalize_header(alias)

            if value == alias:
                score += 10
            elif alias in value:
                score += 2

    return score


def detect_header_row(
    file_bytes,
    file_name,
    sheet_name,
    aliases,
):
    preview = read_excel_sheet(
        file_bytes,
        file_name,
        sheet_name,
        header=None,
        nrows=100,
    )

    best_row = 0
    best_score = 0

    for row_index in range(len(preview)):
        score = score_header_row(
            preview.iloc[row_index].tolist(),
            aliases,
        )

        if score > best_score:
            best_score = score
            best_row = row_index
    if best_score == 0:
        raise ValueError(
            f"No valid header row was detected in worksheet "
            f"'{sheet_name}'."
        )
        
    return best_row


def detect_modification_header_row(
    file_bytes,
    file_name,
    sheet_name,
):
    return detect_header_row(
        file_bytes,
        file_name,
        sheet_name,
        DRAWING_NUMBER_ALIASES
        + DRAWING_REF_ALIASES
        + DESCRIPTION_ALIASES
        + PHASE_ALIASES
        + REVISION_ALIASES
        + MODIFICATION_SHEET_NO_ALIASES,
    )


def detect_dih_header_row(
    file_bytes,
    file_name,
    sheet_name,
):
    return detect_header_row(
        file_bytes,
        file_name,
        sheet_name,
        [
            "DRAWING NO.",
            "DRAWING NO",
            "DRAWING NUMBER",
            "DRAWING TITLE",
            "TITLE",
            "REVISION",
            "REV",
        ],
    )


# ============================================================
# REVISION HELPERS
# ============================================================

def parse_revision_marker(marker):
    marker = normalize_header(marker)

    match = re.match(
        r"^([A-Z]+)(\d+)([A-Z]*)$",
        marker,
    )

    if not match:
        return None

    return (
        match.group(1),
        int(match.group(2)),
        match.group(3),
    )

# ============================================================
# TRANSMITTAL REVISION HELPERS
# ============================================================

TRANSMITTAL_REVISION_REGEX = re.compile(
    r"^(?:AZ|[A-Z]{1,3}\d+[A-Z]?)$",
    flags=re.IGNORECASE,
)

def normalize_approval_status(value):
    """
    Converts transmittal status values into:
        APPROVED
        REJECTED
        OTHER
    """

    text = normalize_header(value)
    text = text.replace("’", "'")
    text = text.replace("�", "'")

    if not text:
        return ""

    # Handles values such as:
    # Ap'
    # App
    # Approved
    # A'
    if (
        text.startswith("AP")
        or text.startswith("A'")
        or text in {"A", "APP"}
    ):
        return "APPROVED"

    if (
        text.startswith("REJ")
        or text.startswith("R'")
        or text in {"R", "REJECT"}
    ):
        return "REJECTED"

    return text


def parse_approval_date(value):
    """
    Returns a pandas Timestamp where possible.
    Blank or invalid dates return NaT.
    """

    if is_blank(value):
        return pd.NaT

    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value)

    try:
        parsed = pd.to_datetime(
            str(value).strip(),
            errors="coerce",
            dayfirst=True,
        )

        return parsed
    except Exception:
        return pd.NaT


def format_approval_date(value):
    parsed = parse_approval_date(value)

    if pd.isna(parsed):
        return ""

    return parsed.strftime("%Y-%m-%d")


def get_latest_transmittal_record(group):
    """
    Selects the latest record by approval date.

    If approval dates are missing, the last uploaded/read
    record wins.
    """

    if group.empty:
        return None

    working = group.copy()

    if "_TransmittalOrder" not in working.columns:
        working["_TransmittalOrder"] = range(
            len(working)
        )

    working["_ParsedApprovalDate"] = (
        working["Approval Date"].apply(
            parse_approval_date
        )
    )

    working["_HasApprovalDate"] = (
        working["_ParsedApprovalDate"].notna()
    )

    working = working.sort_values(
        [
            "_HasApprovalDate",
            "_ParsedApprovalDate",
            "_TransmittalOrder",
        ],
        kind="stable",
    )

    return working.iloc[-1].to_dict()

def is_transmittal_status_header(value):
    text = normalize_header(value)

    return (
        text in {
            "STATUS",
            "+STATUS",
            "APPROVAL STATUS",
            "DOCUMENT STATUS",
        }
        or "STATUS" in text
    )


def is_transmittal_approval_date_header(value):
    text = normalize_header(value)

    return (
        "APPROVAL DATE" in text
        or "DATE APPROVED" in text
        or "APPROVED DATE" in text
        or text in {
            "APPROVAL",
            "STATUS DATE",
            "DATE",
        }
    )

def normalize_transmittal_revision(value):
    """
    Supports:

        A1
        A2
        B4
        C4
        D2
        D3
        D2A
        EA3
        AZ

    Also accepts separators such as:
        D-2
        D 2
        D/2
    """

    if is_blank(value):
        return ""

    text = normalize_header(value)

    text = re.sub(
        r"[\s\-_\/]+",
        "",
        text,
    )

    if not TRANSMITTAL_REVISION_REGEX.fullmatch(
        text
    ):
        return ""

    return text

def extract_approval_date_from_text(value):
    """
    Extracts an approval date from text.

    Supports:
        2026-08-27
        27/08/2026
        A2(2026-08-27)
    """

    text = clean_value(value)

    if not text:
        return ""

    iso_match = re.search(
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        text,
    )

    if iso_match:
        return format_approval_date(
            iso_match.group(0)
        )

    normal_match = re.search(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        text,
    )

    if normal_match:
        return format_approval_date(
            normal_match.group(0)
        )

    return ""


def extract_revision_from_text(value):
    """
    Extracts the revision from text.

    Examples:
        A2
        A2 2026-08-27
        A2(2026-08-27)
    """

    text = normalize_header(value)

    if not text:
        return ""

    tokens = re.findall(
        r"\b(?:AZ|[A-Z]{1,3}\d+[A-Z]?)\b",
        text,
    )

    for token in tokens:
        revision = normalize_transmittal_revision(
            token
        )

        if revision:
            return revision

    return ""


def is_valid_transmittal_revision(value):
    return bool(
        normalize_transmittal_revision(value)
    )


def is_transmittal_drawing_header(value):
    text = normalize_header(value)

    return (
    "DRAWING" in text
    or "DRAWING NO" in text
    or "DOC NO" in text
    or text in {
        "DOCUMENT NUMBER",
        "DOCUMENT NO",
        "DOC NUMBER",
    }
)


def is_transmittal_revision_header(value):
    text = normalize_header(value)

    text = re.sub(
        r"[\s:.\-]+$",
        "",
        text,
    ).strip()

    return text in {
        "REV",
        "REVISION",
        "REVISION NO",
        "VERSION",
    }


def is_transmittal_description_header(value):
    text = normalize_header(value)

    return (
        "DESCRIPTION" in text
        or "SUBJECT" in text
        or "TITLE" in text
    )


def find_transmittal_table_columns(table):
    """
    Finds the actual Word table columns for:

        Drawing / Doc No.
        Rev.
        Description
        Status
        Approval Date
    """

    for row_index, table_row in enumerate(table.rows):
        cell_texts = [
            clean_value(cell.text)
            for cell in table_row.cells
        ]

        if not cell_texts:
            continue

        drawing_index = None
        revision_index = None
        description_index = None
        status_index = None
        approval_date_index = None

        for index, cell_text in enumerate(cell_texts):
            if (
                drawing_index is None
                and is_transmittal_drawing_header(
                    cell_text
                )
            ):
                drawing_index = index

            if (
                revision_index is None
                and is_transmittal_revision_header(
                    cell_text
                )
            ):
                revision_index = index

            if (
                description_index is None
                and is_transmittal_description_header(
                    cell_text
                )
            ):
                description_index = index

            if (
                status_index is None
                and is_transmittal_status_header(
                    cell_text
                )
            ):
                status_index = index

            if (
                approval_date_index is None
                and is_transmittal_approval_date_header(
                    cell_text
                )
            ):
                approval_date_index = index

        if (
            drawing_index is not None
            and revision_index is not None
        ):
            return (
                row_index,
                drawing_index,
                revision_index,
                description_index,
                status_index,
                approval_date_index,
            )

    return (
        None,
        None,
        None,
        None,
        None,
        None,
    )


def extract_table_cell_text(
    table_row,
    cell_index,
):
    if cell_index is None:
        return ""

    if cell_index >= len(table_row.cells):
        return ""

    return clean_value(
        table_row.cells[cell_index].text
    )


def revision_sort_key(value):
    value = normalize_header(value)

    match = re.match(
        r"^([A-Z]+)(\d+)([A-Z]*)$",
        value,
    )

    if not match:
        return ("", -1, "")

    return (
        match.group(1),
        int(match.group(2)),
        match.group(3),
    )


def select_latest_revision(values):
    """
    Selects the latest transmittal/DIH revision.

    Supported values include:
        A1, A2, B4, C4, D3, D2A, EA3, AZ
    """

    cleaned = []

    for value in values:
        value = normalize_transmittal_revision(value)

        if not value:
            value = clean_value(value)

        if value and value not in cleaned:
            cleaned.append(value)

    if not cleaned:
        return ""

    def revision_key(value):
        value = normalize_transmittal_revision(value)

        if value == "AZ":
            return (
                999999,
                "AZ",
                "",
            )

        parsed = parse_revision_marker(value)

        if parsed is None:
            return (
                -1,
                "",
                "",
            )

        letters, number, suffix = parsed

        return (
            number,
            letters,
            suffix,
        )

    return max(
        cleaned,
        key=revision_key,
    )


def extract_dih_revision_markers(value):
    if is_blank(value):
        return []

    text = normalize_header(value)

    markers = re.findall(
        r"\b[A-Z]{1,3}\d+[A-Z]?\b"
        r"|\b[A-Z]{1,3}Z\b"
        r"|\bAZ\b",
        text,
    )

    result = []
    seen = set()

    for marker in markers:
        marker = normalize_header(marker)

        if marker not in seen:
            seen.add(marker)
            result.append(marker)

    return result


def get_dih_family(marker):
    parsed = parse_revision_marker(marker)

    if parsed is None:
        return ""

    letters = parsed[0]

    configured_families = sorted(
        DIH_GROUPS.keys(),
        key=len,
        reverse=True,
    )

    for family in configured_families:
        if letters == family:
            return family

    if letters.startswith("EA"):
        return "EA"

    if letters.startswith("E"):
        return "E"

    # TA1, TA2, etc. belong to the C5 Stage family.
    if letters.startswith("TA"):
        return "T"

    return letters[0]


# ============================================================
# TRANSMITTAL TEXT EXTRACTION
# ============================================================
def find_transmittal_stage(text):
    """
    Converts stage labels from the transmittal description
    into Master Drawing List stage names.
    """

    text = normalize_header(text)

    if re.search(r"\bB\s*0*4\b", text):
        return DIH_GROUPS["D"]["name"]

    if re.search(r"\bA\s*0*4\b", text):
        return DIH_GROUPS["C"]["name"]

    if re.search(r"\bA\s*0*2\b", text):
        return DIH_GROUPS["A"]["name"]

    if re.search(r"\bB\s*0*5\b", text):
        return DIH_GROUPS["F"]["name"]

    if re.search(r"\bB\s*0*7\b", text):
        return DIH_GROUPS["G"]["name"]

    if re.search(r"\bA\s*0*7\b", text):
        return DIH_GROUPS["L"]["name"]

    if re.search(
        r"\bA\s*0*3\b.*STAGE[- ]?1",
        text,
    ):
        return DIH_GROUPS["B"]["name"]

    if re.search(
        r"\bA\s*0*3\b.*STAGE[- ]?2",
        text,
    ):
        return DIH_GROUPS["EA"]["name"]

    if re.search(r"\bC\s*0*3\b", text):
        return DIH_GROUPS["H"]["name"]

    # Standalone C5 Stage. Do not match C5 Stage 1–4 here.
    if re.search(
        r"\bC\s*0*5\s+STAGE\b",
        text,
    ) and not re.search(
        r"\bC\s*0*5\s+STAGE\s*[1-4]\b",
        text,
    ):
        return DIH_GROUPS["T"]["name"]

    return ""

def extract_docx_text(file_bytes):
    """
    Extracts paragraph text and table-row text from DOCX.
    """

    document = Document(io.BytesIO(file_bytes))
    rows = []

    for paragraph in document.paragraphs:
        text = clean_value(paragraph.text)

        if text:
            rows.append(text)

    for table in document.tables:
        for table_row in table.rows:
            cells = []

            for cell in table_row.cells:
                text = clean_value(cell.text)

                if text:
                    cells.append(text)

            if cells:
                rows.append(" | ".join(cells))

    return rows


def extract_transmittal_rows(
    file_bytes,
    file_name,
):
    extension = Path(file_name).suffix.lower()

    if extension == ".doc":
        raise ValueError(
            "Legacy .doc transmittal files are not supported "
            "on the hosted application. Please open the file "
            "in Microsoft Word and save it as .docx before "
            "uploading."
        )

    if extension in {".docx", ".docm"}:
        rows = extract_docx_text(file_bytes)

        if not rows:
            raise ValueError(
                "No readable paragraph or table text was "
                "found in the transmittal form."
            )

        return rows

    raise ValueError(
        "Unsupported transmittal file format. Please upload "
        "a .docx or .docm file."
    )

def extract_transmittal_document_date(document):
    """
    Extracts dates such as:

        Date : 24/08/2026
        Date issued : 24/08/2026
    """

    text_parts = []

    for paragraph in document.paragraphs:
        text_parts.append(
            clean_value(paragraph.text)
        )

    for table in document.tables:
        for table_row in table.rows:
            text_parts.append(
                " ".join(
                    clean_value(cell.text)
                    for cell in table_row.cells
                )
            )

    document_text = "\n".join(text_parts)

    match = re.search(
        r"(?i)\bDATE(?:\s+ISSUED)?\s*[:\-]\s*"
        r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
        document_text,
    )

    if not match:
        return ""

    return format_approval_date(
        match.group(1)
    )
def extract_transmittal_table_records(
    file_bytes,
    file_name,
):
    """
    Extracts drawing, revision, status, approval date and
    description from DOCX/DOCM transmittal tables.
    """

    extension = Path(file_name).suffix.lower()

    if extension not in {".docx", ".docm"}:
        raise ValueError(
            "Unsupported transmittal format. Please upload "
            "a .docx or .docm file."
        )

    document = Document(
        io.BytesIO(file_bytes)
    )

    records = []

    document_date = (
        extract_transmittal_document_date(
            document
        )
    )

    for table in document.tables:
        (
            header_row_index,
            drawing_index,
            revision_index,
            description_index,
            status_index,
            approval_date_index,
        ) = find_transmittal_table_columns(table)

        if header_row_index is None:
            continue

        for table_row in table.rows[
            header_row_index + 1:
        ]:
            drawing_text = extract_table_cell_text(
                table_row,
                drawing_index,
            )

            revision_text = extract_table_cell_text(
                table_row,
                revision_index,
            )

            description_text = extract_table_cell_text(
                table_row,
                description_index,
            )

            status_text = extract_table_cell_text(
                table_row,
                status_index,
            )

            approval_date_text = extract_table_cell_text(
                table_row,
                approval_date_index,
            )

            drawing_revision_pairs = (
                extract_transmittal_drawing_revision_pairs(
                    drawing_text
                )
            )

            if not drawing_revision_pairs:
                continue

            # This is the normal transmittal-table revision,
            # for example D4.
            table_revision = normalize_transmittal_revision(
                revision_text
            )

            if not table_revision:
                continue

            status = normalize_approval_status(
                status_text
            )

            approval_date = format_approval_date(
                approval_date_text
            )

            if not approval_date:
                approval_date = document_date

            # Include text from every table cell.
            #
            # Some Alstom transmittal forms contain more than one
            # "Description" column. For example, the actual drawing title
            # "SER LAYOUT FOR THW TAT-D11" may be in the second Description
            # column, not the first one detected by the header logic.
            full_row_text = " | ".join(
                clean_value(cell.text)
                for cell in table_row.cells
                if clean_value(cell.text)
            )

            stage_source_text = full_row_text

            stage = (
                detect_stage_from_description(
                    stage_source_text
                )
                or find_transmittal_stage(
                    stage_source_text
                )
            )

            for drawing_revision_pair in (
                drawing_revision_pairs
            ):
                drawing_number = (
                    drawing_revision_pair[
                        "Drawing Number"
                    ]
                )

                appended_revision = (
                    drawing_revision_pair[
                        "Appended Revision"
                    ]
                )

                # A revision included after the sixth drawing
                # segment is specific to that drawing and overrides
                # the general Rev. value in the transmittal table.
                #
                # Example:
                #
                # Table Rev.: D4
                # Drawing: 1252/W/TUT/ALT/R32/105/D3
                #
                # Result:
                # Drawing Number: 1252/W/TUT/ALT/R32/105
                # Transmittal Revision: D3
                drawing_revision = (
                    appended_revision
                    or table_revision
                )

                records.append(
                    {
                        "Normalized Drawing Number":
                            drawing_number,
                        "Transmittal Revision":
                            drawing_revision,
                        "Transmittal Stage":
                            stage,
                        "Status":
                            status,
                        "Approval Date":
                            approval_date,
                        "Source Row": full_row_text,
                    }
                )

    columns = [
        "Normalized Drawing Number",
        "Transmittal Revision",
        "Transmittal Stage",
        "Status",
        "Approval Date",
        "Source Row",
    ]

    if not records:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(records)

    result["_TransmittalOrder"] = range(
        len(result)
    )

    return result.reset_index(drop=True)

def get_unmatched_transmittal_records(
    transmittal_df,
    final_df,
):
    """
    Returns transmittal drawings that do not exist in the
    generated/existing Master Drawing List.
    """

    if (
        transmittal_df is None
        or transmittal_df.empty
    ):
        return pd.DataFrame()

    if (
        final_df is None
        or "C1252 Drawing" not in final_df.columns
    ):
        unmatched = transmittal_df.copy()
        unmatched["Missing From Master"] = "Yes"
        return unmatched.reset_index(drop=True)

    master_drawings = {
        normalize_drawing_number(value)
        for value in final_df["C1252 Drawing"]
        if normalize_drawing_number(value)
    }

    transmittal_copy = transmittal_df.copy()

    transmittal_copy["_NormalizedCheckDrawing"] = (
        transmittal_copy[
            "Normalized Drawing Number"
        ].apply(normalize_drawing_number)
    )

    unmatched = transmittal_copy[
        ~transmittal_copy[
            "_NormalizedCheckDrawing"
        ].isin(master_drawings)
    ].copy()

    unmatched["Missing From Master"] = "Yes"

    return (
        unmatched
        .drop(columns=["_NormalizedCheckDrawing"])
        .reset_index(drop=True)
    )
# ============================================================
# TRANSMITTAL REGISTER PROCESSING
# ============================================================

def extract_transmittal_drawing_numbers(text):
    """
    Extract all C1252 drawing numbers from a transmittal row.
    """

    return extract_embedded_drawing_numbers(text)


def remove_drawing_numbers_from_text(
    text,
    drawing_numbers,
):
    """
    Removes drawing numbers before revision extraction.
    This prevents drawing segments such as R24 or R32 from
    being incorrectly interpreted as transmittal revisions.
    """

    result = clean_value(text)

    for drawing_number in drawing_numbers:
        result = result.replace(
            drawing_number,
            " ",
        )

    return result


def extract_transmittal_revision_candidates(
    row_text,
    drawing_numbers,
):
    """
    Extract revision candidates from a transmittal row.

    The function supports values such as:
        D2
        B2
        A1
        EA3
        D2A

    It also recognises common labelled formats such as:
        Rev: D2
        Revision D2
        Version: D2
        Transmittal Version D2
    """

    text = remove_drawing_numbers_from_text(
        row_text,
        drawing_numbers,
    )

    candidates = []

    labelled_pattern = re.compile(
        r"(?i)"
        r"(?:REV(?:ISION)?|VERSION|"
        r"TRANSMITTAL|SUBMITTED|TRANSMITTED)"
        r"\s*[:#\-\s]*"
        r"([A-Z]{1,3}\d+[A-Z]?)"
    )

    for match in labelled_pattern.findall(text):
        revision = normalize_header(match)

        if revision not in candidates:
            candidates.append(revision)

    general_pattern = re.compile(
        r"(?<![A-Z0-9])"
        r"([A-Z]{1,3}\d+[A-Z]?)"
        r"(?![A-Z0-9])"
    )

    for match in general_pattern.findall(text):
        revision = normalize_header(match)

        if revision not in candidates:
            candidates.append(revision)

    return candidates


def resolve_transmittal_stage_name(stage_text):
    """
    Converts stage aliases found in the transmittal form into
    the actual stage/group names used by the Master Drawing List.
    """

    stage_text = normalize_header(stage_text)

    if not stage_text:
        return ""

    # Match the configured DIH group names first.
    for group_info in DIH_GROUPS.values():
        configured_name = normalize_header(
            group_info["name"]
        )

        if configured_name in stage_text:
            return group_info["name"]

    # Match phase aliases dynamically using the configured
    # REQUIRED_PHASE_ORDER values.
    for phase_name in REQUIRED_PHASE_ORDER:
        phase_text = normalize_header(phase_name)

        if phase_text not in stage_text:
            continue

        # Convert aliases such as B4 to the actual group B04.
        for group_info in DIH_GROUPS.values():
            configured_name = normalize_header(
                group_info["name"]
            )

            configured_parts = re.sub(
                r"[^A-Z0-9]",
                "",
                configured_name,
            )

            phase_parts = re.sub(
                r"[^A-Z0-9]",
                "",
                phase_text,
            )

            if (
                configured_parts == phase_parts
                or configured_parts.lstrip("A")
                == phase_parts.lstrip("A")
            ):
                return group_info["name"]

        # Explicit aliases for phase labels that do not map
        # directly by removing spaces or leading zeroes.
        if phase_text in {"B4", "B04"}:
            return DIH_GROUPS["D"]["name"]

        if phase_text in {"A4", "A04"}:
            return DIH_GROUPS["C"]["name"]

        if phase_text in {"A2", "A02", "A3", "A03"}:
            return DIH_GROUPS["A"]["name"]

    # Handle stage descriptions such as:
    # "B4 STAGE CIRCUIT BOOK"
    if re.search(r"\bB\s*0*4\b", stage_text):
        return DIH_GROUPS["D"]["name"]

    if re.search(r"\bA\s*0*4\b", stage_text):
        return DIH_GROUPS["C"]["name"]

    if re.search(r"\bA\s*0*2\b", stage_text):
        return DIH_GROUPS["A"]["name"]

    if (
        re.search(r"\bA\s*0*3\b", stage_text)
        and (
            "STAGE-1" in stage_text
            or "STAGE 1" in stage_text
        )
    ):
        return DIH_GROUPS["B"]["name"]

    if (
        re.search(r"\bA\s*0*3\b", stage_text)
        and (
            "STAGE-2" in stage_text
            or "STAGE 2" in stage_text
        )
    ):
        return DIH_GROUPS["EA"]["name"]

    return clean_value(stage_text)


def detect_transmittal_stage_from_text(
    row_text,
    document_text="",
):
    """
    Detects the Master Drawing List stage/group from the
    transmittal text.

    For example:
        B4 STAGE CIRCUIT BOOK
    becomes:
        B04
    """

    row_text = clean_value(row_text)
    document_text = clean_value(document_text)

    # Prefer the row because it is more precise than the
    # entire document.
    row_stage = resolve_transmittal_stage_name(
        row_text
    )

    if row_stage:
        return row_stage

    return resolve_transmittal_stage_name(
        document_text
    )

def infer_stage_from_revision(
    revision,
    current_stage="",
):
    """
    Uses the revision family as a fallback stage.

    For example:
        D2 -> B04
        B2 -> A03 Stage-1
        A2 -> A02
        C2 -> A04
    """

    current_stage = clean_value(current_stage)

    if current_stage:
        return current_stage

    parsed = parse_revision_marker(revision)

    if parsed is None:
        return ""

    letters = parsed[0]
    family = get_dih_family(letters + "1")

    if family in DIH_GROUPS:
        return DIH_GROUPS[family]["name"]

    return ""

def read_transmittal_register(
    file_bytes,
    file_name,
):
    """
    Reads the transmittal form.

    Primary method:
        Reads the Word table columns directly.

    Fallback:
        Uses the first valid revision after the final
        drawing number.
    """

    result = extract_transmittal_table_records(
        file_bytes,
        file_name,
    )

    if (
        result is not None
        and not result.empty
    ):
        return result                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              

    rows = extract_transmittal_rows(
        file_bytes,
        file_name,
    )

    document_text = "\n".join(rows)

    drawing_matches = list(
        DRAWING_NUMBER_PATTERN.finditer(
            document_text
        )
    )

    if not drawing_matches:
        raise ValueError(
            f"No C1252 drawing numbers were found "
            f"in '{file_name}'."
        )

    final_drawing_match = drawing_matches[-1]

    text_after_drawings = document_text[
        final_drawing_match.end():
    ]

    revision_match = re.search(
        r"(?<![A-Z0-9])"
        r"(AZ|[A-Z]{1,3}\d+[A-Z]?)"
        r"(?![A-Z0-9])",
        text_after_drawings[:100],
        flags=re.IGNORECASE,
    )

    if not revision_match:
        raise ValueError(
            f"The Rev value could not be identified "
            f"in '{file_name}'."
        )

    revision = normalize_transmittal_revision(
        revision_match.group(1)
    )

    if not revision:
        raise ValueError(
            f"The detected Rev value is invalid in "
            f"'{file_name}'."
        )

    stage = find_transmittal_stage(
        document_text
    )

    records = []

    for drawing_match in drawing_matches:
        records.append(
            {
                "Normalized Drawing Number":
                    normalize_drawing_number(
                        drawing_match.group(0)
                    ),
                "Transmittal Revision":
                    revision,
                "Transmittal Stage":
                    stage,
                "Status": "",
                "Approval Date": "",
                "Source Row":
                    document_text,
            }
        )

    return pd.DataFrame(records)


def combine_transmittal_registers(
    transmittal_dataframes,
):
    """
    Combines multiple transmittal files.

    The latest record is selected using the approval date.
    If no date is available, the last uploaded record wins.
    """

    if not transmittal_dataframes:
        return pd.DataFrame(
            columns=[
                "Normalized Drawing Number",
                "Transmittal Revision",
                "Transmittal Stage",
                "Status",
                "Approval Date",
                "Source Row",
            ]
        )

    combined = pd.concat(
        transmittal_dataframes,
        ignore_index=True,
    )

    combined["_TransmittalOrder"] = range(
        len(combined)
    )

    grouped_records = []

    group_columns = [
        "Normalized Drawing Number",
        "Transmittal Revision",
        "Transmittal Stage",
    ]

    for (
        drawing_number,
        revision,
        transmittal_stage,
    ), group in combined.groupby(
        group_columns,
        dropna=False,
        sort=False,
    ):
        latest = get_latest_transmittal_record(
            group
        )

        if latest is None:
            continue

        grouped_records.append(
            {
                "Normalized Drawing Number":
                    normalize_drawing_number(
                        drawing_number
                    ),
                "Transmittal Revision":
                    clean_value(revision),
                "Transmittal Stage":
                    clean_value(
                        transmittal_stage
                        or latest.get(
                            "Transmittal Stage",
                            "",
                        )
                    ),
                "Status":
                    normalize_approval_status(
                        latest.get(
                            "Status",
                            "",
                        )
                    ),
                "Approval Date":
                    format_approval_date(
                        latest.get(
                            "Approval Date",
                            "",
                        )
                    ),
                "Source Row":
                    clean_value(
                        latest.get(
                            "Source Row",
                            "",
                        )
                    ),
            }
        )

    return pd.DataFrame(
        grouped_records
    )


# ============================================================
# HEADER CREATION
# ============================================================

def create_dih_revision_header(
    family,
    number,
    suffix="",
):
    group = DIH_GROUPS.get(family)

    if group is None:
        return ""

    marker = f"{family}{number}{suffix}"

    return f"{group['name']} {marker}"


def create_dih_mod_header(family):
    group = DIH_GROUPS.get(family)

    if (
        group is None
        or not group["include_mod"]
    ):
        return ""

    return f"{group['name']} MOD"


def create_dih_az_header(family):
    group = DIH_GROUPS.get(family)

    if (
        group is None
        or not group["include_az"]
    ):
        return ""

    return f"{group['name']} AZ"


def create_special_revision_header(
    category,
    marker,
):
    if (
        category not in DIH_SPECIAL_CATEGORIES
        or parse_revision_marker(marker) is None
    ):
        return ""

    return (
        f"{category} "
        f"{normalize_header(marker)}"
    )


def create_special_mod_header(category):
    if category not in DIH_SPECIAL_CATEGORIES:
        return ""

    return f"{category} MOD"


def get_modification_mod_header(phase_value):
    phase = clean_value(phase_value)

    if not phase:
        return ""

    upper = normalize_header(phase)

    if (
        "STAGE-1" in upper
        or "STAGE 1" in upper
    ):
        return "A3 Stage 1 MOD"

    if (
        "STAGE-2" in upper
        or "STAGE 2" in upper
    ):
        return "A3 Stage 2 MOD"

    if re.search(r"\bA\s*0*2\b", upper):
        return "A2 Stage MOD"

    if re.search(r"\bA\s*0*4\b", upper):
        return "A4 Stage MOD"

    if re.search(r"\bB\s*0*4\b", upper):
        return "B4 Stage MOD"

    if re.search(r"\bB\s*0*5\b", upper):
        return "B5 Stage MOD"

    if re.search(r"\bB\s*0*7\b", upper):
        return "B7 Stage MOD"

    if re.search(r"\bC\s*0*3\b", upper):
        return "C3 Stage MOD"

    if re.search(r"\bC\s*0*5\b", upper):
        if re.search(
            r"\bC\s*0*5\s+STAGE\s*[1-4]\b",
            upper,
        ):
            stage_number = re.search(
                r"\bC\s*0*5\s+STAGE\s*([1-4])\b",
                upper,
            ).group(1)

            return (
                f"C5 Stage {stage_number} MOD"
            )

        return "C5 Stage MOD"

    if re.search(r"\bA\s*0*7\b", upper):
        return "A7 Stage MOD"

    if re.search(r"\bA\s*1*1\b", upper):
        return "A11 Stage MOD"

    if upper.endswith(" MOD"):
        return phase

    return f"{phase} MOD"


def create_transmittal_column_name(
    stage_group,
):
    stage_group = clean_value(stage_group)

    if not stage_group:
        return ""

    return (
        f"{stage_group} "
        f"{TRANSMITTAL_COLUMN_SUFFIX}"
    )


def is_transmittal_column(column):
    return normalize_header(
        column
    ).endswith(
        normalize_header(
            TRANSMITTAL_COLUMN_SUFFIX
        )
    )


def get_transmittal_column_group(column):
    text = clean_value(column)

    suffix = (
        f" {TRANSMITTAL_COLUMN_SUFFIX}"
    )

    if text.upper().endswith(suffix.upper()):
        return text[:-len(suffix)].strip()

    return ""

def is_modification_transmittal(row):
    """
    Returns True when the transmittal row describes a
    modification drawing/transmittal.
    """

    text = " ".join(
        [
            clean_value(
                row.get("Transmittal Stage", "")
            ),
            clean_value(
                row.get("Source Row", "")
            ),
        ]
    )

    return bool(
        re.search(
            r"(?i)(?<![A-Z0-9])"
            r"(?:MOD|MODIFICATION|MOD SHEET)"
            r"(?![A-Z0-9])",
            text,
        )
    )


def build_transmittal_record_lookup(
    transmittal_df,
):
    """
    Creates an exact drawing-level lookup.

    Key:

        Normalized Drawing Number
        + Transmittal Revision
        + Transmittal Stage
    """

    lookup = {}

    if (
        transmittal_df is None
        or transmittal_df.empty
    ):
        return lookup

    for order, (_, row) in enumerate(
        transmittal_df.iterrows()
    ):

        drawing_number = normalize_drawing_number(
            row.get(
                "Normalized Drawing Number",
                "",
            )
        )

        revision = normalize_transmittal_revision(
            row.get(
                "Transmittal Revision",
                "",
            )
        )

        stage = clean_value(
            row.get(
                "Transmittal Stage",
                "",
            )
        )

        if not drawing_number or not revision:
            continue

        stage = (
            detect_stage_from_description(stage)
            or stage
        )

        key = (
            drawing_number,
            revision,
            normalize_stage_text(stage),
        )

        candidate = {
            "Drawing Number": drawing_number,
            "Revision": revision,
            "Stage": stage,

            # Always treat transmittal content as approved.
            "Status": "APPROVED",

            "Approval Date": format_approval_date(
                row.get(
                    "Approval Date",
                    "",
                )
            ),

            "Is Modification": is_modification_transmittal(
                row
            ),

            "Is Detail Design": is_detail_design_transmittal(
                row
            ),

            "Source Row": clean_value(
                row.get(
                    "Source Row",
                    "",
                )
            ),

            "_TransmittalOrder": row.get(
                "_TransmittalOrder",
                order,
            ),
        }

        existing = lookup.get(key)

        if existing is None:
            lookup[key] = candidate
            continue

        latest = get_latest_transmittal_record(
            pd.DataFrame(
                [
                    existing,
                    candidate,
                ]
            )
        )

        if latest is not None:
            lookup[key] = latest

    return lookup

def add_transmittal_mod_headers(
    dataframe,
    transmittal_df,
):
    """
    Adds MOD columns required by modification transmittals,
    including when no Modification REGISTER is uploaded.
    """

    if dataframe is None:
        return dataframe

    result = dataframe.copy()

    if (
        transmittal_df is None
        or transmittal_df.empty
    ):
        return result

    for _, row in transmittal_df.iterrows():
        if not is_modification_transmittal(row):
            continue

        stage = clean_value(
            row.get("Transmittal Stage", "")
        )

        stage = (
            detect_stage_from_description(stage)
            or stage
        )

        if not stage:
            continue

        mod_header = f"{stage} MOD"

        if mod_header not in result.columns:
            result[mod_header] = ""

    return result
# ============================================================
# DIH REGISTER
# ============================================================

def find_dih_sheet(file_bytes, file_name):
    sheet_names = get_sheet_names(
        file_bytes,
        file_name,
    )

    for sheet_name in sheet_names:
        if normalize_header(sheet_name) in {
            "DIH-TEMPLATE",
            "DIH TEMPLATE",
        }:
            return sheet_name

    for sheet_name in sheet_names:
        if "DIH" in normalize_header(sheet_name):
            return sheet_name

    raise ValueError(
        "No DIH worksheet was found. "
        f"Available sheets: {sheet_names}"
    )

def find_obsolete_drawings_row(raw_df, start_row):
    """
    Returns the worksheet row index where the OBSOLETE DRAWINGS
    section begins. Returns None if the section is not present.
    """

    for row_index in range(start_row, len(raw_df)):
        row_values = raw_df.iloc[row_index].tolist()

        for value in row_values:
            text = normalize_header(value)

            if "OBSOLETE DRAWINGS" in text:
                return row_index

    return None

def detect_dih_sheet_header_data(
    file_bytes,
    file_name,
):
    sheet_name = find_dih_sheet(
        file_bytes,
        file_name,
    )

    header_row = detect_dih_header_row(
        file_bytes,
        file_name,
        sheet_name,
    )

    raw_df = read_excel_sheet(
        file_bytes,
        file_name,
        sheet_name,
        header=None,
    )

    if raw_df.empty:
        raise ValueError(
            "The DIH worksheet is empty."
        )

    headers = make_unique_column_names(
        raw_df.iloc[header_row].tolist()
    )

    obsolete_row = find_obsolete_drawings_row(
        raw_df,
        header_row + 1,
    )

    # Only read normal DIH rows. Anything from the
    # OBSOLETE DRAWINGS section onwards is excluded.
    data_end_row = (
        obsolete_row
        if obsolete_row is not None
        else len(raw_df)
    )

    df = raw_df.iloc[
        header_row + 1:data_end_row
    ].copy()

    df.columns = headers
    df = df.dropna(axis=0, how="all")
    df = df.dropna(axis=1, how="all")

    if df.empty:
        raise ValueError(
            "The DIH worksheet contains no data."
        )

    return (
        sheet_name,
        header_row,
        raw_df,
        df,
    )


def detect_dih_special_category_from_text(
    text,
):
    search_text = normalize_header(text)

    categories = sorted(
        DIH_SPECIAL_CATEGORIES.items(),
        key=lambda item: max(
            len(normalize_header(keyword))
            for keyword in item[1]["keywords"]
        ),
        reverse=True,
    )

    for category, config in categories:
        for keyword in config["keywords"]:
            if (
                normalize_header(keyword)
                in search_text
            ):
                return category

    return ""


def detect_dih_document_category(
    raw_df,
    header_row,
    file_name,
):
    values = [clean_value(file_name)]

    if raw_df is not None and header_row > 0:
        values.extend(
            clean_value(value)
            for value in raw_df.iloc[
                :header_row
            ].astype(object).to_numpy().ravel()
            if clean_value(value)
        )

    return detect_dih_special_category_from_text(
        " ".join(values)
    )


def detect_dih_special_category_from_row(
    row_values,
    revision_index,
):
    values = [
        clean_value(value)
        for value in row_values[:revision_index]
        if clean_value(value)
    ]

    return detect_dih_special_category_from_text(
        " ".join(values)
    )

def detect_scheme_bonding_plan_type(value):
    """
    Detects whether DIH content relates to a Scheme Plan or
    Bonding Plan.

    Detection is based on worksheet content, not file name.
    """

    text = normalize_header(value)

    if "BONDING PLAN" in text:
        return "Bonding Plan"

    if "SCHEME PLAN" in text:
        return "Scheme Plan"

    return ""


def get_dih_document_text(raw_df):
    """
    Converts all DIH worksheet cells into searchable text.
    """

    if raw_df is None or raw_df.empty:
        return ""

    values = []

    for value in raw_df.astype(object).to_numpy().ravel():
        text = clean_value(value)

        if text:
            values.append(text)

    return " ".join(values)

def read_dih_register(
    file_bytes,
    file_name,
):
    (
        sheet_name,
        header_row,
        raw_df,
        df,
    ) = detect_dih_sheet_header_data(
        file_bytes,
        file_name,
    )

    drawing_column = find_column(
        df,
        DRAWING_NUMBER_ALIASES,
    )

    title_column = find_column(
        df,
        [
            "DRAWING TITLE",
            "TITLE",
            "SUBJECT",
            "DESCRIPTION",
        ],
    )

    revision_column = find_column(
        df,
        REVISION_ALIASES,
    )

    if drawing_column is None:
        raise ValueError(
            "DIH drawing-number column was not found."
        )

    if title_column is None:
        raise ValueError(
            "DIH DRAWING TITLE column was not found."
        )

    if revision_column is None:
        raise ValueError(
            "DIH REVISION column was not found."
        )

    revision_index = df.columns.get_loc(
        revision_column
    )

    document_category = (
        detect_dih_document_category(
            raw_df,
            header_row,
            file_name,
        )
    )

    # Read actual DIH content to identify Scheme Plan or Bonding Plan.
    # This does not depend on the uploaded file name.
    document_plan_type = (
        detect_scheme_bonding_plan_type(
            get_dih_document_text(raw_df)
        )
    )

    output = pd.DataFrame(index=df.index)

    output["DIH Drawing Number"] = (
        df[drawing_column].apply(clean_value)
    )

    output["Normalized Drawing Number"] = (
        df[drawing_column].apply(
            normalize_drawing_number
        )
    )

    output["DIH Description"] = (
        df[title_column].apply(
            clean_description
        )
    )

    categories = []
    revisions = []
    plan_types = []

    for _, row in df.iterrows():
        category = document_category

        if not category:
            category = (
                detect_dih_special_category_from_row(
                    row.tolist(),
                    revision_index,
                )
            )

        categories.append(category)

        # Prefer each drawing's own title. If it does not contain
        # the text, use the overall DIH document heading/content.
        row_plan_type = detect_scheme_bonding_plan_type(
            row.get(title_column, "")
        )

        if not row_plan_type:
            row_plan_type = document_plan_type

        plan_types.append(row_plan_type)

        revision_values = [
            clean_value(value)
            for value in row.iloc[
                revision_index:
            ].tolist()
            if clean_value(value)
        ]

        revisions.append(
            combine_unique_values(
                revision_values,
                separator=" ",
            )
        )

    output["DIH Category"] = categories
    output["DIH Revision"] = revisions
    output["DIH Plan Type"] = plan_types

    output = output[
        output[
            "Normalized Drawing Number"
        ].astype(str).str.strip() != ""
    ]

    return (
        output.reset_index(drop=True),
        sheet_name,
        header_row,
        document_category,
    )


# ============================================================
# DIH HEADER GENERATION
# ============================================================

def get_dynamic_dih_headers(dih_df):
    headers = []
    special_categories = set()

    if dih_df is None or dih_df.empty:
        return []

    for _, row in dih_df.iterrows():
        revision = row.get(
            "DIH Revision",
            "",
        )

        category = clean_value(
            row.get(
                "DIH Category",
                "",
            )
        )

        if category in DIH_SPECIAL_CATEGORIES:
            special_categories.add(category)

        for marker in extract_dih_revision_markers(
            revision
        ):
            if marker == "AZ":
                family = "A"

                az_header = (
                    create_dih_az_header(
                        family
                    )
                )

                if az_header:
                    headers.append(
                        az_header
                    )

                continue

            parsed = parse_revision_marker(
                marker
            )

            if parsed is None:
                continue

            letters, number, suffix = parsed

            if category in DIH_SPECIAL_CATEGORIES:
                header = (
                    create_special_revision_header(
                        category,
                        marker,
                    )
                )

                if header:
                    headers.append(header)

                continue

            family = get_dih_family(marker)

            if family not in DIH_GROUPS:
                continue

            header = create_dih_revision_header(
                family,
                number,
                suffix,
            )

            if header:
                headers.append(header)

    # Add MOD columns for active groups.
    active_families = set()

    for header in headers:
        for family, group_info in DIH_GROUPS.items():
            if normalize_header(
                header
            ).startswith(
                normalize_header(
                    group_info["name"]
                ) + " "
            ):
                active_families.add(family)

    for family in active_families:
        mod_header = create_dih_mod_header(
            family
        )

        if mod_header:
            headers.append(mod_header)

    for category in special_categories:
        mod_header = create_special_mod_header(
            category
        )

        if mod_header:
            headers.append(mod_header)

    return sort_headers(headers)

# ============================================================
# DIH MAPPING AND LOOKUP
# ============================================================

def map_dih_revision_to_headers(
    revision,
    dynamic_headers,
    category="",
):
    result = {}

    for marker in extract_dih_revision_markers(
        revision
    ):
        if marker == "AZ":
            # AZ belongs to the currently detected DIH family.
            # If the DIH revision field contains only AZ, use A
            # as the default family.
            family = "A"

            header = create_dih_az_header(
                family
            )

            if header in dynamic_headers:
                result[header] = marker

            continue

        parsed = parse_revision_marker(marker)

        if parsed is None:
            continue

        if category in DIH_SPECIAL_CATEGORIES:
            header = create_special_revision_header(
                category,
                marker,
            )

            if header in dynamic_headers:
                result[header] = marker

            continue

        family = get_dih_family(marker)

        if family not in DIH_GROUPS:
            continue

        letters, number, suffix = parsed

        header = create_dih_revision_header(
            family,
            number,
            suffix,
        )

        if header in dynamic_headers:
            result[header] = marker

    return result


def create_dih_lookup(
    dih_df,
    dynamic_headers,
):
    """
    Creates a drawing-level DIH lookup containing the first
    description, mapped revision values and special categories.
    """

    lookup = {}

    if dih_df is None or dih_df.empty:
        return lookup

    for drawing_number, group in dih_df.groupby(
        "Normalized Drawing Number",
        sort=False,
        dropna=False,
    ):
        drawing_number = clean_value(
            drawing_number
        )

        if not drawing_number:
            continue

        descriptions = []
        phase_values = {}
        categories = []

        for _, row in group.iterrows():
            description = clean_description(
                row.get(
                    "DIH Description",
                    "",
                )
            )

            if description:
                descriptions.append(description)

            # Always define category for every DIH row.
            category = clean_value(
                row.get(
                    "DIH Category",
                    "",
                )
            )

            if (
                category in DIH_SPECIAL_CATEGORIES
                and category not in categories
            ):
                categories.append(category)

            mapped_headers = map_dih_revision_to_headers(
                revision=row.get(
                    "DIH Revision",
                    "",
                ),
                dynamic_headers=dynamic_headers,
                category=category,
            )

            for header, value in mapped_headers.items():
                phase_values[header] = (
                    combine_unique_values(
                        [
                            phase_values.get(
                                header,
                                "",
                            ),
                            value,
                        ]
                    )
                )

        lookup[drawing_number] = {
            "Description": first_non_blank_description(
                descriptions
            ),
            "Phase Values": phase_values,
            "Categories": categories,
        }

    return lookup


# ============================================================
# HEADER SORTING
# ============================================================

def get_special_header_parts(header):
    text = normalize_header(header)

    categories = sorted(
        DIH_SPECIAL_CATEGORIES,
        key=lambda item: len(
            normalize_header(item)
        ),
        reverse=True,
    )

    for category in categories:
        category_text = normalize_header(
            category
        )

        if text == f"{category_text} MOD":
            return category, "MOD"

        prefix = f"{category_text} "

        if text.startswith(prefix):
            marker = text[len(prefix):].strip()

            if parse_revision_marker(marker):
                return category, marker

    return "", ""


def get_header_group(header):
    """
    Returns the main stage/group name for a full column name.

    Examples:
        A2 Stage A1
            -> A2 STAGE

        C3 Stage HA1
            -> C3 STAGE

        C5 Stage 1 JB1
            -> C5 STAGE 1

        B4 Stage Latest Transmittal Form Version
            -> B4 STAGE
    """

    column_text = clean_value(header)

    if not column_text:
        return ""

    if column_text in {
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    }:
        return column_text

    if is_transmittal_column(column_text):
        group = get_transmittal_column_group(
            column_text
        )

        return clean_value(group)

    special_group, _ = get_special_header_parts(
        column_text
    )

    if special_group:
        return clean_value(special_group)

    stage_names = [
        group_info["name"]
        for group_info in DIH_GROUPS.values()
    ]

    stage_names.extend(
        [
            "PDC Existing",
            "PDC New",
            "PDC Additional New",
            "LIFTING BARRIER",
            "UFWL",
            "VARIATION ORDERS",
            "WTT-RE-OPEN",
        ]
    )

    # Longest first is important:
    # C5 Stage 1 must be checked before C5 Stage.
    stage_names = sorted(
        set(stage_names),
        key=lambda value: len(
            normalize_header(value)
        ),
        reverse=True,
    )

    normalized_column = normalize_header(
        column_text
    )

    for stage_name in stage_names:
        normalized_stage = normalize_header(
            stage_name
        )

        if (
            normalized_column
            == normalized_stage
            or normalized_column.startswith(
                normalized_stage + " "
            )
        ):
            return clean_value(stage_name)

    # Support MOD and AZ headers where the group is
    # already present but not detected above.
    if normalized_column.endswith(" MOD"):
        return clean_value(
            column_text[:-4]
        )

    if normalized_column.endswith(" AZ"):
        return clean_value(
            column_text[:-3]
        )

    # Never return an empty group. The column itself is
    # used as a safe fallback so the Excel header cannot
    # become blank.
    return column_text


def get_header_sort_key(header):
    text = normalize_header(header)
    group = normalize_header(
        get_header_group(header)
    )

    category, marker = get_special_header_parts(
        header
    )

    # Transmittal column goes after all revision columns
    # belonging to the same stage.
    if is_transmittal_column(header):
        return (
            REQUIRED_PHASE_ORDER.get(
                group,
                999,
            ),
            900003,
            text,
        )

    if category:
        order = REQUIRED_PHASE_ORDER.get(
            normalize_header(category),
            999,
        )

        if marker == "MOD":
            return order, 900001, text

        parsed = parse_revision_marker(marker)

        if parsed:
            letters, number, suffix = parsed

            return (
                order,
                number,
                letters,
                suffix,
                text,
            )

        return order, 999999, text

    phase_order = REQUIRED_PHASE_ORDER.get(
        group,
        999,
    )

    # MOD after revisions, before AZ.
    if text.endswith(" MOD"):
        return (
            phase_order,
            900001,
            text,
        )

    # AZ is the final revision.
    if text.endswith(" AZ"):
        return (
            phase_order,
            900002,
            text,
        )

    # Extract the revision marker after the stage name.
    marker_text = text

    if group and marker_text.startswith(
        group + " "
    ):
        marker_text = marker_text[
            len(group) + 1:
        ].strip()

    parsed = parse_revision_marker(
        marker_text
    )

    if parsed:
        letters, number, suffix = parsed

        return (
            phase_order,
            number,
            letters,
            suffix,
            text,
        )

    return (
        phase_order,
        999999,
        text,
    )


def sort_headers(headers):
    unique_headers = list(
        dict.fromkeys(
            [
                header
                for header in headers
                if clean_value(header)
            ]
        )
    )

    return sorted(
        unique_headers,
        key=get_header_sort_key,
    )


# ============================================================
# MODIFICATION REGISTER
# ============================================================

def find_modification_sheet(
    file_bytes,
    file_name,
):
    for sheet_name in get_sheet_names(
        file_bytes,
        file_name,
    ):
        try:
            header_row = (
                detect_modification_header_row(
                    file_bytes,
                    file_name,
                    sheet_name,
                )
            )

            preview = read_excel_sheet(
                file_bytes,
                file_name,
                sheet_name,
                header=header_row,
                nrows=5,
            )

            preview.columns = (
                make_unique_column_names(
                    preview.columns
                )
            )

            drawing_column = find_column(
                preview,
                DRAWING_NUMBER_ALIASES,
            )

            if drawing_column is None:
                drawing_column = find_column(
                    preview,
                    DRAWING_REF_ALIASES,
                )

            if (
                drawing_column is not None
                and find_column(
                    preview,
                    PHASE_ALIASES,
                ) is not None
                and find_column(
                    preview,
                    REVISION_ALIASES,
                ) is not None
                and find_column(
                    preview,
                    MODIFICATION_SHEET_NO_ALIASES,
                ) is not None
            ):
                return sheet_name

        except Exception:
            continue

    raise ValueError(
        "Could not find a valid Modification "
        "Sheet with Drawing, Phase and Rev "
        "columns."
    )


def read_modification_register(
    file_bytes,
    file_name,
):
    sheet_name = find_modification_sheet(
        file_bytes,
        file_name,
    )

    header_row = (
        detect_modification_header_row(
            file_bytes,
            file_name,
            sheet_name,
        )
    )

    df = read_excel_sheet(
        file_bytes,
        file_name,
        sheet_name,
        header=header_row,
    )

    df = df.dropna(axis=0, how="all")
    df = df.dropna(axis=1, how="all")

    if df.empty:
        raise ValueError(
            "The Modification Sheet is empty."
        )

    df.columns = make_unique_column_names(
        df.columns
    )

    drawing_ref_column = find_column(
        df,
        DRAWING_REF_ALIASES,
    )

    drawing_number_column = find_column(
        df,
        DRAWING_NUMBER_ALIASES,
    )

    drawing_column = (
        drawing_number_column
        or drawing_ref_column
    )

    description_column = find_column(
        df,
        DESCRIPTION_ALIASES,
    )

    phase_column = find_column(
        df,
        PHASE_ALIASES,
    )

    revision_column = find_column(
        df,
        REVISION_ALIASES,
    )

    modification_sheet_no_column = find_column(
        df,
        MODIFICATION_SHEET_NO_ALIASES,
    )

    if drawing_column is None:
        raise ValueError(
            "No affected drawing column was found."
        )

    if description_column is None:
        df["_Generated Description"] = ""
        description_column = (
            "_Generated Description"
        )

    if phase_column is None:
        raise ValueError(
            "No Phase column was found."
        )

    if revision_column is None:
        raise ValueError(
            "No Rev column was found."
        )
    if modification_sheet_no_column is None:
        raise ValueError(
            "No Modification Sheet No. column was found."
        )

    drawing_values = df[
        drawing_column
    ].tolist()

    if drawing_ref_column is not None:
        drawing_refs = df[
            drawing_ref_column
        ].tolist()
    else:
        drawing_refs = [""] * len(df)

    selected_drawings = []
    candidate_counts = []

    for drawing_value, drawing_ref in zip(
        drawing_values,
        drawing_refs,
    ):
        selected, count = (
            select_drawing_matching_ref(
                drawing_value,
                drawing_ref,
            )
        )

        selected_drawings.append(selected)
        candidate_counts.append(count)

    df["_SelectedDrawingNumber"] = (
        selected_drawings
    )

    df["_CandidateCount"] = candidate_counts

    df = df[
        df[
            "_SelectedDrawingNumber"
        ].astype(str).str.strip() != ""
    ].copy()

    if df.empty:
        raise ValueError(
            "No drawing numbers matching Drawing "
            "Ref were found."
        )

    df["_NormalizedSelectedDrawing"] = (
        df["_SelectedDrawingNumber"].apply(
            normalize_drawing_number
        )
    )

    df["_OriginalOrder"] = range(len(df))

    # ------------------------------------------------------------
    # Modification duplicate key
    #
    # The same affected drawing can legitimately appear more than
    # once when it is modified in different stages or revisions.
    #
    # Therefore, do not deduplicate using drawing number alone.
    # ------------------------------------------------------------
    df["_ModificationDuplicateKey"] = [
        create_modification_duplicate_key(
            drawing_number,
            modification_phase,
            modification_sheet_no,
            modification_revision,
        )
        for (
            drawing_number,
            modification_phase,
            modification_sheet_no,
            modification_revision,
        ) in zip(
            df["_NormalizedSelectedDrawing"],
            df[phase_column],
            df[modification_sheet_no_column],
            df[revision_column],
        )
    ]

    df = (
        df.sort_values(
            [
                "_ModificationDuplicateKey",
                "_CandidateCount",
                "_OriginalOrder",
            ],
            kind="stable",
        )
        .drop_duplicates(
            "_ModificationDuplicateKey",
            keep="first",
        )
        .sort_values(
            "_OriginalOrder",
            kind="stable",
        )
        .reset_index(drop=True)
    )

    output = pd.DataFrame(
        index=range(len(df))
    )

    output["Modification Drawing Number"] = (
        df["_SelectedDrawingNumber"].apply(
            clean_value
        )
    )

    output["Normalized Drawing Number"] = (
        df["_NormalizedSelectedDrawing"].apply(
            clean_value
        )
    )

    output["Description"] = (
        df[description_column].apply(
            clean_description
        )
    )

    output["_Modification Phase"] = (
        df[phase_column].apply(clean_value)
    )

    output["_Modification Revision"] = (
        df[revision_column].apply(clean_value)
    )

    output["_ModificationSheetNo"] = (
        df[modification_sheet_no_column].apply(
            clean_value
        )
    )

    modification_headers = []

    for phase in output[
        "_Modification Phase"
    ]:
        header = get_modification_mod_header(
            phase
        )

        if (
            header
            and header not in modification_headers
        ):
            modification_headers.append(header)

    for header in modification_headers:
        output[header] = ""

    for row_index, row in output.iterrows():
        header = get_modification_mod_header(
            row["_Modification Phase"]
        )

        revision = clean_value(
            row["_Modification Revision"]
        )

        if header and revision:
            output.loc[
                row_index,
                header,
            ] = revision

    return (
        output,
        sheet_name,
        header_row,
    )

def combine_duplicate_master_rows(df):
    """
    Combines duplicate drawing rows generated from multiple
    modification or DIH files.

    Text in phase/revision columns is combined uniquely.
    Descriptions are combined line by line.
    """

    if df is None or df.empty:
        return df

    working = df.copy()

    working["_NormalizedMergeDrawing"] = (
        working["C1252 Drawing"].apply(
            normalize_drawing_number
        )
    )

    output_rows = []

    for _, group in working.groupby(
        "_NormalizedMergeDrawing",
        sort=False,
    ):
        if group.empty:
            continue

        first_row = group.iloc[0].to_dict()

        for column in df.columns:
            if column in {
                "Drawing Ref",
                "C1252 Drawing",
            }:
                continue

            values = group[column].tolist()

            if column == "Description":
                # Keep only the description from the first record.
                # Do not concatenate descriptions from multiple
                # Modification Sheet files.
                first_row[column] = clean_description(
                    group.iloc[0][column]
                )
            else:
                first_row[column] = (
                    combine_unique_values(values)
                )

        output_rows.append(first_row)

    result = pd.DataFrame(
        output_rows,
        columns=df.columns,
    )

    return result.reset_index(drop=True)

# ============================================================
# CREATE FINAL MASTER
# ============================================================

def create_final_master(
    modification_df,
    dih_df,
):
    if modification_df is None:
        modification_df = pd.DataFrame(
            columns=[
                "Modification Drawing Number",
                "Normalized Drawing Number",
                "Description",
                "_Modification Phase",
                "_Modification Revision",
            ]
        )

    if dih_df is None:
        dih_df = pd.DataFrame(
            columns=[
                "DIH Drawing Number",
                "Normalized Drawing Number",
                "DIH Description",
                "DIH Category",
                "DIH Revision",
                "DIH Plan Type",
            ]
        )

    dih_headers = get_dynamic_dih_headers(
        dih_df
    )

    dih_lookup = create_dih_lookup(
        dih_df,
        dih_headers,
    )

    internal_columns = {
        "Modification Drawing Number",
        "Normalized Drawing Number",
        "Description",
        "_Modification Phase",
        "_Modification Revision",
        "_ModificationSheetNo",
        "_ModificationSourceFile",
        "_ModificationDuplicateKey",
    }

    modification_headers = [
        column
        for column in modification_df.columns
        if column not in internal_columns
    ]

    all_phase_headers = sort_headers(
        dih_headers + modification_headers
    )

    output_rows = []

    modification_numbers = set(
        modification_df[
            "Normalized Drawing Number"
        ]
        .astype(str)
        .map(normalize_drawing_number)
    )

    for _, mod_row in modification_df.iterrows():
        normalized_number = (
            normalize_drawing_number(
                mod_row.get(
                    "Normalized Drawing Number",
                    "",
                )
            )
        )

        drawing_number = clean_value(
            mod_row.get(
                "Modification Drawing Number",
                "",
            )
        )

        matched_dih = dih_lookup.get(
            normalized_number,
            {
                "Description": "",
                "Phase Values": {},
                "Categories": [],
            },
        )

        description = (
            clean_description(
                mod_row.get(
                    "Description",
                    "",
                )
            )
            or clean_description(
                matched_dih.get(
                    "Description",
                    "",
                )
            )
        )

        output_row = {
            "Drawing Ref": extract_drawing_ref(
                drawing_number
            ),
            "C1252 Drawing": drawing_number,
            "Description": description,
        }

        for header in all_phase_headers:
            output_row[header] = clean_value(
                mod_row.get(header, "")
            )

        modification_phase = clean_value(
            mod_row.get(
                "_Modification Phase",
                "",
            )
        )

        modification_revision = clean_value(
            mod_row.get(
                "_Modification Revision",
                "",
            )
        )

        normal_mod_header = (
            get_modification_mod_header(
                modification_phase
            )
        )

        special_categories = [
            category
            for category in matched_dih.get(
                "Categories",
                [],
            )
            if category in DIH_SPECIAL_CATEGORIES
        ]

        special_mod_headers = [
            create_special_mod_header(
                category
            )
            for category in special_categories
            if create_special_mod_header(
                category
            ) in all_phase_headers
        ]

        if (
            modification_revision
            and special_mod_headers
        ):
            if normal_mod_header in output_row:
                output_row[
                    normal_mod_header
                ] = ""

            for header in special_mod_headers:
                output_row[header] = (
                    combine_unique_values(
                        [
                            output_row.get(
                                header,
                                "",
                            ),
                            modification_revision,
                        ]
                    )
                )

        elif (
            modification_revision
            and normal_mod_header
        ):
            output_row[
                normal_mod_header
            ] = combine_unique_values(
                [
                    output_row.get(
                        normal_mod_header,
                        "",
                    ),
                    modification_revision,
                ]
            )

        for header, value in matched_dih.get(
            "Phase Values",
            {},
        ).items():
            if not clean_value(
                output_row.get(header, "")
            ):
                output_row[header] = value

        output_rows.append(output_row)

    for normalized_number, dih_data in (
        dih_lookup.items()
    ):
        if normalized_number in modification_numbers:
            continue

        dih_rows = dih_df[
            dih_df[
                "Normalized Drawing Number"
            ].map(
                normalize_drawing_number
            )
            == normalized_number
        ]

        drawing_number = first_non_blank(
            dih_rows[
                "DIH Drawing Number"
            ]
        )

        output_row = {
            "Drawing Ref": extract_drawing_ref(
                drawing_number
            ),
            "C1252 Drawing": drawing_number,
            "Description": clean_description(
                dih_data.get(
                    "Description",
                    "",
                )
            ),
        }

        for header in all_phase_headers:
            output_row[header] = ""

        for header, value in dih_data.get(
            "Phase Values",
            {},
        ).items():
            output_row[header] = value

        output_rows.append(output_row)

    columns = [
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    ] + all_phase_headers

    if not output_rows:
        return pd.DataFrame(columns=columns)

    final_df = pd.DataFrame(
        output_rows,
        columns=columns,
    )

    corrected_drawings = []

    for _, row in final_df.iterrows():
        selected, _ = (
            select_drawing_matching_ref(
                row.get(
                    "C1252 Drawing",
                    "",
                ),
                row.get(
                    "Drawing Ref",
                    "",
                ),
            )
        )

        corrected_drawings.append(
            selected
            or clean_value(
                row.get(
                    "C1252 Drawing",
                    "",
                )
            )
        )

    final_df["C1252 Drawing"] = (
        corrected_drawings
    )

    final_df["Drawing Ref"] = final_df[
        "C1252 Drawing"
    ].apply(extract_drawing_ref)

    final_df = combine_duplicate_master_rows(
        final_df
    )

    return sort_by_drawing_ref(final_df)

# ============================================================
# EXISTING MASTER DRAWING LIST
# ============================================================

def find_existing_master_sheet(file_bytes, file_name):
    sheet_names = get_sheet_names(
        file_bytes,
        file_name,
    )

    for sheet_name in sheet_names:
        if normalize_header(sheet_name) in {
            "MASTER DRAWING LIST",
            "MASTER DRAWING LISTS",
        }:
            return sheet_name

    for sheet_name in sheet_names:
        if "MASTER" in normalize_header(sheet_name):
            return sheet_name

    if sheet_names:
        return sheet_names[0]

    raise ValueError(
        "The existing Master Drawing List contains "
        "no worksheets."
    )
def combine_existing_master_header(
    main_header,
    sub_header,
):
    """
    Converts the two Excel header rows into one canonical
    internal column name.

    Examples:
        A2 Stage + MOD
            -> A2 Stage MOD

        A2 Stage + A2
            -> A2 Stage A2

        PDC Existing + A2 2026-08-27
            -> PDC Existing A2

        A2 Stage + Latest Transmittal Form Version
            -> A2 Stage Latest Transmittal Form Version
    """

    main_header = clean_value(main_header)
    sub_header = clean_value(sub_header)

    if not main_header:
        return sub_header

    if main_header in {
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    }:
        return main_header

    normalized_sub_header = normalize_header(
        sub_header
    )

    # Important: MOD is not a revision marker, so it must
    # be handled explicitly.
    if normalized_sub_header in {
        "MOD",
        "AZ",
    }:
        return f"{main_header} {normalized_sub_header}"

    # Preserve transmittal columns when reading an existing
    # workbook.
    if (
        normalized_sub_header
        == normalize_header(
            TRANSMITTAL_COLUMN_SUFFIX
        )
    ):
        return (
            f"{main_header} "
            f"{TRANSMITTAL_COLUMN_SUFFIX}"
        )

    revision = extract_revision_from_text(
        sub_header
    )

    if revision:
        return f"{main_header} {revision}"

    # If the subheader contains an approval date, the
    # revision will normally already have been extracted.
    # Otherwise retain the original main header.
    return main_header


def read_existing_master_sheet_data(
    file_bytes,
    file_name,
    sheet_name,
):
    """
    Reads an existing generated Master Drawing List.

    The workbook has two header rows:
        Row 1 = grouped/main headers
        Row 2 = revision/subheaders

    Merged cells appear as blank or Unnamed columns when
    read by pandas, so the first header row is forward-filled.
    """

    raw_df = read_excel_sheet(
        file_bytes,
        file_name,
        sheet_name,
        header=None,
    )

    if raw_df.empty:
        raise ValueError(
            "The existing Master Drawing List "
            "worksheet is empty."
        )

    if len(raw_df) < 2:
        raise ValueError(
            "The existing Master Drawing List must contain "
            "two header rows."
        )

    main_headers = [
        clean_value(value)
        for value in raw_df.iloc[0].tolist()
    ]

    sub_headers = [
        clean_value(value)
        for value in raw_df.iloc[1].tolist()
    ]

    # Forward-fill merged group headers.
    current_group = ""

    for index, value in enumerate(main_headers):
        if value and not value.startswith("Unnamed"):
            current_group = value
        elif current_group:
            main_headers[index] = current_group

    combined_headers = []

    for index in range(len(main_headers)):
        main_header = clean_value(
            main_headers[index]
        )

        raw_sub_header = clean_value(
            sub_headers[index]
        )

        if (
            not main_header
            or main_header.startswith("Unnamed")
        ):
            main_header = raw_sub_header

        final_header = combine_existing_master_header(
            main_header,
            raw_sub_header,
        )

        combined_headers.append(
            final_header
        )

    df = raw_df.iloc[2:].copy()
    df.columns = combined_headers

    df = df.dropna(
        axis=0,
        how="all",
    )

    df = df.dropna(
        axis=1,
        how="all",
    )

    # Remove any repeated header row accidentally read as data.
    if not df.empty:
        first_row_text = " ".join(
            clean_value(value)
            for value in df.iloc[0].tolist()
        ).upper()

        if (
            "DRAWING REF" in first_row_text
            or "C1252 DRAWING" in first_row_text
        ):
            df = df.iloc[1:].copy()

    return df.reset_index(drop=True)


def extract_existing_approval_data(
    file_bytes,
    file_name,
    sheet_name,
):
    """
    Reads approval dates from an existing Master Drawing List.

    Returns:

        {
            "Header Statuses": {
                "PDC Existing A2": {
                    "Status": "APPROVED",
                    "Approval Date": "2026-08-27",
                }
            },

            "Cell Statuses": {
                (
                    "1252/W/THW/ALT/R05/004",
                    "PDC Existing MOD",
                ): {
                    "Status": "APPROVED",
                    "Approval Date": "2026-08-27",
                }
            }
        }

    A revision/header or data cell is considered approved when
    it contains an approval date.
    """

    extension = Path(file_name).suffix.lower()

    if extension not in {".xlsx", ".xlsm"}:
        return {
            "Header Statuses": {},
            "Cell Statuses": {},
        }

    workbook = load_workbook(
        io.BytesIO(file_bytes),
        read_only=False,
        data_only=False,
    )

    try:
        if sheet_name not in workbook.sheetnames:
            return {
                "Header Statuses": {},
                "Cell Statuses": {},
            }

        worksheet = workbook[sheet_name]

        if worksheet.max_row < 2:
            return {
                "Header Statuses": {},
                "Cell Statuses": {},
            }

        main_headers = []
        sub_headers = []

        current_group = ""

        # ----------------------------------------------------
        # Read and forward-fill the first header row
        # ----------------------------------------------------

        for column_index in range(
            1,
            worksheet.max_column + 1,
        ):
            main_header = clean_value(
                worksheet.cell(
                    row=1,
                    column=column_index,
                ).value
            )

            sub_header = clean_value(
                worksheet.cell(
                    row=2,
                    column=column_index,
                ).value
            )

            if (
                main_header
                and not main_header.startswith("Unnamed")
            ):
                current_group = main_header

            if not main_header:
                main_header = current_group

            main_headers.append(main_header)
            sub_headers.append(sub_header)

        # ----------------------------------------------------
        # Create canonical column names
        #
        # MOD must be preserved. Otherwise:
        #
        #     A2 Stage + MOD
        #
        # incorrectly becomes:
        #
        #     A2 Stage
        #
        # and approval/cell-status matching will use the
        # wrong column name.
        # ----------------------------------------------------

        combined_headers = []

        for main_header, sub_header in zip(
            main_headers,
            sub_headers,
        ):
            if not main_header:
                combined_headers.append("")
                continue

            combined_header = (
                combine_existing_master_header(
                    main_header,
                    sub_header,
                )
            )

            combined_headers.append(
                clean_value(combined_header)
            )

        combined_headers = make_unique_column_names(
            combined_headers
        )

        # ----------------------------------------------------
        # Find the drawing-number column
        # ----------------------------------------------------

        drawing_column_index = None

        for index, header in enumerate(
            combined_headers,
            start=1,
        ):
            header_text = normalize_header(header)

            if (
                "C1252 DRAWING" in header_text
                or header_text in {
                    "DRAWING NUMBER",
                    "DRAWING NO",
                    "DRAWING NO.",
                }
            ):
                drawing_column_index = index
                break

        header_statuses = {}
        cell_statuses = {}

        # ----------------------------------------------------
        # Read approval dates from revision headers
        # ----------------------------------------------------

        for column_index, (
            combined_header,
            sub_header,
        ) in enumerate(
            zip(
                combined_headers,
                sub_headers,
            ),
            start=1,
        ):
            if not combined_header:
                continue

            if normalize_header(
                combined_header
            ) in {
                "DRAWING REF",
                "C1252 DRAWING",
                "DESCRIPTION",
            }:
                continue

            approval_date = (
                extract_approval_date_from_text(
                    sub_header
                )
            )

            if not approval_date:
                continue

            header_statuses[
                combined_header
            ] = {
                "Status": "APPROVED",
                "Approval Date": approval_date,
            }

        # ----------------------------------------------------
        # Read approval dates from drawing/modification cells
        # ----------------------------------------------------

        if drawing_column_index is not None:
            for row_index in range(
                3,
                worksheet.max_row + 1,
            ):
                drawing_value = worksheet.cell(
                    row=row_index,
                    column=drawing_column_index,
                ).value

                drawing_key = normalize_drawing_number(
                    drawing_value
                )

                if not drawing_key:
                    continue

                for column_index, combined_header in enumerate(
                    combined_headers,
                    start=1,
                ):
                    if not combined_header:
                        continue

                    if column_index == drawing_column_index:
                        continue

                    cell_value = worksheet.cell(
                        row=row_index,
                        column=column_index,
                    ).value

                    approval_date = (
                        extract_approval_date_from_text(
                            cell_value
                        )
                    )

                    if not approval_date:
                        continue

                    cell_statuses[
                        (
                            drawing_key,
                            combined_header,
                        )
                    ] = {
                        "Status": "APPROVED",
                        "Approval Date": approval_date,
                    }

        return {
            "Header Statuses": header_statuses,
            "Cell Statuses": cell_statuses,
        }

    finally:
        workbook.close()

def extract_existing_approval_data_by_worksheet(
    file_bytes,
    file_name,
):
    """
    Reads approval dates independently from every worksheet.

    Returns:
        {
            "Master Drawing List": {
                "Header Statuses": {...},
                "Cell Statuses": {...},
            },
            "THW": {
                "Header Statuses": {...},
                "Cell Statuses": {...},
            },
        }
    """

    extension = Path(file_name).suffix.lower()

    if extension not in {".xlsx", ".xlsm"}:
        return {}

    workbook = load_workbook(
        io.BytesIO(file_bytes),
        read_only=False,
        data_only=False,
    )

    approval_data_by_sheet = {}

    try:
        for worksheet_name in workbook.sheetnames:
            try:
                approval_data_by_sheet[worksheet_name] = (
                    extract_existing_approval_data(
                        file_bytes,
                        file_name,
                        worksheet_name,
                    )
                )

            except Exception as error:
                log_warning(
                    "Could not read approval data from worksheet",
                    worksheet=worksheet_name,
                    error_type=type(error).__name__,
                )

                approval_data_by_sheet[worksheet_name] = {
                    "Header Statuses": {},
                    "Cell Statuses": {},
                }

    finally:
        workbook.close()

    return approval_data_by_sheet


def get_existing_approval_data_for_worksheet(
    existing_approval_data,
    worksheet_name,
):
    """
    Returns approval data for one specific output worksheet.
    """

    if not existing_approval_data:
        return {
            "Header Statuses": {},
            "Cell Statuses": {},
        }

    # New format: approval data stored by worksheet.
    by_worksheet = existing_approval_data.get(
        "By Worksheet",
        {},
    )

    if by_worksheet:
        requested_name = normalize_header(
            worksheet_name
        )

        for source_name, approval_data in (
            by_worksheet.items()
        ):
            if normalize_header(source_name) == requested_name:
                return approval_data

    # Backward-compatible fallback for old structure.
    if (
        "Header Statuses" in existing_approval_data
        or "Cell Statuses" in existing_approval_data
    ):
        return existing_approval_data

    return {
        "Header Statuses": {},
        "Cell Statuses": {},
    }


def get_existing_approval_data_for_station(
    existing_approval_data,
    station_name,
):
    """
    Returns approval data for the requested output station worksheet.

    Exceptional grouping rules:
        AIR + AWE -> AIR
        SUN + YOT -> SUN & YOT
        TCE + TCETUC -> TCE
        SHD + AB11 + AB3 + MB = SHD

    Approval data from matching legacy worksheets is combined.
    """

    empty_result = {
        "Header Statuses": {},
        "Cell Statuses": {},
    }

    if not existing_approval_data:
        return empty_result

    by_worksheet = existing_approval_data.get(
        "By Worksheet",
        {},
    )

    if not by_worksheet:
        return empty_result

    requested_station = get_station_worksheet_name(
        station_name
    )

    header_statuses = {}
    cell_statuses = {}

    matching_worksheets = []

    for worksheet_name, approval_data in by_worksheet.items():
        source_station = get_station_worksheet_name(
            worksheet_name
        )

        if normalize_header(source_station) == normalize_header(
            requested_station
        ):
            matching_worksheets.append(
                (
                    worksheet_name,
                    approval_data,
                )
            )

    # Process legacy worksheets first, then the exact combined
    # worksheet name last when it exists.
    matching_worksheets.sort(
        key=lambda item: (
            normalize_header(item[0])
            == normalize_header(requested_station)
        )
    )

    for _, approval_data in matching_worksheets:
        header_statuses.update(
            approval_data.get(
                "Header Statuses",
                {},
            )
        )

        cell_statuses.update(
            approval_data.get(
                "Cell Statuses",
                {},
            )
        )

    return {
        "Header Statuses": header_statuses,
        "Cell Statuses": cell_statuses,
    }


def read_existing_master_list(
    file_bytes,
    file_name,
):
    sheet_name = find_existing_master_sheet(
        file_bytes,
        file_name,
    )

    df = read_existing_master_sheet_data(
        file_bytes,
        file_name,
        sheet_name,
    )

    if df.empty:
        raise ValueError(
            "The existing Master Drawing List "
            "worksheet contains no data."
        )

    df.columns = make_unique_column_names(
        df.columns
    )

    drawing_column = find_column(
        df,
        DRAWING_NUMBER_ALIASES,
    )

    if drawing_column is None:
        raise ValueError(
            "The existing Master Drawing List does "
            "not contain a C1252 drawing column."
        )

    description_column = find_column(
        df,
        DESCRIPTION_ALIASES,
    )

    drawing_ref_column = find_column(
        df,
        DRAWING_REF_ALIASES,
    )

    rename_columns = {
        drawing_column: "C1252 Drawing",
    }

    if (
        description_column is not None
        and description_column != "Description"
    ):
        rename_columns[
            description_column
        ] = "Description"

    if (
        drawing_ref_column is not None
        and drawing_ref_column != "Drawing Ref"
    ):
        rename_columns[
            drawing_ref_column
        ] = "Drawing Ref"

    df = df.rename(
        columns=rename_columns
    )

    if "Drawing Ref" not in df.columns:
        df["Drawing Ref"] = ""

    if "Description" not in df.columns:
        df["Description"] = ""

    df["C1252 Drawing"] = df[
        "C1252 Drawing"
    ].apply(clean_value)

    df["Normalized Drawing Number"] = df[
        "C1252 Drawing"
    ].apply(normalize_drawing_number)

    df["Drawing Ref"] = df[
        "Drawing Ref"
    ].apply(clean_value)

    df["Description"] = df[
        "Description"
    ].apply(clean_description)

    df = df[
        df[
            "Normalized Drawing Number"
        ].astype(str).str.strip() != ""
    ].copy()

    if df.empty:
        raise ValueError(
            "The existing Master Drawing List "
            "contains no valid drawing numbers."
        )

    df = (
        df.drop_duplicates(
            "Normalized Drawing Number",
            keep="first",
        )
        .reset_index(drop=True)
    )

    standard_columns = [
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
        "Normalized Drawing Number",
    ]

    remaining_columns = [
        column
        for column in df.columns
        if column not in standard_columns
    ]

    df = df[
        standard_columns
        + remaining_columns
    ]

    return df, sheet_name


def merge_with_existing_master(
    existing_df,
    new_df,
):
    """
    New drawings are placed above existing drawings.
    Existing non-blank values are preserved.
    """

    if (
        existing_df is None
        or existing_df.empty
    ):
        return (
            new_df.copy()
            .reset_index(drop=True)
        )

    if new_df is None or new_df.empty:
        return (
            existing_df.copy()
            .reset_index(drop=True)
        )

    existing = existing_df.copy()
    new = new_df.copy()

    for dataframe in [existing, new]:
        for column in [
            "Drawing Ref",
            "C1252 Drawing",
            "Description",
        ]:
            if column not in dataframe.columns:
                dataframe[column] = ""

        dataframe["C1252 Drawing"] = (
            dataframe["C1252 Drawing"]
            .apply(clean_value)
        )

        dataframe[
            "Normalized Drawing Number"
        ] = (
            dataframe["C1252 Drawing"]
            .apply(normalize_drawing_number)
        )

    existing = existing[
        existing[
            "Normalized Drawing Number"
        ].astype(str).str.strip() != ""
    ].copy()

    new = new[
        new[
            "Normalized Drawing Number"
        ].astype(str).str.strip() != ""
    ].copy()

    existing = (
        existing.drop_duplicates(
            "Normalized Drawing Number",
            keep="first",
        )
        .reset_index(drop=True)
    )

    new = (
        new.drop_duplicates(
            "Normalized Drawing Number",
            keep="first",
        )
        .reset_index(drop=True)
    )

    standard_columns = [
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    ]

    all_columns = []

    for column in (
        standard_columns
        + list(new.columns)
        + list(existing.columns)
    ):
        if (
            column
            and column != "Normalized Drawing Number"
            and column not in all_columns
        ):
            all_columns.append(column)

    ordered_columns = standard_columns + [
        column
        for column in all_columns
        if column not in standard_columns
    ]

    existing = existing.reindex(
        columns=ordered_columns
    ).fillna("")

    new = new.reindex(
        columns=ordered_columns
    ).fillna("")

    existing_lookup = {}

    for index, row in existing.iterrows():
        key = normalize_drawing_number(
            row.get(
                "C1252 Drawing",
                "",
            )
        )

        if key:
            existing_lookup[key] = index

    new_rows = []

    for _, new_row in new.iterrows():
        key = normalize_drawing_number(
            new_row.get(
                "C1252 Drawing",
                "",
            )
        )

        if not key:
            continue

        if key not in existing_lookup:
            new_rows.append(
                new_row.to_dict()
            )
            continue

        existing_index = existing_lookup[key]

        for column in ordered_columns:
            if column in {
                "Drawing Ref",
                "C1252 Drawing",
            }:
                continue

            new_value = clean_value(
                new_row.get(column, "")
            )

            old_value = clean_value(
                existing.loc[
                    existing_index,
                    column,
                ]
            )

            if not new_value:
                continue

            if not old_value:
                existing.loc[
                    existing_index,
                    column,
                ] = new_value
            elif column == "Description":
                # Keep the existing description.
                # Do not concatenate descriptions from DIH or
                # Modification registers.
                existing.loc[
                    existing_index,
                    column,
                ] = old_value

            else:
                existing.loc[
                    existing_index,
                    column,
                ] = combine_unique_values(
                    [
                        old_value,
                        new_value,
                    ]
                )

    new_only_df = pd.DataFrame(
        new_rows,
        columns=ordered_columns,
    )

    if not new_only_df.empty:
        new_only_df["Drawing Ref"] = (
            new_only_df[
                "C1252 Drawing"
            ].apply(extract_drawing_ref)
        )

    merged_df = pd.concat(
        [
            new_only_df,
            existing[ordered_columns],
        ],
        ignore_index=True,
    ).fillna("")

    merged_df["_MergeDrawingKey"] = (
        merged_df["C1252 Drawing"].apply(
            normalize_drawing_number
        )
    )

    return (
        merged_df.drop_duplicates(
            "_MergeDrawingKey",
            keep="first",
        )
        .drop(
            columns=["_MergeDrawingKey"]
        )
        .reset_index(drop=True)
    )


# ============================================================
# TRANSMITTAL COLUMN INTEGRATION
# ============================================================

def get_stage_groups_from_columns(df):
    groups = []
    seen = set()

    if df is None or df.empty:
        return groups

    excluded_columns = {
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
        "Normalized Drawing Number",
    }

    for column in df.columns:
        if column in excluded_columns:
            continue

        if is_transmittal_column(column):
            continue

        group = get_header_group(column)
        key = normalize_header(group)

        if key and key not in seen:
            seen.add(key)
            groups.append(group)

    return groups


def get_active_stage_groups_for_row(
    row,
    stage_groups,
):
    active_groups = []

    for stage_group in stage_groups:
        for column in row.index:
            if is_transmittal_column(column):
                continue

            if (
                get_header_group(column)
                == stage_group
                and clean_value(
                    row.get(column, "")
                )
            ):
                active_groups.append(stage_group)
                break

    return active_groups

def is_detail_design_transmittal(row):
    """
    Returns True only when the transmittal description/source text
    contains CIRCUIT BOOK or SER LAYOUT.
    """

    text = clean_value(
        row.get("Source Row", "")
    )

    text = normalize_header(text)

    return (
        "CIRCUIT BOOK" in text
        or "SER LAYOUT" in text
    )

def build_transmittal_scope_lookup(
    transmittal_df,
):
    """
    Builds a global approval lookup directly from the
    transmittal records.

    The lookup does not depend on the drawing existing in
    the Master Drawing List.

    Key:

        Station + Revision + Stage
    """

    lookup = {}

    if (
        transmittal_df is None
        or transmittal_df.empty
    ):
        return lookup

    required_columns = {
        "Normalized Drawing Number",
        "Transmittal Revision",
        "Status",
        "Approval Date",
    }

    if not required_columns.issubset(
        transmittal_df.columns
    ):
        return lookup

    for order, (_, row) in enumerate(
        transmittal_df.iterrows()
    ):
        if is_modification_transmittal(row):
            continue

        drawing_number = normalize_drawing_number(
            row.get(
                "Normalized Drawing Number",
                "",
            )
        )

        revision = normalize_transmittal_revision(
            row.get(
                "Transmittal Revision",
                "",
            )
        )

        if not drawing_number or not revision:
            continue

        station = normalize_header(
            extract_area_from_drawing_number(
                drawing_number
            )
        )

        if not station:
            continue

        stage = clean_value(
            row.get(
                "Transmittal Stage",
                "",
            )
        )

        detected_stage = detect_stage_from_description(
            stage
        )

        if detected_stage:
            stage = detected_stage

        stage_key = normalize_stage_text(
            stage
        )

        key = (
            station,
            revision,
            stage_key,
        )

        candidate = {
            "Status": normalize_approval_status(
                row.get(
                    "Status",
                    "",
                )
            ),
            "Approval Date": format_approval_date(
                row.get(
                    "Approval Date",
                    "",
                )
            ),
            "Transmittal Stage": stage,
            "Is Detail Design": is_detail_design_transmittal(
                row
            ),
            "_TransmittalOrder": row.get(
                "_TransmittalOrder",
                order,
            ),
        }

        existing = lookup.get(key)

        if existing is None:
            lookup[key] = candidate
            continue

        latest = get_latest_transmittal_record(
            pd.DataFrame(
                [
                    existing,
                    candidate,
                ]
            )
        )

        if latest is not None:
            lookup[key] = {
                # Every matched transmittal is displayed as approved.
                "Status": "APPROVED",
                "Approval Date": format_approval_date(
                    latest.get(
                        "Approval Date",
                        "",
                    )
                ),
                "Transmittal Stage": clean_value(
                    latest.get(
                        "Transmittal Stage",
                        stage,
                    )
                ),
                # Important: retain the CIRCUIT BOOK / SER LAYOUT flag.
                "Is Detail Design": bool(
                    latest.get(
                        "Is Detail Design",
                        False,
                    )
                ),
                "_TransmittalOrder": latest.get(
                    "_TransmittalOrder",
                    order,
                ),
            }

    return lookup

def add_transmittal_status_data(
    final_df,
    transmittal_df,
):
    """
    Stores transmittal status by:

        Station + Revision + Stage

    If the stage cannot be identified from the description,
    the record is also stored using an empty stage key.

    This allows the later matching logic to fall back to:

        Station + Revision

    Example drawing:

        1252/W/THW/ALT/R05/004

    Station:

        THW
    """

    result = final_df.copy()

    result["_Transmittal Scope Status"] = [
        {}
        for _ in range(len(result))
    ]

    if (
        transmittal_df is None
        or transmittal_df.empty
    ):
        return result

    required_columns = {
        "Normalized Drawing Number",
        "Transmittal Revision",
        "Status",
        "Approval Date",
    }

    if not required_columns.issubset(
        transmittal_df.columns
    ):
        return result

    scope_records = {}

    for order, (_, row) in enumerate(
        transmittal_df.iterrows()
    ):
        drawing_number = normalize_drawing_number(
            row.get(
                "Normalized Drawing Number",
                "",
            )
        )

        revision = normalize_transmittal_revision(
            row.get(
                "Transmittal Revision",
                "",
            )
        )

        if not drawing_number or not revision:
            continue

        # Example:
        # 1252/W/THW/ALT/R05/004
        # parts[2] = THW
        station = normalize_header(
            extract_area_from_drawing_number(
                drawing_number
            )
        )

        if not station:
            continue

        stage = clean_value(
            row.get(
                "Transmittal Stage",
                "",
            )
        )

        # Try to detect a proper stage from the description.
        detected_stage = detect_stage_from_description(
            stage
        )

        if detected_stage:
            stage = detected_stage

        stage_key = normalize_stage_text(stage)

        # Empty stage is intentional. It means that the
        # revision-only fallback will be used later.
        key = (
            station,
            revision,
            stage_key,
        )

        candidate = {
            "Status": "APPROVED",
            "Approval Date": format_approval_date(
                row.get(
                    "Approval Date",
                    "",
                )
            ),
            "Transmittal Stage": stage,
            "Is Detail Design": is_detail_design_transmittal(
                row
            ),
            "_TransmittalOrder": row.get(
                "_TransmittalOrder",
                order,
            ),
        }

        existing = scope_records.get(key)

        if existing is None:
            scope_records[key] = candidate
            continue

        # Select the latest status/date when more than
        # one transmittal has the same station, revision
        # and stage.
        candidates = pd.DataFrame(
            [
                existing,
                candidate,
            ]
        )

        latest = get_latest_transmittal_record(
            candidates
        )

        if latest is not None:
            scope_records[key] = {
                "Status": "APPROVED",
                "Approval Date": format_approval_date(
                    latest.get(
                        "Approval Date",
                        "",
                    )
                ),
                "Transmittal Stage": clean_value(
                    latest.get(
                        "Transmittal Stage",
                        stage,
                    )
                ),
                "Is Detail Design": bool(
                    latest.get(
                        "Is Detail Design",
                        False,
                    )
                ),
                "_TransmittalOrder": latest.get(
                    "_TransmittalOrder",
                    order,
                ),
            }

    # Every worksheet row gets access to the same
    # station/revision/status lookup.
    for index in result.index:
        result.at[
            index,
            "_Transmittal Scope Status",
        ] = scope_records.copy()

    return result


def reorder_transmittal_columns(
    df,
    stage_groups,
):
    base_columns = [
        column
        for column in [
            "Drawing Ref",
            "C1252 Drawing",
            "Description",
        ]
        if column in df.columns
    ]

    ordinary_columns = [
        column
        for column in df.columns
        if column not in base_columns
        and not is_transmittal_column(column)
    ]

    ordered_columns = list(base_columns)
    inserted_groups = set()

    for index, column in enumerate(
        ordinary_columns
    ):
        ordered_columns.append(column)

        group = get_header_group(column)

        following_same_group = any(
            get_header_group(next_column)
            == group
            for next_column in ordinary_columns[
                index + 1:
            ]
        )

        if (
            not following_same_group
            and group in stage_groups
            and group not in inserted_groups
        ):
            transmittal_column = (
                create_transmittal_column_name(
                    group
                )
            )

            if transmittal_column in df.columns:
                ordered_columns.append(
                    transmittal_column
                )
                inserted_groups.add(group)

    for stage_group in stage_groups:
        transmittal_column = (
            create_transmittal_column_name(
                stage_group
            )
        )

        if (
            transmittal_column in df.columns
            and transmittal_column
            not in ordered_columns
        ):
            ordered_columns.append(
                transmittal_column
            )

    return df.reindex(
        columns=[
            column
            for column in ordered_columns
            if column in df.columns
        ]
    )


# ============================================================
# VISIBLE COLUMNS
# ============================================================
def has_pending_modification_transmittal_value(
    df,
    mod_column,
    transmittal_record_lookup,
):
    """
    Returns True when a modification transmittal will write a value
    into the specified MOD column for at least one drawing in the
    current worksheet.
    """

    if (
        df is None
        or df.empty
        or not transmittal_record_lookup
        or "C1252 Drawing" not in df.columns
    ):
        return False

    worksheet_drawings = {
        normalize_drawing_number(drawing_number)
        for drawing_number in df["C1252 Drawing"]
        if normalize_drawing_number(drawing_number)
    }

    if not worksheet_drawings:
        return False

    mod_stage_key = normalize_stage_text(
        get_header_group(mod_column)
    )

    for (
        drawing_number,
        revision,
        transmittal_stage,
    ), record in transmittal_record_lookup.items():

        if drawing_number not in worksheet_drawings:
            continue

        if not record.get("Is Modification", False):
            continue

        if not clean_value(revision):
            continue

        if (
            normalize_stage_text(transmittal_stage)
            == mod_stage_key
        ):
            return True

    return False

def get_visible_columns_for_sheet(
    df,
    transmittal_df=None,
    transmittal_record_lookup=None,
):
    """
    Returns only columns that contain values in the current worksheet.

    MOD columns are removed when they are blank for every drawing in
    the current worksheet. A MOD column is retained when a matching
    modification transmittal will write a value into that worksheet.
    """

    base_columns = [
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    ]

    internal_columns = {
        "Normalized Drawing Number",
        "_Transmittal Status Data",
        "_Transmittal Scope Status",
    }

    phase_columns = [
        column
        for column in df.columns
        if (
            column not in base_columns
            and column not in internal_columns
        )
    ]

    active_columns = set()
    active_groups = set()

    # Identify columns containing actual values in this worksheet.
    for column in phase_columns:
        values = (
            df[column]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        if (values != "").any():
            active_columns.add(column)

            if not is_transmittal_column(column):
                active_groups.add(
                    get_header_group(column)
                )

    visible_phase_columns = []

    for column in phase_columns:
        # Keep a transmittal column only when its stage/group
        # has at least one active value in this worksheet.
        if is_transmittal_column(column):
            source_group = get_transmittal_column_group(
                column
            )

            if source_group in active_groups:
                visible_phase_columns.append(column)

            continue

        # Keep normal revision, AZ and MOD columns only where
        # the current worksheet has at least one real value.
        if column in active_columns:
            visible_phase_columns.append(column)
            continue

        # A modification transmittal value is written after the
        # worksheet dataframe is exported. Keep that MOD column
        # only when it applies to a drawing in this worksheet.
        if (
            normalize_header(column).endswith(" MOD")
            and has_pending_modification_transmittal_value(
                df,
                column,
                transmittal_record_lookup,
            )
        ):
            visible_phase_columns.append(column)

    return [
        column
        for column in base_columns
        if column in df.columns
    ] + sort_headers(
        visible_phase_columns
    )


# ============================================================
# EXCEL OUTPUT
# ============================================================

def create_header_formats(workbook):
    colors = [
        "#D98BC3",
        "#00A6D6",
        "#F4B183",
        "#70AD47",
        "#FFC000",
        "#5B9BD5",
        "#A5A5A5",
        "#ED7D31",
        "#8064A2",
        "#4472C4",
        "#C55A11",
        "#548235",
    ]

    return [
        workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": color,
                "border": 1,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        for color in colors
    ]

def get_excel_subheader(column_name):
    """
    Gets the second-row Excel header.

    Examples:
        A2 Stage A1
            -> A1

        A2 Stage AZ
            -> AZ

        B4 Stage Latest Transmittal Form Version
            -> Latest Transmittal Form Version
    """

    if column_name in {
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    }:
        return column_name

    column_text = clean_value(column_name)
    group = clean_value(
        get_header_group(column_text)
    )

    if not group:
        return column_text

    prefix = f"{group} "

    if column_text.upper().startswith(
        prefix.upper()
    ):
        subheader = column_text[
            len(prefix):
        ].strip()

        return subheader or group

    return column_text


def create_modification_details_dataframe(
    modification_df,
):
    """
    Creates the Modification Details worksheet.

    The worksheet shows:
        Drawing Ref
        C1252 Drawing
        Description
        Modification Stage
        Modification Revision
        Modification Sheet No.

    Modification Sheet No. contains the worksheet name from
    the uploaded Modification REGISTER.
    """

    output_columns = [
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
        "Modification Stage",
        "Modification Revision",
        "Modification Sheet No.",
    ]

    if (
        modification_df is None
        or modification_df.empty
    ):
        return pd.DataFrame(
            columns=output_columns
        )

    result = pd.DataFrame()

    # --------------------------------------------------------
    # Drawing number
    # --------------------------------------------------------

    if (
        "Modification Drawing Number"
        in modification_df.columns
    ):
        result["C1252 Drawing"] = (
            modification_df[
                "Modification Drawing Number"
            ].apply(clean_value)
        )

    elif "C1252 Drawing" in modification_df.columns:
        result["C1252 Drawing"] = (
            modification_df[
                "C1252 Drawing"
            ].apply(clean_value)
        )

    else:
        result["C1252 Drawing"] = ""

    result["Drawing Ref"] = (
        result["C1252 Drawing"].apply(
            extract_drawing_ref
        )
    )

    # --------------------------------------------------------
    # Description
    # --------------------------------------------------------

    if "Description" in modification_df.columns:
        result["Description"] = (
            modification_df[
                "Description"
            ].apply(clean_description)
        )
    else:
        result["Description"] = ""

    # --------------------------------------------------------
    # Modification stage
    # --------------------------------------------------------

    if (
        "_Modification Phase"
        in modification_df.columns
    ):
        result["Modification Stage"] = (
            modification_df[
                "_Modification Phase"
            ].apply(clean_value)
        )
    else:
        result["Modification Stage"] = ""

    # --------------------------------------------------------
    # Modification revision
    # --------------------------------------------------------

    if (
        "_Modification Revision"
        in modification_df.columns
    ):
        result["Modification Revision"] = (
            modification_df[
                "_Modification Revision"
            ].apply(clean_value)
        )
    else:
        result["Modification Revision"] = ""

    # --------------------------------------------------------
    # Modification worksheet name
    # --------------------------------------------------------

    if (
        "_ModificationSheetNo"
        in modification_df.columns
    ):
        result["Modification Sheet No."] = (
            modification_df[
                "_ModificationSheetNo"
            ].apply(clean_value)
        )
    else:
        result["Modification Sheet No."] = ""

    return result[
        output_columns
    ].reset_index(drop=True)


def format_information_worksheet(
    workbook,
    worksheet,
    df,
):
    header_format = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#215C84",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )

    for index, column in enumerate(
        df.columns
    ):
        worksheet.write(
            0,
            index,
            column,
            header_format,
        )

    worksheet.freeze_panes(1, 0)
    worksheet.set_row(0, 30)

    for index, column in enumerate(
        df.columns
    ):
        worksheet.set_column(
            index,
            index,
            min(
                max(len(str(column)) + 2, 15),
                60,
            ),
        )
def get_subheader_revision_value(column_name):
    """
    Returns the revision represented by a column.

    Examples:
        A3 Stage 1 A3 -> A3
        B4 Stage D2   -> D2
        A2 Stage AZ   -> AZ
    """

    subheader = normalize_transmittal_revision(
        get_excel_subheader(column_name)
    )

    return subheader


def get_status_for_column(
    column_name,
    sheet_df=None,
    transmittal_lookup=None,
    first_revision_columns=None,
):
    """
    Returns:

        Status,
        Approval Date,
        Is Detail Design,
        Transmittal Match Found

    Detail Design is True only where the matched transmittal
    description contains CIRCUIT BOOK or SER LAYOUT.
    """

    revision = get_subheader_revision_value(
        column_name
    )

    if not revision or not transmittal_lookup:
        return "", "", False, False

    column_stage_key = normalize_stage_text(
        get_header_group(column_name)
    )

    if not column_stage_key:
        return "", "", False, False

    # Find stations in the current worksheet.
    sheet_stations = set()

    if (
        sheet_df is not None
        and not sheet_df.empty
        and "C1252 Drawing" in sheet_df.columns
    ):
        for drawing_number in sheet_df["C1252 Drawing"]:
            station = extract_area_from_drawing_number(
                drawing_number
            )

            if station:
                sheet_stations.add(
                    normalize_header(station)
                )

    station_revision_records = []

    for key, record in transmittal_lookup.items():
        if not isinstance(key, tuple) or len(key) != 3:
            continue

        (
            record_station,
            record_revision,
            record_stage,
        ) = key

        record_station = normalize_header(
            record_station
        )

        record_revision = normalize_transmittal_revision(
            record_revision
        )

        record_stage_key = normalize_stage_text(
            record_stage
        )

        if record_revision != revision:
            continue

        if (
            sheet_stations
            and record_station not in sheet_stations
        ):
            continue

        station_revision_records.append(
            (
                record_stage_key,
                record,
            )
        )

    if not station_revision_records:
        return "", "", False, False

    stage_specific_records = [
        record
        for record_stage_key, record
        in station_revision_records
        if record_stage_key != ""
    ]

    stage_less_records = [
        record
        for record_stage_key, record
        in station_revision_records
        if record_stage_key == ""
    ]

    if stage_specific_records:
        matching_records = [
            record
            for record_stage_key, record
            in station_revision_records
            if record_stage_key == column_stage_key
        ]

    else:
        if first_revision_columns is None:
            first_revision_columns = (
                get_first_revision_columns(sheet_df)
            )

        first_column = first_revision_columns.get(
            revision
        )

        if column_name != first_column:
            return "", "", False, False

        matching_records = stage_less_records

    if not matching_records:
        return "", "", False, False

    latest = get_latest_transmittal_record(
        pd.DataFrame(matching_records)
    )

    if latest is None:
        return "", "", False, False

    is_detail_design = bool(
        latest.get(
            "Is Detail Design",
            False,
        )
    )

    # Do not change anything in the header unless the matching
    # transmittal row contains CIRCUIT BOOK or SER LAYOUT.
    #
    # Return True as the fourth value so an old approval date from
    # the existing workbook is NOT applied as a fallback.
    if not is_detail_design:
        return "", "", False, True

    # Detail Design transmittals are always displayed as approved.
    return (
        "APPROVED",
        format_approval_date(
            latest.get("Approval Date", "")
        ),
        True,
        True,
    )


def create_status_subheader_format(
    workbook,
    status,
):
    """
    Green for approved and red for rejected.
    Other statuses use the normal subheader colour.
    """

    status = normalize_approval_status(status)

    if status == "APPROVED":
        background = "#70AD47"

    elif status == "REJECTED":
        background = "#C00000"

    else:
        background = "#5B7894"

    return workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": background,
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )
def create_status_value_format(workbook, status):
    """
    Applies font colour only to modification transmittal
    drawing-cell values. No background fill is applied.
    """

    status = normalize_approval_status(status)

    if status == "APPROVED":
        font_color = "#008000"      # Green

    elif status == "REJECTED":
        font_color = "#C00000"      # Red

    else:
        font_color = "#000000"      # Normal black

    return workbook.add_format(
        {
            "font_color": font_color,
        }
    )
    
def apply_modification_transmittal_values(
    workbook,
    worksheet,
    df,
    transmittal_record_lookup,
):
    """
    Writes modification transmittal values into individual
    drawing cells.

    Example:

        A2(2026-09-01)

    The MOD column header itself is not changed.
    """

    if (
        df is None
        or df.empty
        or not transmittal_record_lookup
        or "C1252 Drawing" not in df.columns
    ):
        return

    modification_columns = [
        column
        for column in df.columns
        if normalize_header(column).endswith(" MOD")
    ]

    if not modification_columns:
        return

    for data_index, (_, row) in enumerate(
        df.iterrows()
    ):
        drawing_number = normalize_drawing_number(
            row.get(
                "C1252 Drawing",
                "",
            )
        )

        if not drawing_number:
            continue

        for column in modification_columns:
            stage = get_header_group(column)

            stage_key = normalize_stage_text(
                stage
            )

            matching_records = []

            for (
                key_drawing,
                key_revision,
                key_stage,
            ), record in transmittal_record_lookup.items():

                if key_drawing != drawing_number:
                    continue

                if not record.get(
                    "Is Modification",
                    False,
                ):
                    continue

                if normalize_stage_text(
                    key_stage
                ) != stage_key:
                    continue

                matching_records.append(record)

            if not matching_records:
                continue

            matching_records = sorted(
                matching_records,
                key=lambda record: (
                    bool(
                        record.get(
                            "Approval Date",
                            "",
                        )
                    ),
                    parse_approval_date(
                        record.get(
                            "Approval Date",
                            "",
                        )
                    ),
                    record.get(
                        "_TransmittalOrder",
                        0,
                    ),
                ),
            )

            latest = matching_records[-1]

            revision = clean_value(
                latest.get("Revision", "")
            )

            approval_date = clean_value(
                latest.get("Approval Date", "")
            )

            if not revision:
                continue

            display_value = revision

            if approval_date:
                display_value = (
                    f"{revision}"
                    f"({approval_date})"
                )

            # Always show MOD transmittal values as approved/green,
            # regardless of the transmittal Status field.
            status_format = (
                create_status_value_format(
                    workbook,
                    "APPROVED",
                )
            )

            column_index = df.columns.get_loc(
                column
            )

            # Excel row 1 and row 2 are headers.
            # Therefore dataframe row 0 starts at Excel row 3.
            excel_row = data_index + 2

            worksheet.write(
                excel_row,
                column_index,
                display_value,
                status_format,
            )
def apply_existing_approved_cell_values(
    workbook,
    worksheet,
    df,
    existing_cell_statuses,
):
    """
    Restores existing approved drawing/modification cells.

    Existing approved cells are written as:

        A2(2026-08-27)

    The approval date is read from the original Master Drawing
    List approval data.
    """

    if (
        not existing_cell_statuses
        or df is None
        or df.empty
        or "C1252 Drawing" not in df.columns
    ):
        return

    for data_index, (_, row) in enumerate(
        df.iterrows()
    ):
        drawing_key = normalize_drawing_number(
            row.get(
                "C1252 Drawing",
                "",
            )
        )

        if not drawing_key:
            continue

        for column_index, column in enumerate(
            df.columns
        ):
            existing_status = (
                existing_cell_statuses.get(
                    (
                        drawing_key,
                        column,
                    ),
                    {},
                )
            )

            if not existing_status:
                continue

            cell_value = clean_value(
                row.get(
                    column,
                    "",
                )
            )

            if not cell_value:
                continue

            approval_date = clean_value(
                existing_status.get(
                    "Approval Date",
                    "",
                )
            )

            if not approval_date:
                continue

            # Do not add the date a second time if it is already
            # contained in the existing cell value.
            existing_date_in_cell = (
                extract_approval_date_from_text(
                    cell_value
                )
            )

            if existing_date_in_cell:
                display_value = cell_value

            else:
                display_value = (
                    f"{cell_value}"
                    f"({approval_date})"
                )

            approved_format = (
                create_status_value_format(
                    workbook,
                    "APPROVED",
                )
            )

            # Data starts on Excel row 3 because rows 1 and 2
            # contain the grouped headers.
            worksheet.write(
                data_index + 2,
                column_index,
                display_value,
                approved_format,
            )

            
def format_worksheet(
    workbook,
    worksheet,
    df,
    status_df=None,
    transmittal_lookup=None,
    transmittal_record_lookup=None,
    existing_approval_data=None,
    apply_transmittal_status=False,
):
    """
    Writes a two-row grouped Excel header.

    Row 1:
        Main stage/group name

    Row 2:
        Revision/subheader

    Base columns are vertically merged across both rows.
    """
    if existing_approval_data is None:
        existing_approval_data = {
            "By Worksheet": {},
        }

    existing_header_statuses = (
        existing_approval_data.get(
            "Header Statuses",
            {},
        )
    )

    existing_cell_statuses = (
        existing_approval_data.get(
            "Cell Statuses",
            {},
        )
    )

    if not apply_transmittal_status:
        status_df = None
        transmittal_lookup = None

    header_formats = create_header_formats(
        workbook
    )

    main_header_format = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#215C84",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )

    sub_header_format = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#5B7894",
            "border": 1,
            "align": "center",
            "valign": "center",
            "text_wrap": True,
        }
    )

    alternate_row_format = workbook.add_format(
        {
            "bg_color": "#F3F6FA",
            "valign": "top",
        }
    )

    description_format = workbook.add_format(
        {
            "text_wrap": True,
            "valign": "top",
        }
    )

    base_columns = {
        "Drawing Ref",
        "C1252 Drawing",
        "Description",
    }

    first_revision_columns = (
        get_first_revision_columns(df)
    )

    column_groups = []

    for column in df.columns:
        if column in base_columns:
            group = column
        else:
            group = get_header_group(column)

        # Critical fallback: never allow a blank Excel
        # main header.
        group = clean_value(group) or clean_value(
            column
        )

        column_groups.append(group)

    group_indexes = {}

    for group in column_groups:
        if group not in group_indexes:
            group_indexes[group] = len(
                group_indexes
            )

    column_index = 0

    while column_index < len(df.columns):
        column_name = df.columns[
            column_index
        ]

        group = column_groups[column_index]
        group_end = column_index

        while (
            group_end + 1 < len(df.columns)
            and column_groups[group_end + 1]
            == group
        ):
            group_end += 1

        if group in base_columns:
            # Merge base columns vertically.
            worksheet.merge_range(
                0,
                column_index,
                1,
                column_index,
                group,
                main_header_format,
            )

        else:
            colour_index = (
                group_indexes[group]
                % len(header_formats)
            )

            group_header_format = (
                header_formats[colour_index]
            )

            if group_end > column_index:
                worksheet.merge_range(
                    0,
                    column_index,
                    0,
                    group_end,
                    group,
                    group_header_format,
                )
            else:
                worksheet.write(
                    0,
                    column_index,
                    group,
                    group_header_format,
                )

            for sub_index in range(
                column_index,
                group_end + 1,
            ):
                original_column = df.columns[
                    sub_index
                ]

                subheader = (
                    get_excel_subheader(
                        original_column
                    )
                )

                if not subheader:
                    subheader = clean_value(
                        original_column
                    )

                # Modification transmittals apply to individual drawing
                # cells, not to the MOD column header.
                if normalize_header(
                    original_column
                ).endswith(" MOD"):
                    status = ""
                    approval_date = ""
                    is_detail_design = False
                    transmittal_match_found = False

                else:
                    # First use a newly uploaded transmittal status.
                    (
                        status,
                        approval_date,
                        is_detail_design,
                        transmittal_match_found,
                    ) = get_status_for_column(
                        original_column,
                        sheet_df=status_df,
                        transmittal_lookup=transmittal_lookup,
                        first_revision_columns=(
                            first_revision_columns
                        ),
                    )

                    # Preserve an old header approval date only when there is
                    # no matching newly uploaded transmittal record.
                    if not transmittal_match_found:
                        old_header_status = (
                            existing_header_statuses.get(
                                original_column,
                                {},
                            )
                        )

                        old_approval_date = (
                            old_header_status.get(
                                "Approval Date",
                                "",
                            )
                        )

                        if old_approval_date:
                            status = "APPROVED"
                            approval_date = old_approval_date

                header_text = subheader

                # Add the date only when the matched transmittal description
                # contains CIRCUIT BOOK or SER LAYOUT.
                if approval_date and is_detail_design:
                    header_text = (
                        f"{subheader}\n"
                        f"(Detail Design: {approval_date})"
                    )

                if status in {
                    "APPROVED",
                    "REJECTED",
                }:
                    status_format = (
                        create_status_subheader_format(
                            workbook,
                            status,
                        )
                    )

                    worksheet.write(
                        1,
                        sub_index,
                        header_text,
                        status_format,
                    )
                else:
                    worksheet.write(
                        1,
                        sub_index,
                        header_text,
                        sub_header_format,
                    )

        column_index = group_end + 1

    worksheet.freeze_panes(2, 0)

    worksheet.set_row(0, 42)
    worksheet.set_row(1, 42)

    if len(df.columns) > 0:
        worksheet.autofilter(
            1,
            0,
            max(len(df) + 1, 2),
            len(df.columns) - 1,
        )

    if len(df) > 0 and len(df.columns) > 0:
        worksheet.conditional_format(
            2,
            0,
            len(df) + 1,
            len(df.columns) - 1,
            {
                "type": "formula",
                "criteria": "=MOD(ROW(),2)=1",
                "format": alternate_row_format,
            },
        )

    for index, column in enumerate(
        df.columns
    ):
        subheader = get_excel_subheader(
            column
        )

        maximum_length = max(
            len(str(column)),
            len(str(subheader)),
            12,
        )

        for value in df[column].tolist():
            if not is_blank(value):
                maximum_length = max(
                    maximum_length,
                    len(str(value)),
                )

        worksheet.set_column(
            index,
            index,
            min(
                maximum_length + 2,
                38,
            ),
        )

    if "Description" in df.columns:
        description_index = df.columns.get_loc(
            "Description"
        )

        worksheet.set_column(
            description_index,
            description_index,
            70,
            description_format,
        )

    if apply_transmittal_status:
        apply_modification_transmittal_values(
            workbook,
            worksheet,
            df,
            transmittal_record_lookup,
        )

    apply_existing_approved_cell_values(
        workbook,
        worksheet,
        df,
        existing_cell_statuses,
    )


def safe_sheet_name(
    name,
    used_names,
):
    name = clean_value(name) or "Unassigned"

    name = re.sub(
        r"[\[\]\:\*\?\/\\]",
        "_",
        name,
    )[:31]

    original_name = name
    counter = 2

    while name in used_names:
        suffix = f"_{counter}"

        name = (
            original_name[
                :31 - len(suffix)
            ]
            + suffix
        )

        counter += 1

    used_names.add(name)

    return name


def is_scheme_or_bonding_plan_description(
    description,
):
    """
    Returns True when the individual drawing description contains
    Scheme Plan or Bonding Plan.
    """

    text = normalize_header(description)

    return (
        "SCHEME PLAN" in text
        or "BONDING PLAN" in text
    )


def get_scheme_bonding_plan_drawing_keys(
    dih_df,
):
    """
    Returns drawing keys classified as Scheme Plan or Bonding Plan
    from DIH worksheet content.

    The DIH Plan Type is detected from:
        - each drawing title; or
        - document text above the DIH table, including the area
          below Contract Title.

    No filename, station, or drawing-number rule is used.
    """

    if (
        dih_df is None
        or dih_df.empty
        or "DIH Plan Type" not in dih_df.columns
        or "Normalized Drawing Number" not in dih_df.columns
    ):
        return set()

    matching_rows = dih_df[
        dih_df["DIH Plan Type"].apply(
            clean_value
        ).isin(
            {
                "Scheme Plan",
                "Bonding Plan",
            }
        )
    ]

    return {
        normalize_drawing_number(
            drawing_number
        )
        for drawing_number in matching_rows[
            "Normalized Drawing Number"
        ]
        if normalize_drawing_number(drawing_number)
    }


def is_scheme_or_bonding_plan_row(
    row,
    dih_scheme_bonding_keys=None,
):
    """
    Returns True if the row is either:

    1. Individually described as Scheme Plan or Bonding Plan; or
    2. Classified from the DIH document content as Scheme Plan or
       Bonding Plan.

    The second condition handles DIH documents such as:

        ITC-B07-GEN-GEN-DWG-SCHEME PLAN B7 STAGE

    where the individual drawing titles may not repeat the words
    SCHEME PLAN.
    """

    description = row.get(
        "Description",
        "",
    )

    if is_scheme_or_bonding_plan_description(
        description
    ):
        return True

    drawing_number = normalize_drawing_number(
        row.get(
            "C1252 Drawing",
            "",
        )
    )

    return bool(
        drawing_number
        and drawing_number in (
            dih_scheme_bonding_keys or set()
        )
    )


def get_scheme_bonding_plan_dataframe(
    final_df,
    dih_df,
):
    """
    Returns all final Master Drawing List rows that belong in the
    Scheme Plan & Bonding Plan worksheet.

    Classification comes from either the final Description or the
    originating DIH worksheet content.
    """

    if final_df is None or final_df.empty:
        return pd.DataFrame(
            columns=(
                final_df.columns
                if final_df is not None
                else []
            )
        )

    dih_scheme_bonding_keys = (
        get_scheme_bonding_plan_drawing_keys(
            dih_df
        )
    )

    result = final_df[
        final_df.apply(
            lambda row: is_scheme_or_bonding_plan_row(
                row,
                dih_scheme_bonding_keys,
            ),
            axis=1,
        )
    ].copy()

    return sort_by_drawing_ref(result)

def generate_output_excel(
    final_df,
    modification_sheet,
    dih_file_details,
    dih_df=None,
    existing_master_details="",
    transmittal_details="",
    transmittal_lookup=None,
    transmittal_record_lookup=None,
    transmittal_df=None,
    modification_df=None,
    existing_approval_data=None,
    preserve_input_order=False,
    existing_master_sheet="",
):

    if existing_approval_data is None:
        existing_approval_data = {
            "Header Statuses": {},
            "Cell Statuses": {},
        }

    output_buffer = io.BytesIO()

    if transmittal_lookup is None:
        transmittal_lookup = {}

    with pd.ExcelWriter(
        output_buffer,
        engine="xlsxwriter",
    ) as writer:
        workbook = writer.book
        used_names = set()

        # ====================================================
        # MAIN MASTER DRAWING LIST
        # ====================================================

        main_source_df = final_df.copy()

        if not preserve_input_order:
            main_source_df = sort_by_drawing_ref(
                main_source_df
            )

        main_columns = get_visible_columns_for_sheet(
            main_source_df,
            transmittal_df=transmittal_df,
            transmittal_record_lookup=(
                transmittal_record_lookup
            ),
        )

        main_df = main_source_df[
            [
                column
                for column in main_columns
                if column in main_source_df.columns
            ]
        ].copy()

        main_sheet = safe_sheet_name(
            "Master Drawing List",
            used_names,
        )

        main_df.to_excel(
            writer,
            sheet_name=main_sheet,
            index=False,
            startrow=2,
            header=False,
        )

        # Format the main worksheet here.
        main_approval_data = (
            get_existing_approval_data_for_worksheet(
                existing_approval_data,
                existing_master_sheet or main_sheet,
            )
        )

        format_worksheet(
            workbook,
            writer.sheets[main_sheet],
            main_df,
            status_df=None,
            transmittal_lookup=None,
            transmittal_record_lookup=None,
            existing_approval_data=main_approval_data,
            apply_transmittal_status=False,
        )
        
        # ====================================================
        # SCHEME PLAN & BONDING PLAN WORKSHEET
        # ====================================================

        scheme_bonding_source_df = (
            get_scheme_bonding_plan_dataframe(
                final_df,
                dih_df,
            )
        )

        if not scheme_bonding_source_df.empty:
            scheme_bonding_columns = (
                get_visible_columns_for_sheet(
                    scheme_bonding_source_df,
                    transmittal_df=transmittal_df,
                    transmittal_record_lookup=(
                        transmittal_record_lookup
                    ),
                )
            )

            scheme_bonding_df = (
                scheme_bonding_source_df[
                    [
                        column
                        for column in scheme_bonding_columns
                        if column
                        in scheme_bonding_source_df.columns
                    ]
                ]
                .copy()
            )

            scheme_bonding_sheet = safe_sheet_name(
                "Scheme Plan & Bonding Plan",
                used_names,
            )

            scheme_bonding_df.to_excel(
                writer,
                sheet_name=scheme_bonding_sheet,
                index=False,
                startrow=2,
                header=False,
            )

            scheme_bonding_approval_data = (
                get_existing_approval_data_for_worksheet(
                    existing_approval_data,
                    scheme_bonding_sheet,
                )
            )

            format_worksheet(
                workbook,
                writer.sheets[scheme_bonding_sheet],
                scheme_bonding_df,
                status_df=scheme_bonding_source_df,
                transmittal_lookup=transmittal_lookup,
                transmittal_record_lookup=(
                    transmittal_record_lookup
                ),
                existing_approval_data=(
                    scheme_bonding_approval_data
                ),
                apply_transmittal_status=True,
            )

        # ----------------------------------------------------
        # Station worksheets must exclude drawings that belong
        # in the Scheme Plan & Bonding Plan worksheet.
        #
        # This is based only on Description content.
        # No drawing number, station, filename, or DIH source
        # is hardcoded.
        # ----------------------------------------------------

        if (
            "Description" in final_df.columns
            and not final_df.empty
        ):
            dih_scheme_bonding_keys = (
                get_scheme_bonding_plan_drawing_keys(
                    dih_df
                )
            )

            station_final_df = final_df[
                ~final_df.apply(
                    lambda row: is_scheme_or_bonding_plan_row(
                        row,
                        dih_scheme_bonding_keys,
                    ),
                    axis=1,
                )
            ].copy()

        else:
            station_final_df = final_df.copy()

        # ====================================================
        # LOCATION / STATION WORKSHEETS
        # ====================================================

        if (
            "C1252 Drawing" in station_final_df.columns
            and not station_final_df.empty
        ):
            area_values = station_final_df[
                "C1252 Drawing"
            ].apply(
                extract_area_from_drawing_number
            ).apply(clean_value)

        else:
            area_values = pd.Series(
                index=station_final_df.index,
                dtype="object",
            )

        # ----------------------------------------------------
        # Group stations into output worksheets.
        #
        # Exceptional cases:
        #   AIR + AWE -> AIR
        #   SUN + YOT -> SUN & YOT
        #   TUC + YOY -> TUC
        #   TCE + TCETUC -> TCE
        # ----------------------------------------------------

        station_worksheet_values = area_values.apply(
            get_station_worksheet_name
        )

        areas = [
            area
            for area in station_worksheet_values.unique()
            if clean_value(area)
        ]

        for area in areas:
            # Include all drawings belonging to the grouped
            # output worksheet.
            location_source_df = station_final_df[
                station_worksheet_values == area
            ].copy()

            if location_source_df.empty:
                continue

            if not preserve_input_order:
                location_source_df = sort_by_drawing_ref(
                    location_source_df
                )

            location_columns = (
                get_visible_columns_for_sheet(
                    location_source_df,
                    transmittal_df=transmittal_df,
                    transmittal_record_lookup=(
                        transmittal_record_lookup
                    ),
                )
            )

            location_df = location_source_df[
                [
                    column
                    for column in location_columns
                    if column in location_source_df.columns
                ]
            ].copy()

            location_sheet = safe_sheet_name(
                area,
                used_names,
            )

            location_df.to_excel(
                writer,
                sheet_name=location_sheet,
                index=False,
                startrow=2,
                header=False,
            )

            # Format the current station worksheet.
            station_approval_data = (
                get_existing_approval_data_for_station(
                    existing_approval_data,
                    area,
                )
            )

            format_worksheet(
                workbook,
                writer.sheets[location_sheet],
                location_df,
                status_df=location_source_df,
                transmittal_lookup=transmittal_lookup,
                transmittal_record_lookup=(
                    transmittal_record_lookup
                ),
                existing_approval_data=station_approval_data,
                apply_transmittal_status=True,
            )

        # ====================================================
        # MODIFICATION DETAILS WORKSHEET
        # ====================================================

        modification_details_df = (
            create_modification_details_dataframe(
                modification_df
            )
        )

        modification_details_sheet = safe_sheet_name(
            "Modification Details",
            used_names,
        )

        modification_details_df.to_excel(
            writer,
            sheet_name=modification_details_sheet,
            index=False,
        )

        format_information_worksheet(
            workbook,
            writer.sheets[
                modification_details_sheet
            ],
            modification_details_df,
        )

        # ====================================================
        # INFORMATION WORKSHEET
        # ====================================================

        information_df = pd.DataFrame(
            {
                "Item": [
                    "Generated Date",
                    "Modification Worksheet",
                    "Modification Details Worksheet",
                    "DIH Files",
                    "Existing Master Drawing List",
                    "Transmittal Form",
                    "Final Drawing Rows",
                    "Final Output Columns",
                    "Location Sheets",
                ],
                "Value": [
                    get_current_hkt_text(),
                    modification_sheet,
                    "Modification Details",
                    "\n".join(
                        dih_file_details
                    )
                    if dih_file_details
                    else "No DIH files uploaded",
                    existing_master_details
                    if existing_master_details
                    else (
                        "No existing Master Drawing "
                        "List uploaded"
                    ),
                    transmittal_details
                    if transmittal_details
                    else (
                        "No transmittal form uploaded"
                    ),
                    len(final_df),
                    len(final_df.columns),
                    len(areas),
                ],
            }
        )

        information_sheet = safe_sheet_name(
            "Information",
            used_names,
        )

        information_df.to_excel(
            writer,
            sheet_name=information_sheet,
            index=False,
        )

        format_information_worksheet(
            workbook,
            writer.sheets[information_sheet],
            information_df,
        )

    output_buffer.seek(0)

    return output_buffer.getvalue()



def render_latest_master_download():
    """
    Displays the current shared Master Drawing List
    in the top-right area of the application.
    """

    try:
        latest = get_latest_master_file()

    except Exception as error:
        st.warning(
            "The shared latest Master Drawing List could not be loaded."
        )

        log_warning(
            "Could not load shared latest Master Drawing List",
            error_type=type(error).__name__,
        )

        return None

    if latest is None:
        st.info(
            "No shared Master Drawing List has been published yet."
        )

        return None

    left_column, right_column = st.columns(
        [3, 1],
        vertical_alignment="center",
    )

    with left_column:
        st.caption(
            "Shared latest Master Drawing List"
        )

    with right_column:
        st.download_button(
            label="Download Latest Master List",
            data=latest["bytes"],
            file_name="Master_Drawing_List_latest.xlsx",
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
            key="mdl_download_shared_latest_master",
        )

    updated_time_hkt = latest.get(
        "updated_time_hkt",
        "",
    )

    caption_parts = [
        "Latest version: "
        f"{latest['file_sha256'][:12]}",
    ]

    if latest.get("commit_sha"):
        caption_parts.append(
            f"Commit: {latest['commit_sha'][:12]}"
        )

    if updated_time_hkt:
        caption_parts.append(
            f"Updated: {updated_time_hkt}"
        )

    st.caption(
        " | ".join(caption_parts)
    )

    return latest

def initialize_pending_master_state():
    """
    Initializes temporary storage for a workbook that has been
    generated but not yet published as the shared latest file.
    """

    defaults = {
        "mdl_pending_master_bytes": None,
        "mdl_pending_master_filename": "",
        "mdl_pending_expected_github_sha": None,
        "mdl_pending_master_created_at": "",
        "mdl_pending_master_mode": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def render_pending_master_actions():
    """
    Displays download and publish controls for a workbook that
    has been generated but not yet published.
    """

    pending_bytes = st.session_state.get(
        "mdl_pending_master_bytes"
    )

    if not pending_bytes:
        return

    pending_filename = (
        st.session_state.get(
            "mdl_pending_master_filename"
        )
        or "Master_Drawing_List_generated.xlsx"
    )

    created_at = st.session_state.get(
        "mdl_pending_master_created_at",
        "",
    )

    st.markdown("---")

    st.subheader(
        "Generated Master Drawing List"
    )

    st.info(
        "This workbook has been generated but has not yet "
        "replaced the shared latest Master Drawing List."
    )

    if created_at:
        st.caption(
            f"Generated at: {created_at}"
        )

    download_column, publish_column = st.columns(
        [1, 1],
        gap="large",
    )

    with download_column:
        st.download_button(
            label="Download Generated Workbook",
            data=pending_bytes,
            file_name=pending_filename,
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
            key="mdl_download_pending_master_workbook",
        )

    with publish_column:
        publish_clicked = st.button(
            "Publish as Latest Shared Master List",
            type="primary",
            use_container_width=True,
            key="mdl_publish_pending_master_workbook",
        )

    if not publish_clicked:
        return

    pending_mode = st.session_state.get(
        "mdl_pending_master_mode",
        "",
    )

    try:
        # Read the current shared latest file immediately before
        # publishing. This prevents an old generated workbook
        # from replacing the current shared workbook.
        latest_now = get_latest_master_file()

        expected_github_sha = st.session_state.get(
            "mdl_pending_expected_github_sha"
        )

        # --------------------------------------------------------
        # Protection 1:
        # A newly created workbook cannot replace an existing
        # shared Master Drawing List.
        #
        # Create mode is allowed only when no latest file exists.
        # --------------------------------------------------------
        if (
            pending_mode
            == "Create a new Master Drawing List"
            and latest_now is not None
        ):
            st.error(
                "This workbook was created as a new Master "
                "Drawing List and cannot replace the existing "
                "shared latest file."
            )

            st.warning(
                "Please download the current latest Master "
                "Drawing List, select 'Update an existing "
                "Master Drawing List', upload the downloaded "
                "workbook, and generate the update again."
            )

            return

        # --------------------------------------------------------
        # Protection 2:
        # An update workbook must still be based on the same
        # latest GitHub file that existed when it was generated.
        # --------------------------------------------------------
        if (
            pending_mode
            == "Update an existing Master Drawing List"
            and latest_now is not None
            and expected_github_sha
            and latest_now["github_sha"]
            != expected_github_sha
        ):
            raise LatestMasterConflictError(
                "The shared Master Drawing List changed after "
                "this workbook was generated."
            )

        # --------------------------------------------------------
        # Publish the workbook.
        #
        # For update mode, pass the expected SHA so GitHub also
        # performs its own optimistic-locking check.
        #
        # For create mode, expected_github_sha is None and is
        # therefore only allowed when no latest file exists.
        # --------------------------------------------------------
        publish_sha = None

        if (
            pending_mode
            == "Update an existing Master Drawing List"
        ):
            publish_sha = expected_github_sha

        published_latest = publish_latest_master_file(
            pending_bytes,
            expected_github_sha=publish_sha,
            message=(
                "Publish latest Master Drawing List | "
                f"time={get_current_hkt_text()} | "
                f"workflow={pending_mode} | "
                f"session={get_session_id()}"
            ),
        )

        log_info(
            "Pending Master Drawing List published",
            github_path=published_latest["path"],
            commit_sha=published_latest["commit_sha"],
            file_sha256=published_latest["file_sha256"],
            workflow_mode=pending_mode,
        )

        # Clear the pending workbook only after successful
        # publication.
        st.session_state.mdl_pending_master_bytes = None
        st.session_state.mdl_pending_master_filename = ""
        st.session_state.mdl_pending_expected_github_sha = None
        st.session_state.mdl_pending_master_created_at = ""
        st.session_state.mdl_pending_master_mode = ""

        st.success(
            "The workbook was published successfully as the "
            "latest shared Master Drawing List."
        )

        st.rerun()

    except LatestMasterConflictError:
        st.error(
            "The workbook was not published because another "
            "user published a newer Master Drawing List first."
        )

        st.warning(
            "Please download the current latest workbook from "
            "the top-right of the page, select 'Update an "
            "existing Master Drawing List', upload it, and "
            "generate the update again."
        )

    except Exception as error:
        error_id = create_error_id()

        show_application_error(
            "The generated workbook could not be published.",
            error=error,
            error_id=error_id,
        )

# ============================================================
# USER INTERFACE
# ============================================================

logo_html = ""

if ICON_PATH.exists():
    encoded_logo = base64.b64encode(
        ICON_PATH.read_bytes()
    ).decode("utf-8")

    logo_html = f"""
    <img src="data:image/png;base64,{encoded_logo}"
         class="professional-logo">
    """

def render_master_drawing_list_interface():
    initialize_pending_master_state()

    # ============================================================
    # PAGE STYLING
    # ============================================================

    st.markdown(
        """
        <style>
        :root {
            --navy:#102A43;
            --blue:#1769C2;
            --blue-dark:#0F4FA8;
            --teal:#008F95;
            --green:#167A3D;
            --border:#D9E2EC;
            --text:#243B53;
            --muted:#627D98;
            --soft-blue:#EAF3FF;
            --soft-teal:#E7F8F7;
            --shadow:0 8px 25px rgba(16,42,67,.08);
        }

        .stApp {
            background:linear-gradient(
                180deg,
                #EEF4F9 0px,
                #F5F7FA 430px,
                #F5F7FA 100%
            );
            color:var(--text);
        }

        .main .block-container {
            max-width:1480px;
            padding-top:1.8rem;
            padding-bottom:4rem;
        }
        .information-card-contact {
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid var(--border);
            color: var(--muted);
            font-size: 13px;
            line-height: 1.6;
        }

        .information-card-contact a {
            color: var(--blue);
            font-weight: 750;
            text-decoration: none;
        }

        .information-card-contact a:hover {
            color: var(--blue-dark);
            text-decoration: underline;
        }

        #MainMenu,
        footer {
            visibility:hidden;
        }

        .professional-header {
            position:relative;
            overflow:hidden;
            background:linear-gradient(
                135deg,
                #102A43 0%,
                #163D5A 58%,
                #1C587D 100%
            );
            border-radius:18px;
            padding:30px 34px;
            margin-bottom:26px;
            box-shadow:0 12px 30px rgba(16,42,67,.22);
            color:white;
        }

        .professional-header-inner {
            position:relative;
            z-index:1;
            display:flex;
            align-items:center;
            gap:20px;
        }

        .professional-logo {
            width:68px;
            height:68px;
            object-fit:contain;
            background:white;
            padding:8px;
            border-radius:14px;
        }

        .professional-header h1 {
            margin:0;
            color:white;
            font-size:30px;
            font-weight:750;
        }

        .professional-header p {
            margin:8px 0 0;
            color:#D9EAF5;
            font-size:15px;
        }

        .header-tag {
            display:inline-block;
            margin-top:15px;
            padding:5px 11px;
            border:1px solid rgba(255,255,255,.25);
            border-radius:20px;
            color:#EAF5FB;
            background:rgba(255,255,255,.10);
            font-size:11px;
            font-weight:700;
            letter-spacing:.6px;
            text-transform:uppercase;
        }

        .section-heading {
            margin-top:24px;
            margin-bottom:5px;
            color:var(--navy);
            font-size:21px;
            font-weight:750;
        }

        .section-description {
            margin-bottom:17px;
            color:var(--muted);
            font-size:14px;
            line-height:1.6;
        }

        .information-card,
        .upload-panel {
            background:#FFFFFF;
            border:1px solid var(--border);
            border-radius:14px;
            padding:22px 25px;
            margin-bottom:24px;
            box-shadow:var(--shadow);
        }

        .information-card-title,
        .upload-panel-title {
            color:var(--navy);
            font-size:18px;
            font-weight:750;
        }

        .information-card-text,
        .upload-panel-description {
            color:var(--muted);
            font-size:14px;
            line-height:1.65;
        }

        .upload-panel {
            height:100%;
            padding:22px 23px 18px;
        }

        .upload-panel.required {
            border-top:4px solid var(--blue);
        }

        .upload-panel.optional {
            border-top:4px solid var(--teal);
        }

        .upload-panel-header {
            display:flex;
            justify-content:space-between;
            gap:12px;
            margin-bottom:12px;
        }

        .status-badge {
            flex-shrink:0;
            padding:5px 9px;
            border-radius:15px;
            font-size:10px;
            font-weight:800;
            text-transform:uppercase;
        }

        .status-badge.required {
            color:#124B86;
            background:var(--soft-blue);
        }

        .status-badge.optional {
            color:#086A6F;
            background:var(--soft-teal);
        }

        [data-testid="stFileUploader"] {
            background:#FBFCFE;
            border:1px dashed #9FB3C8;
            border-radius:10px;
            padding:9px;
        }

        .stButton > button,
        .stDownloadButton > button {
            min-height:50px;
            border:none;
            border-radius:10px;
            color:white;
            font-size:15px;
            font-weight:750;
        }

        .stButton > button {
            background:linear-gradient(
                135deg,
                var(--blue),
                var(--blue-dark)
            );
        }

        .stDownloadButton > button {
            background:linear-gradient(
                135deg,
                var(--green),
                #0D5A2C
            );
        }

        [data-testid="stMetric"] {
        background:#FFFFFF;
        border:1px solid var(--border);
        border-radius:12px;
        padding:16px 18px;
        }

        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] p {
            color:#627D98 !important;
            font-weight:700 !important;
        }

        [data-testid="stMetricValue"],
        [data-testid="stMetricValue"] div {
            color:#102A43 !important;
            font-weight:800 !important;
        }

        [data-testid="stMetricDelta"],
        [data-testid="stMetricDelta"] div {
            color:#243B53 !important;
        }

        .professional-footer {
            margin-top:45px;
            padding-top:18px;
            border-top:1px solid var(--border);
            color:var(--muted);
            font-size:12px;
            line-height:1.7;
            text-align:center;
        }

        @media (max-width:900px) {
            .professional-header h1 {
                font-size:24px;
            }
        }

        /* Workflow selector label */
        [data-testid="stRadio"] > label,
        [data-testid="stRadio"] > label p {
            color: #000000 !important;
            font-weight: 700 !important;
        }

        /* Radio option text */
        [data-testid="stRadio"] label,
        [data-testid="stRadio"] label p,
        [data-testid="stRadio"] label span {
            color: #000000 !important;
        }

        /* Radio option text in Streamlit's inner containers */
        [data-testid="stRadio"] div[role="radiogroup"] label,
        [data-testid="stRadio"] div[role="radiogroup"] label p,
        [data-testid="stRadio"] div[role="radiogroup"] label span {
            color: #000000 !important;
        }

        /* Radio buttons */
        [data-testid="stRadio"] input[type="radio"] {
            accent-color: #1769C2;
        }
        .workflow-card {
            min-height: 150px;
            padding: 22px;
            border: 2px solid #D9E2EC;
            border-radius: 14px;
            background: #FFFFFF;
            box-shadow: 0 5px 16px rgba(16,42,67,.08);
            margin-bottom: 10px;
        }

        .workflow-card.selected {
            border: 2px solid #1769C2;
            background: #EEF6FF;
            box-shadow: 0 6px 18px rgba(23,105,194,.18);
        }

        .workflow-card-title {
            color: #102A43 !important;
            font-size: 18px;
            font-weight: 800;
            margin-bottom: 8px;
        }

        .workflow-card-description {
            color: #243B53 !important;
            font-size: 14px;
            line-height: 1.55;
            min-height: 48px;
        }

        .workflow-card-status {
            display: inline-block;
            margin-top: 10px;
            padding: 4px 10px;
            border-radius: 14px;
            color: #FFFFFF !important;
            background: #1769C2;
            font-size: 11px;
            font-weight: 800;
            text-transform: uppercase;
        }

        .workflow-card-status.inactive {
            color: #486581 !important;
            background: #EAF0F6;
        }

        div.stButton > button.workflow-select-button {
            width: 100%;
            min-height: 42px;
            margin-top: 4px;
            border: 1px solid #1769C2;
            border-radius: 8px;
            color: #FFFFFF !important;
            background: #1769C2;
            font-weight: 750;
        }

        div.stButton > button.workflow-select-button:hover {
            color: #FFFFFF !important;
            background: #0F4FA8;
            border-color: #0F4FA8;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    latest_master = render_latest_master_download()

    # ============================================================
    # APPLICATION HEADER
    # ============================================================

    render_html(
        f"""
        <div class="professional-header">
            <div class="professional-header-inner">
                {logo_html}

                <div>
                    <h1>Alstom Master Drawing List Generator</h1>

                    <p>
                        Alstom drawing list generation and update platform
                    </p>

                    <span class="header-tag">
                        Alstom document management workflow
                    </span>
                </div>
            </div>
        </div>
        """
    )


    # ============================================================
    # APPLICATION INFORMATION
    # ============================================================

    render_html(
        """
        <div class="information-card">
            <div class="information-card-title">
                📘 Generate an Alstom Master Drawing List
            </div>

            <div class="information-card-text">
                Choose whether to create a new Alstom Master Drawing
                List or update an existing one. The available upload
                fields will change according to your selection.
            </div>

            <div class="information-card-contact">
                For assistance or inquiries, please contact
                <a href="mailto:jimin.lee@alstomgroup.com">
                    jimin.lee@alstomgroup.com
                </a>.
            </div>
        </div>
        """
    )


    # ============================================================
    # MASTER LIST MODE SELECTION
    # ============================================================

    render_html(
        """
        <div class="section-heading">
            Alstom Master Drawing List mode
        </div>

        <div class="section-description">
            Select one workflow. The existing Alstom Master Drawing
            List upload field is shown only when the update workflow
            is selected.
        </div>
        """
    )

    # ============================================================
    # CONTAINER-BASED WORKFLOW SELECTION
    # ============================================================

    CREATE_MODE = "Create a new Master Drawing List"
    UPDATE_MODE = "Update an existing Master Drawing List"

    if "mdl_master_list_mode" not in st.session_state:
        st.session_state.mdl_master_list_mode = CREATE_MODE

    mdl_master_list_mode = st.session_state.mdl_master_list_mode

    render_html(
        """
        <div style="
            color:#102A43;
            font-size:15px;
            font-weight:750;
            margin-bottom:10px;
        ">
            Select workflow
        </div>
        """
    )

    workflow_col_1, workflow_col_2 = st.columns(
        [1, 1],
        gap="large",
    )


    # ============================================================
    # CREATE NEW WORKFLOW CARD
    # ============================================================

    with workflow_col_1:
        create_selected = (
            mdl_master_list_mode == CREATE_MODE
        )

        create_card_class = (
            "workflow-card selected"
            if create_selected
            else "workflow-card"
        )

        create_status_class = (
            "workflow-card-status"
            if create_selected
            else "workflow-card-status inactive"
        )

        render_html(
            f"""
            <div class="{create_card_class}">
                <div class="workflow-card-title">
                    ➕ Create a new Master Drawing List
                </div>

                <div class="workflow-card-description">
                    Create a new register using the Modification
                    Sheet REGISTER and DIH Register files.
                    Transmittal forms can optionally apply
                    approval status and dates.
                </div>

                <span class="{create_status_class}">
                    {
                        "Selected"
                        if create_selected
                        else "Click below to select"
                    }
                </span>
            </div>
            """
        )

        if st.button(
            "Create New Master List",
            key="mdl_select_create_master_mode",
            use_container_width=True,
            type=(
                "primary"
                if create_selected
                else "secondary"
            ),
        ):
            st.session_state.mdl_master_list_mode = CREATE_MODE
            st.rerun()


    # ============================================================
    # UPDATE EXISTING WORKFLOW CARD
    # ============================================================

    with workflow_col_2:
        update_selected = (
            mdl_master_list_mode == UPDATE_MODE
        )

        update_card_class = (
            "workflow-card selected"
            if update_selected
            else "workflow-card"
        )

        update_status_class = (
            "workflow-card-status"
            if update_selected
            else "workflow-card-status inactive"
        )

        render_html(
            f"""
            <div class="{update_card_class}">
                <div class="workflow-card-title">
                    📂 Update an existing Master Drawing List
                </div>

                <div class="workflow-card-description">
                    Upload an existing Master Drawing List.
                    Modification, DIH and Transmittal files are
                    optional additions or status updates.
                </div>

                <span class="{update_status_class}">
                    {
                        "Selected"
                        if update_selected
                        else "Click below to select"
                    }
                </span>
            </div>
            """
        )

        if st.button(
            "Use Existing Master List",
            key="mdl_select_existing_master_mode",
            use_container_width=True,
            type=(
                "primary"
                if update_selected
                else "secondary"
            ),
        ):
            st.session_state.mdl_master_list_mode = UPDATE_MODE
            st.rerun()

    st.markdown(
        "<br>",
        unsafe_allow_html=True,
    )


    # ============================================================
    # DEFAULT FILE VARIABLES
    # ============================================================

    # These variables are initialised before the conditional
    # UI blocks so they are always available in the processing
    # section below.

    modification_files = []
    dih_files = []
    existing_master_file = None
    transmittal_files = []
    existing_master_bytes = None
    existing_master_name = None
    existing_approval_data = {
        "By Worksheet": {},
    }

    # ============================================================
    # CREATE NEW MASTER DRAWING LIST
    # ============================================================

    if mdl_master_list_mode == CREATE_MODE:
        render_html(
            """
            <div class="information-card">
                <div class="information-card-title">
                    Create a new Alstom Master Drawing List
                </div>

                <div class="information-card-text">
                    Upload at least one DIH Register file.
                    The Modification Sheet REGISTER is optional
                    and can be used to add modification information.
                    Transmittal forms are optional and can be used to apply
                    approval colours and approval dates.
                </div>
            </div>
            """
        )

        column_1, column_2, column_3 = st.columns(
            [1, 1, 1],
            gap="large",
        )


        # ============================================================
        # DIH REGISTER FILES — FIRST COLUMN
        # ============================================================

        with column_1:
            render_html(
                """
                <div class="upload-panel required">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            DIH Register Files
                        </div>

                        <span class="status-badge required">
                            Required
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Upload one or more DIH Register files.
                        At least one DIH file is required.
                    </div>
                </div>
                """
            )

            dih_files = st.file_uploader(
                "Select DIH Register Files",
                type=SUPPORTED_EXCEL_EXTENSIONS,
                accept_multiple_files=True,
                key="mdl_create_dih_files",
            )

            if dih_files:
                st.success(
                    f"{len(dih_files)} DIH file(s) loaded"
                )
            else:
                st.info(
                    "At least one DIH file is required."
                )


        # ============================================================
        # MODIFICATION REGISTER — SECOND COLUMN
        # ============================================================

        with column_2:
            render_html(
                """
                <div class="upload-panel optional">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            Modification Sheet REGISTER
                        </div>

                        <span class="status-badge optional">
                            Optional
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Optional additional modification data.
                    </div>
                </div>
                """
            )

            modification_files = st.file_uploader(
                "Select Modification Sheet REGISTER files",
                type=SUPPORTED_EXCEL_EXTENSIONS,
                accept_multiple_files=True,
                key="mdl_create_modification_files",
            )

            if modification_files:
                st.success(
                    f"{len(modification_files)} modification "
                    "register file(s) loaded"
                )
            else:
                st.info(
                    "Modification registers are optional."
                )


        # ============================================================
        # TRANSMITTAL FORMS — THIRD COLUMN
        # ============================================================

        with column_3:
            render_html(
                """
                <div class="upload-panel optional">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            Transmittal Forms
                        </div>

                        <span class="status-badge optional">
                            Optional
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Upload one or more transmittal forms
                        to apply approval status and dates.
                    </div>
                </div>
                """
            )

            transmittal_files = st.file_uploader(
                "Select Transmittal Forms",
                type=SUPPORTED_TRANSMITTAL_EXTENSIONS,
                accept_multiple_files=True,
                key="mdl_create_transmittal_files",
            )

            if transmittal_files:
                st.success(
                    f"{len(transmittal_files)} transmittal "
                    "form(s) loaded"
                )
            else:
                st.info(
                    "Transmittal forms are optional."
                )


    # ============================================================
    # UPDATE EXISTING MASTER DRAWING LIST
    # ============================================================

    else:
        render_html(
            """
            <div class="information-card">
                <div class="information-card-title">
                    Update an existing Master Drawing List
                </div>

                <div class="information-card-text">
                    Upload an existing Master Drawing List.
                    Modification, DIH and Transmittal files are
                    optional. They can be used to add information
                    or update approval status and dates.
                </div>
            </div>
            """
        )

        column_1, column_2, column_3, column_4 = st.columns(
            [1.2, 1, 1, 1],
            gap="large",
        )


        # --------------------------------------------------------
        # EXISTING MASTER DRAWING LIST
        # --------------------------------------------------------

        with column_1:
            render_html(
                """
                <div class="upload-panel required">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            Existing Master Drawing List
                        </div>

                        <span class="status-badge required">
                            Required
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Select the Master Drawing List that you
                        want to update.
                    </div>
                </div>
                """
            )

            existing_master_file = st.file_uploader(
                "Select Existing Master Drawing List",
                type=SUPPORTED_EXCEL_EXTENSIONS,
                accept_multiple_files=False,
                key="mdl_existing_master_file",
            )

            if existing_master_file is not None:
                st.success(
                    "Existing master loaded: "
                    f"{existing_master_file.name}"
                )

                st.info(
                    "For update mode, download the latest workbook from "
                    "the top-right of the page and upload it without opening "
                    "and resaving it in Excel. Resaving may change the internal "
                    "file bytes and cause the version check to fail."
                )
            else:
                st.info(
                    "An existing Master Drawing List is required."
                )

        # --------------------------------------------------------
        # OPTIONAL DIH REGISTER FILES
        # --------------------------------------------------------

        with column_2:
            render_html(
                """
                <div class="upload-panel optional">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            DIH Register Files
                        </div>

                        <span class="status-badge optional">
                            Optional
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Optional additional DIH data.
                    </div>
                </div>
                """
            )

            dih_files = st.file_uploader(
                "Select DIH Register Files",
                type=SUPPORTED_EXCEL_EXTENSIONS,
                accept_multiple_files=True,
                key="mdl_update_dih_files",
            )

            if dih_files:
                st.success(
                    f"{len(dih_files)} DIH file(s) loaded"
                )
            else:
                st.info(
                    "DIH files are optional."
                )


        # --------------------------------------------------------
        # OPTIONAL MODIFICATION REGISTER
        # --------------------------------------------------------

        with column_3:
            render_html(
                """
                <div class="upload-panel optional">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            Modification REGISTER
                        </div>

                        <span class="status-badge optional">
                            Optional
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Optional additional modification data.
                    </div>
                </div>
                """
            )

            modification_files = st.file_uploader(
                "Select Modification REGISTER files",
                type=SUPPORTED_EXCEL_EXTENSIONS,
                accept_multiple_files=True,
                key="mdl_update_modification_files",
            )

            if modification_files:
                st.success(
                    f"{len(modification_files)} modification "
                    "register file(s) loaded"
                )
            else:
                st.info(
                    "Modification registers are optional."
                )


        # --------------------------------------------------------
        # OPTIONAL TRANSMITTAL FORMS
        # --------------------------------------------------------

        with column_4:
            render_html(
                """
                <div class="upload-panel optional">
                    <div class="upload-panel-header">
                        <div class="upload-panel-title">
                            Transmittal Forms
                        </div>

                        <span class="status-badge optional">
                            Optional
                        </span>
                    </div>

                    <div class="upload-panel-description">
                        Optional transmittal forms for updating
                        approval status and approval dates.
                    </div>
                </div>
                """
            )

            transmittal_files = st.file_uploader(
                "Select Transmittal Forms",
                type=SUPPORTED_TRANSMITTAL_EXTENSIONS,
                accept_multiple_files=True,
                key="mdl_update_transmittal_files",
            )

            if transmittal_files:
                st.success(
                    f"{len(transmittal_files)} transmittal "
                    "form(s) loaded"
                )
            else:
                st.info(
                    "Transmittal forms are optional."
                )


    # ============================================================
    # GENERATE BUTTON
    # ============================================================

    st.markdown(
        "<br>",
        unsafe_allow_html=True,
    )

    generate_button = st.button(
        "Generate Master Excel Workbook",
        type="primary",
        use_container_width=True,
        key="mdl_generate_master_excel_workbook",
    )


    # ============================================================
    # PROCESSING
    # ============================================================

    if generate_button:
        log_info(
            "Master workbook generation started",
            mode=mdl_master_list_mode,
            dih_file_count=len(dih_files or []),
            transmittal_file_count=len(
                transmittal_files or []
            ),
            modification_file_count=len(
                modification_files or []
            ),
            existing_master_uploaded=(
                existing_master_file is not None
            ),
        )

        # --------------------------------------------------------
        # Validate workflow
        # --------------------------------------------------------

        if mdl_master_list_mode == CREATE_MODE:
            if not dih_files:
                log_warning(
                    "Generation stopped because no DIH file was uploaded",
                    mode=mdl_master_list_mode,
                )

                st.error(
                    "Please upload at least one DIH Register file."
                )
                st.stop()

        elif mdl_master_list_mode == UPDATE_MODE:
            if existing_master_file is None:
                log_warning(
                    "Generation stopped because no existing master "
                    "file was uploaded",
                    mode=mdl_master_list_mode,
                )

                st.error(
                    "Please upload an existing Alstom Master "
                    "Drawing List."
                )
                st.stop()

        else:
            raise ValueError(
                f"Unknown Master Drawing List mode: {mdl_master_list_mode}"
            )
        
        try:
            # --------------------------------------------------------
            # Capture the shared latest version before processing
            # --------------------------------------------------------

            latest_before_generation = (
                get_latest_master_file()
            )

            # --------------------------------------------------------
            # Prepare and validate the existing master input
            # --------------------------------------------------------

            if mdl_master_list_mode == UPDATE_MODE:
                existing_master_bytes = (
                    existing_master_file.getvalue()
                )

                existing_master_name = (
                    existing_master_file.name
                )

                validate_existing_file_is_latest(
                    existing_master_file,
                    latest_before_generation,
                )

        except LatestMasterConflictError:
            st.error(
                "This workbook was not generated because the uploaded "
                "Master Drawing List is not the current shared version."
            )

            st.warning(
                "Please download the latest Master Drawing List from "
                "the top-right of the page, upload that exact file, "
                "and try again."
            )

            log_warning(
                "Generation stopped because the uploaded master "
                "drawing list was stale"
            )

            st.stop()

        except Exception as error:
            error_id = create_error_id()

            show_application_error(
                "The shared latest Master Drawing List could not be loaded.",
                error=error,
                error_id=error_id,
            )

            st.stop()

        existing_master_df = None
        existing_master_details = ""
        existing_master_sheet = ""
        existing_approval_data = {
            "Header Statuses": {},
            "Cell Statuses": {},
        }

        transmittal_details = ""
        transmittal_df = pd.DataFrame()
        transmittal_lookup = {}
        transmittal_record_lookup = {}

        modification_df = pd.DataFrame(
            columns=[
                "Modification Drawing Number",
                "Normalized Drawing Number",
                "Description",
                "_Modification Phase",
                "_Modification Revision",
                "_ModificationSheetNo",
            ]
        )

        modification_sheet = "Not uploaded"
        modification_header = None

        dih_df = pd.DataFrame(
            columns=[
                "DIH Drawing Number",
                "Normalized Drawing Number",
                "DIH Description",
                "DIH Category",
                "DIH Revision",
                "DIH Plan Type",
            ]
        )

        dih_file_details = []
        dih_file_preview = []

        try:
            # ====================================================
            # VALIDATE ALL UPLOADED FILES
            # ====================================================

            for uploaded_modification_file in (
                modification_files or []
            ):
                validate_uploaded_file(
                    uploaded_modification_file,
                    SUPPORTED_EXCEL_EXTENSIONS,
                    MAX_EXCEL_FILE_SIZE_MB,
                )

            if existing_master_file is not None:
                validate_uploaded_file(
                    existing_master_file,
                    SUPPORTED_EXCEL_EXTENSIONS,
                    MAX_EXCEL_FILE_SIZE_MB,
                )

            for uploaded_dih_file in dih_files or []:
                validate_uploaded_file(
                    uploaded_dih_file,
                    SUPPORTED_EXCEL_EXTENSIONS,
                    MAX_EXCEL_FILE_SIZE_MB,
                )

            for uploaded_transmittal_file in (
                transmittal_files or []
            ):
                validate_uploaded_file(
                    uploaded_transmittal_file,
                    SUPPORTED_TRANSMITTAL_EXTENSIONS,
                    MAX_TRANSMITTAL_FILE_SIZE_MB,
                )

            modification_dataframes = []
            modification_sheet_details = []
            modification_header_details = []

            if modification_files:
                with st.spinner(
                    f"Reading {len(modification_files)} "
                    "Modification REGISTER file(s)..."
                ):
                    for uploaded_modification_file in modification_files:
                        try:
                            (
                                single_modification_df,
                                single_modification_sheet,
                                single_modification_header,
                            ) = read_modification_register(
                                uploaded_modification_file.getvalue(),
                                uploaded_modification_file.name,
                            )

                        except Exception as error:
                            raise ValueError(
                                "Could not read modification file "
                                f"'{uploaded_modification_file.name}'.\n\n"
                                f"{error}"
                            ) from error

                        single_modification_df[
                            "_ModificationSourceFile"
                        ] = uploaded_modification_file.name

                        modification_dataframes.append(
                            single_modification_df
                        )

                        modification_sheet_details.append(
                            f"{uploaded_modification_file.name} | "
                            f"Sheet: {single_modification_sheet} | "
                            f"Header row: "
                            f"{single_modification_header + 1} | "
                            f"Rows: {len(single_modification_df)}"
                        )

                        modification_header_details.append(
                            f"{uploaded_modification_file.name}: "
                            f"row {single_modification_header + 1}"
                        )

                        log_info(
                            "Modification register loaded",
                            file_name=(
                                uploaded_modification_file.name
                            ),
                            worksheet=single_modification_sheet,
                            rows=len(single_modification_df),
                        )

                if modification_dataframes:
                    modification_df = pd.concat(
                        modification_dataframes,
                        ignore_index=True,
                        sort=False,
                    ).fillna("")

                    modification_sheet = "\n".join(
                        modification_sheet_details
                    )

                    modification_header = "\n".join(
                        modification_header_details
                    )

            dih_dataframes = []

            if dih_files:
                with st.spinner(
                    f"Reading {len(dih_files)} DIH file(s)..."
                ):
                    for uploaded_dih_file in dih_files:
                        try:
                            (
                                single_dih_df,
                                single_dih_sheet,
                                single_dih_header,
                                single_dih_category,
                            ) = read_dih_register(
                                uploaded_dih_file.getvalue(),
                                uploaded_dih_file.name,
                            )

                        except Exception as error:
                            raise ValueError(
                                f"Could not read DIH file "
                                f"'{uploaded_dih_file.name}'.\n\n"
                                f"{error}"
                            ) from error

                        dih_dataframes.append(
                            single_dih_df
                        )

                        log_info(
                            "DIH register loaded",
                            file_name=uploaded_dih_file.name,
                            worksheet=single_dih_sheet,
                            rows=len(single_dih_df),
                            category=(
                                single_dih_category
                                or "Standard DIH"
                            ),
                        )

                        dih_file_details.append(
                            (
                                f"{uploaded_dih_file.name} | "
                                f"Sheet: {single_dih_sheet} | "
                                f"Header row: "
                                f"{single_dih_header + 1} | "
                                f"Category: "
                                f"{single_dih_category or 'Standard DIH'} | "
                                f"Rows: {len(single_dih_df)}"
                            )
                        )

                        dih_file_preview.append(
                            {
                                "File": uploaded_dih_file.name,
                                "Worksheet": single_dih_sheet,
                                "Header row": single_dih_header + 1,
                                "Detected category": (
                                    single_dih_category
                                    or "Standard DIH"
                                ),
                                "Scheme / Bonding rows": int(
                                    single_dih_df["DIH Plan Type"]
                                    .isin(
                                        [
                                            "Scheme Plan",
                                            "Bonding Plan",
                                        ]
                                    )
                                    .sum()
                                ),
                                "Rows": len(single_dih_df),
                            }
                        )

                if dih_dataframes:
                    dih_df = pd.concat(
                        dih_dataframes,
                        ignore_index=True,
                    )

            if existing_master_bytes is not None:
                with st.spinner(
                    "Reading the existing Master Drawing List..."
                ):
                    (
                        existing_master_df,
                        existing_master_sheet,
                    ) = read_existing_master_list(
                        existing_master_bytes,
                        existing_master_name,
                    )

                    approval_data_by_worksheet = (
                        extract_existing_approval_data_by_worksheet(
                            existing_master_bytes,
                            existing_master_name,
                        )
                    )

                    existing_approval_data = {
                        "By Worksheet": approval_data_by_worksheet,
                    }

                    existing_master_details = (
                        f"{existing_master_name} | "
                        f"Sheet: {existing_master_sheet} | "
                        f"Rows: {len(existing_master_df)}"
                    )

                    approval_by_worksheet = (
                        existing_approval_data.get(
                            "By Worksheet",
                            {},
                        )
                    )

                    existing_header_approval_count = sum(
                        len(
                            data.get(
                                "Header Statuses",
                                {},
                            )
                        )
                        for data in approval_by_worksheet.values()
                    )

                    existing_cell_approval_count = sum(
                        len(
                            data.get(
                                "Cell Statuses",
                                {},
                            )
                        )
                        for data in approval_by_worksheet.values()
                    )

                    
                    log_info(
                        "Existing master drawing list loaded",
                        file_name=existing_master_name,
                        worksheet=existing_master_sheet,
                        rows=len(existing_master_df),
                        existing_header_approvals=(
                            existing_header_approval_count
                        ),
                        existing_cell_approvals=(
                            existing_cell_approval_count
                        ),
                    )
                    

            if transmittal_files:
                transmittal_dataframes = []
                transmittal_detail_lines = []

                with st.spinner(
                    f"Reading {len(transmittal_files)} transmittal form(s)..."
                ):
                    for uploaded_transmittal_file in transmittal_files:
                        try:
                            single_transmittal_df = (
                                read_transmittal_register(
                                    uploaded_transmittal_file.getvalue(),
                                    uploaded_transmittal_file.name,
                                )
                            )
                        except Exception as error:
                            raise ValueError(
                                f"Could not read transmittal file "
                                f"'{uploaded_transmittal_file.name}'.\n\n"
                                f"{error}"
                            ) from error

                        transmittal_dataframes.append(
                            single_transmittal_df
                        )
                        log_info(
                            "Transmittal form loaded",
                            file_name=(
                                uploaded_transmittal_file.name
                            ),
                            rows=len(single_transmittal_df),
                        )

                        transmittal_detail_lines.append(
                            f"{uploaded_transmittal_file.name} | "
                            f"Rows extracted: "
                            f"{len(single_transmittal_df)}"
                        )

                transmittal_df = combine_transmittal_registers(
                    transmittal_dataframes
                )

                transmittal_lookup = build_transmittal_scope_lookup(
                    transmittal_df
                )
                transmittal_record_lookup = (
                    build_transmittal_record_lookup(
                        transmittal_df
                    )
                )
                transmittal_details = "\n".join(
                    transmittal_detail_lines
                )

            dynamic_headers = get_dynamic_dih_headers(
                dih_df
            )

            with st.expander(
                "Detected input information",
                expanded=False,
            ):
                col_a, col_b = st.columns(2)

                with col_a:
                    st.write(
                        {
                            "Modification worksheet":
                                modification_sheet,
                            "Modification header rows": (
                                modification_header
                                if modification_header
                                else "Not uploaded"
                            ),
                            "Modification files": len(
                                modification_files or []
                            ),
                            "Modification rows": (
                                len(modification_df)
                                if modification_df is not None
                                else 0
                            ),
                            "Existing master worksheet": (
                                existing_master_sheet
                                if existing_master_file
                                is not None
                                else "Not uploaded"
                            ),
                            "Transmittal rows":
                                len(transmittal_df),
                        }
                    )

                with col_b:
                    st.write(
                        {
                            "DIH files uploaded":
                                len(dih_files or []),
                            "Combined DIH rows":
                                len(dih_df),
                            "Generated DIH headers":
                                len(dynamic_headers),
                            "Existing master rows": (
                                len(existing_master_df)
                                if existing_master_df
                                is not None
                                else 0
                            ),
                        }
                    )

                if dih_file_preview:
                    st.dataframe(
                        pd.DataFrame(
                            dih_file_preview
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

                if not transmittal_df.empty:
                    st.markdown(
                        "#### Extracted transmittal data"
                    )

                    st.dataframe(
                        transmittal_df,
                        use_container_width=True,
                        hide_index=True,
                    )

                if existing_master_df is not None:
                    st.markdown(
                        "#### Existing master list preview"
                    )

                    st.dataframe(
                        existing_master_df.head(10),
                        use_container_width=True,
                        hide_index=True,
                    )

                st.markdown(
                    "#### Generated DIH headers"
                )

                st.code(
                    "\n".join(dynamic_headers)
                    if dynamic_headers
                    else "No dynamic headers found."
                )

                has_modification_data = (
                        modification_df is not None
                        and not modification_df.empty
                    )

            has_dih_data = (
                dih_df is not None
                and not dih_df.empty
            )

            if has_modification_data or has_dih_data:
                with st.spinner(
                    "Combining DIH and Modification data..."
                ):
                    generated_df = create_final_master(
                        modification_df,
                        dih_df,
                    )
            else:
                generated_df = pd.DataFrame(
                    columns=[
                        "Drawing Ref",
                        "C1252 Drawing",
                        "Description",
                    ]
                )

            if existing_master_df is not None:
                with st.spinner(
                    "Adding new information to the existing "
                    "Master Drawing List..."
                ):
                    final_df = (
                        merge_with_existing_master(
                            existing_master_df,
                            generated_df,
                        )
                    )
            else:
                final_df = generated_df

            final_df = add_transmittal_mod_headers(
                final_df,
                transmittal_df,
            )
            unmatched_transmittals = (
                get_unmatched_transmittal_records(
                    transmittal_df,
                    final_df,
                )
            )

            if not transmittal_df.empty:
                if not unmatched_transmittals.empty:
                    st.error(
                        f"{len(unmatched_transmittals)} transmittal drawing(s) "
                        "are missing from the Master Drawing List."
                    )

                    st.markdown(
                        """
                        The following drawings exist in the uploaded
                        transmittal form but were not found in the existing
                        or generated Master Drawing List. They were not added
                        automatically.
                        """
                    )

                    display_columns = [
                        column
                        for column in [
                            "Normalized Drawing Number",
                            "Transmittal Revision",
                            "Transmittal Stage",
                            "Status",
                            "Approval Date",
                            "Missing From Master",
                            "Source Row",
                        ]
                        if column in unmatched_transmittals.columns
                    ]

                    st.dataframe(
                        unmatched_transmittals[display_columns],
                        use_container_width=True,
                        hide_index=True,
                    )
                    
                    # missing_csv = unmatched_transmittals[
                    #     display_columns
                    # ].to_csv(index=False).encode("utf-8")

                    
                    # st.download_button(
                    #     label="Download Missing Transmittal Drawings",
                    #     data=missing_csv,
                    #     file_name="Missing_Transmittal_Drawings.csv",
                    #     mime="text/csv",
                    #     key="download_missing_transmittals",
                    # )
                    

                else:
                    st.success(
                        "All transmittal drawings were found in the "
                        "Master Drawing List."
                    )
            if (
                transmittal_files
                and not transmittal_df.empty
            ):
                with st.spinner(
                    "Applying latest transmittal approval statuses..."
                ):
                    final_df = add_transmittal_status_data(
                        final_df,
                        transmittal_df,
                    )

            with st.spinner(
                "Generating the Master Excel file..."
            ):
                output_bytes = generate_output_excel(
                    final_df=final_df,
                    modification_sheet=modification_sheet,
                    dih_file_details=dih_file_details,
                    dih_df=dih_df,
                    existing_master_details=existing_master_details,
                    transmittal_details=transmittal_details,
                    transmittal_lookup=transmittal_lookup,
                    transmittal_record_lookup=(
                        transmittal_record_lookup
                    ),
                    transmittal_df=transmittal_df,
                    modification_df=modification_df,
                    existing_approval_data=existing_approval_data,
                    preserve_input_order=(
                        existing_master_df is not None
                    ),
                    existing_master_sheet=existing_master_sheet,
                )
            output_file_name = (
                "Master_Drawing_List_"
                f"{get_current_hkt_filename_text()}"
                "_HKT.xlsx"
            )

            st.session_state.mdl_pending_master_bytes = (
                output_bytes
            )

            st.session_state.mdl_pending_master_mode = (
                mdl_master_list_mode
            )

            st.session_state.mdl_pending_master_filename = (
                output_file_name
            )

            st.session_state.mdl_pending_expected_github_sha = (
                latest_before_generation["github_sha"]
                if latest_before_generation is not None
                else None
            )

            st.session_state.mdl_pending_master_created_at = (
                get_current_hkt_text()
            )

            st.info(
                "Review or download the generated workbook. "
                "Click 'Publish as Latest Shared Master List' only "
                "when you want it to become the shared latest version."
            )

            log_info(
                "Master workbook generated successfully",
                final_rows=len(final_df),
                final_columns=len(final_df.columns),
                output_size_mb=round(
                    len(output_bytes) / 1024 / 1024,
                    2,
                ),
            )

            st.success(
                "The Master Excel file was generated successfully, "
                "but it has not been published yet."
            )
            try:
                upload_log_to_github()

            except Exception as github_error:
                LOGGER.error(
                    "Automatic GitHub log upload failed",
                    exc_info=True,
                    extra={
                        "session_id": get_session_id(),
                    },
                )

            new_drawing_count = len(
                generated_df
            )

            if existing_master_df is not None:
                existing_keys = set(
                    existing_master_df[
                        "C1252 Drawing"
                    ].apply(
                        normalize_drawing_number
                    )
                )

                generated_keys = set(
                    generated_df[
                        "C1252 Drawing"
                    ].apply(
                        normalize_drawing_number
                    )
                )

                new_unique_count = len(
                    generated_keys - existing_keys
                )

            else:
                new_unique_count = (
                    new_drawing_count
                )

            # ============================================================
            # DYNAMIC OUTPUT METRICS
            # ============================================================

            metric_items = []

            # ------------------------------------------------------------
            # Modification information
            # ------------------------------------------------------------
            if (
                modification_files
                or (
                    modification_df is not None
                    and not modification_df.empty
                )
            ):
                metric_items.extend(
                    [
                        (
                            "Modification files",
                            len(modification_files or []),
                        ),
                        (
                            "Modification rows",
                            len(modification_df)
                            if modification_df is not None
                            else 0,
                        ),
                    ]
                )

            # ------------------------------------------------------------
            # DIH information
            # ------------------------------------------------------------
            if (
                dih_files
                or (
                    dih_df is not None
                    and not dih_df.empty
                )
            ):
                metric_items.append(
                    (
                        "DIH files",
                        len(dih_files or []),
                    )
                )

                metric_items.append(
                    (
                        "DIH rows",
                        len(dih_df)
                        if dih_df is not None
                        else 0,
                    )
                )

            # ------------------------------------------------------------
            # Transmittal information
            # ------------------------------------------------------------
            if (
                transmittal_files
                or (
                    transmittal_df is not None
                    and not transmittal_df.empty
                )
            ):
                approved_count = 0
                rejected_count = 0

                if (
                    transmittal_df is not None
                    and not transmittal_df.empty
                    and "Status" in transmittal_df.columns
                ):
                    statuses = transmittal_df[
                        "Status"
                    ].apply(
                        normalize_approval_status
                    )

                    approved_count = (
                        statuses == "APPROVED"
                    ).sum()

                    rejected_count = (
                        statuses == "REJECTED"
                    ).sum()

                metric_items.extend(
                    [
                        (
                            "Transmittal files",
                            len(transmittal_files or []),
                        ),
                        (
                            "Transmittal rows",
                            len(transmittal_df)
                            if transmittal_df is not None
                            else 0,
                        ),
                        (
                            "Approved / Rejected",
                            f"{approved_count} / {rejected_count}",
                        ),
                    ]
                )

            # ------------------------------------------------------------
            # Existing Master Drawing List information
            # ------------------------------------------------------------
            if existing_master_df is not None:
                metric_items.append(
                    (
                        "Existing master rows",
                        len(existing_master_df),
                    )
                )

            # ------------------------------------------------------------
            # Always show final output information
            # ------------------------------------------------------------
            metric_items.extend(
                [
                    (
                        "Final output rows",
                        len(final_df),
                    ),
                    (
                        "Input mode",
                        (
                            "Existing master"
                            if existing_master_df is not None
                            and (
                                modification_df is None
                                or modification_df.empty
                            )
                            else "Register consolidation"
                        ),
                    ),
                ]
            )

            # Display metrics dynamically.
            # Streamlit works best with a maximum of five columns
            # per row, so split them into rows of five.
            for start_index in range(
                0,
                len(metric_items),
                5,
            ):
                current_metrics = metric_items[
                    start_index:start_index + 5
                ]

                metric_columns = st.columns(
                    len(current_metrics)
                )

                for metric_column, (
                    label,
                    value,
                ) in zip(
                    metric_columns,
                    current_metrics,
                ):
                    with metric_column:
                        st.metric(
                            label,
                            value,
                        )

            st.markdown(
                "### Output preview"
            )

            preview_columns = (
                get_visible_columns_for_sheet(
                    final_df,
                    transmittal_df=transmittal_df,
                    transmittal_record_lookup=(
                        transmittal_record_lookup
                    ),
                )
            )

            preview_df = final_df[
                [
                    column
                    for column in preview_columns
                    if column in final_df.columns
                ]
            ].copy()

            st.dataframe(
                preview_df.head(30),
                use_container_width=True,
                hide_index=True,
            )

        except Exception as error:
            error_id = create_error_id()

            show_application_error(
                "The Master Excel file could not be generated.",
                error=error,
                error_id=error_id,
            )

            log_info(
                "Master workbook generation failed",
                error_id=error_id,
                mode=mdl_master_list_mode,
            )

            try:
                upload_log_to_github()

            except Exception as github_error:
                LOGGER.error(
                    "Could not upload error log to GitHub",
                    exc_info=True,
                    extra={
                        "session_id": get_session_id(),
                    },
                )

    render_pending_master_actions()


    # ============================================================
    # FOOTER
    # ============================================================

    render_html(
        """
        <div class="professional-footer">
            <strong>
                Alstom Master Drawing List Generator
            </strong>
            <br>
            Alstom document management tool
            &nbsp;|&nbsp;
            Please verify the generated workbook before issue.
        </div>
        """
    )

if __name__ == "__main__":
    try:
        log_info(
            "Application session started",
            mode=st.session_state.get(
                "mdl_master_list_mode",
                "Not selected",
            ),
        )

        main()

        log_info(
            "Application script completed"
        )

    except Exception as error:
        error_id = create_error_id()

        show_application_error(
            "The application encountered an unexpected error.",
            error=error,
            error_id=error_id,
        )

        log_info(
            "Uncaught application failure displayed",
            error_id=error_id,
        )

# ============================================================
# ALSTOM ENGINEERING TOOLS INTEGRATION ENTRY POINT
# ============================================================

def render_master_drawing_list_app():
    """
    Public renderer used by app.py.

    This function displays the Master Drawing List Generator
    inside the combined Alstom Engineering Tools application.
    """

    try:
        log_info(
            "Master Drawing List tool opened",
            mode=st.session_state.get(
                "mdl_master_list_mode",
                "Not selected",
            ),
        )

        render_master_drawing_list_interface()

        log_info(
            "Master Drawing List tool render completed"
        )

    except Exception as error:
        error_id = create_error_id()

        show_application_error(
            "The Master Drawing List tool encountered "
            "an unexpected error.",
            error=error,
            error_id=error_id,
        )

        log_info(
            "Master Drawing List tool failure displayed",
            error_id=error_id,
        )
