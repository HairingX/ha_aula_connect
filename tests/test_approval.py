"""Tests of what the login page shows at each step of the MitID login."""

from __future__ import annotations

import pytest

from custom_components.aula_connect.views import login_stage


@pytest.mark.parametrize(
    ("message", "qr_shown", "stage", "code"),
    [
        ("Open your MitID app now...", False, "open_app", None),
        ("Login request has been made, open your MitID app now", False, "open_app", None),
        ("Login request has been made, open your MitID app now", True, "scan", None),
        ("Please use the following OTP code in the app: 1234", False, "code", "1234"),
        ("The OTP/QR code has been verified, now waiting user to approve login", False, "approve", None),
        ("App login was accepted, finalizing authentication", False, "finishing", None),
        ("Password accepted, finalizing authentication", False, "finishing", None),
    ],
)
def test_the_page_shows_the_step_the_mitid_login_is_at(
    message: str, qr_shown: bool, stage: str, code: str | None
) -> None:
    """The messages are the MitID client's own, word for word."""
    assert login_stage(message, qr_shown) == (stage, code)
