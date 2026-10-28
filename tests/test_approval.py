"""Tests of how the user approves with MitID, and what the login page shows for it."""

from __future__ import annotations

import pytest

from custom_components.aula_connect.const import approval_of, auth_method
from custom_components.aula_connect.views import login_stage


@pytest.mark.parametrize(
    ("approval", "method"),
    [("app_here", "APP"), ("app_elsewhere", "APP"), ("code_display", "TOKEN")],
)
def test_each_approval_logs_in_with_its_mitid_method(approval: str, method: str) -> None:
    assert auth_method(approval) == method


def test_an_entry_from_before_the_choice_approves_as_it_did() -> None:
    assert approval_of({"auth_method": "APP"}) == "app_elsewhere"
    assert approval_of({"auth_method": "TOKEN"}) == "code_display"
    assert approval_of({"auth_method": "APP", "mitid_approval": "app_here"}) == "app_here"


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
