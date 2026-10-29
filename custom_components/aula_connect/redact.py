"""Error texts that can be logged and shown, with Aula's and MitID's secrets taken out."""

from __future__ import annotations

import re

# Aula takes its access token in the URL, and the HTTP client puts the URL in its errors.
_SECRET = re.compile(
    r"(?i)\b(access_token|refresh_token|id_token|token|code|password)(=|[\"']?\s*:\s*[\"']?)[^&\s\"',)]+"
)


def error_text(err: BaseException) -> str:
    """The error's text, with every token, code and password in it replaced by REDACTED."""
    return _SECRET.sub(r"\1\2REDACTED", str(err))
