"""Tests of the texts the login shows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TRANSLATIONS = Path(__file__).parents[1] / "custom_components" / "aula_connect" / "translations"


@pytest.mark.parametrize("language", ["en", "da"])
@pytest.mark.parametrize("step", ["user", "reconfigure", "token_credentials"])
def test_every_field_of_the_login_is_explained(language: str, step: str) -> None:
    texts = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))["config"]["step"][step]
    assert set(texts["data_description"]) == set(texts["data"])


@pytest.mark.parametrize("language", ["en", "da"])
def test_the_login_names_no_hardware_token(language: str) -> None:
    """MitID calls the device a code display; "hardware token" names nothing a user knows."""
    text = (TRANSLATIONS / f"{language}.json").read_text("utf-8").lower()
    assert "hardware" not in text


@pytest.mark.parametrize("language", ["en", "da"])
def test_every_way_to_approve_with_mitid_is_named(language: str) -> None:
    texts = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))
    assert set(texts["selector"]["mitid_approval"]["options"]) == {"app_here", "app_elsewhere", "code_display"}
