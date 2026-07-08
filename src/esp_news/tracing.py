"""LangSmith tracing setup.

Tracing is controlled entirely by environment variables (loaded from ``.env``)::

    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=lsv2_...
    LANGSMITH_PROJECT=esp-news-reporter

When these aren't set, the ``@traceable`` decorators used across the pipeline
become no-ops, so the code runs identically with or without tracing.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def init_tracing() -> bool:
    """Load ``.env`` and report whether LangSmith tracing is active.

    Returns ``True`` only if tracing is enabled *and* an API key is present to
    upload with.
    """
    load_dotenv()
    enabled = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    has_key = bool(os.getenv("LANGSMITH_API_KEY"))

    if enabled and has_key:
        project = os.getenv("LANGSMITH_PROJECT", "default")
        logger.info("LangSmith tracing ENABLED (project=%s)", project)
        return True
    if enabled and not has_key:
        logger.warning(
            "LANGSMITH_TRACING=true but LANGSMITH_API_KEY is not set — "
            "traces will not be uploaded."
        )
    else:
        logger.info(
            "LangSmith tracing disabled "
            "(set LANGSMITH_TRACING=true + LANGSMITH_API_KEY in .env to enable)."
        )
    return False
