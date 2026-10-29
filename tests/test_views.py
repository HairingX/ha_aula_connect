"""Tests of the MitID login page and what it asks while the login runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
from typing import Any
from unittest.mock import MagicMock

from aula_connect.login.client import AulaLoginClient
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.aula_connect.auth_session import AuthSession, auth_sessions
from custom_components.aula_connect.views import (
    AulaAuthSelectIdentityView,
    AulaAuthStatusView,
    AulaAuthView,
)

ClientFactory = Callable[[], Awaitable[Any]]


@pytest.fixture
async def page(hass: HomeAssistant, hass_client_no_auth: ClientFactory) -> Any:
    """A client of Home Assistant's HTTP server, with the login views registered."""
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(AulaAuthView(hass))
    hass.http.register_view(AulaAuthStatusView(hass))
    hass.http.register_view(AulaAuthSelectIdentityView(hass))
    return await hass_client_no_auth()


def _login(hass: HomeAssistant, qr_codes: tuple[str, str] | None = None) -> AuthSession:
    client = MagicMock(spec=AulaLoginClient)
    client.get_qr_codes_svg.return_value = qr_codes
    session = AuthSession(client=client)
    auth_sessions(hass)["flow"] = session
    return session


async def test_the_login_page_shows_the_qr_code_and_no_app_button(hass: HomeAssistant, page: Any) -> None:
    _login(hass)
    response = await page.get("/api/aula_connect/auth/flow")
    assert response.status == 200
    text = await response.text()
    assert 'id="qr-container"' in text
    assert "appswitch.mitid.dk" not in text


async def test_the_login_page_says_it_is_utf_8(hass: HomeAssistant, page: Any) -> None:
    """Without it a browser shows the Danish letters wrongly."""
    _login(hass)
    response = await page.get("/api/aula_connect/auth/flow")
    assert response.charset == "utf-8"
    assert '<meta charset="utf-8">' in await response.text()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/aula_connect/auth/other"),
        ("get", "/api/aula_connect/auth/other/status"),
        ("post", "/api/aula_connect/auth/other/select_identity"),
    ],
)
async def test_a_flow_without_a_login_is_not_found(hass: HomeAssistant, page: Any, method: str, path: str) -> None:
    _login(hass)
    response = await getattr(page, method)(path)
    assert response.status == 404


async def test_the_status_shows_a_qr_code_while_mitid_asks_for_one(hass: HomeAssistant, page: Any) -> None:
    _login(hass, ("<svg>1</svg>", "<svg>2</svg>"))
    response = await page.get("/api/aula_connect/auth/flow/status")
    status = await response.json()
    assert status["stage"] == "scan"
    assert status["qr_svg"] in ("<svg>1</svg>", "<svg>2</svg>")
    assert status["completed"] is False
    assert status["error"] is None


async def test_the_status_says_how_far_the_login_has_come(hass: HomeAssistant, page: Any) -> None:
    session = _login(hass)
    session.status_message = "App login was accepted, finalizing authentication"
    session.completed = True
    response = await page.get("/api/aula_connect/auth/flow/status")
    status = await response.json()
    assert status["stage"] == "finishing"
    assert status["qr_svg"] is None
    assert status["identities"] == []
    assert status["completed"] is True


async def test_the_status_offers_the_identities_and_no_qr_code(hass: HomeAssistant, page: Any) -> None:
    session = _login(hass, ("<svg>1</svg>", "<svg>2</svg>"))
    session.available_identities = ["Parent", "Teacher"]
    response = await page.get("/api/aula_connect/auth/flow/status")
    status = await response.json()
    assert status["identities"] == ["Parent", "Teacher"]
    assert status["qr_svg"] is None


async def test_the_chosen_identity_is_handed_to_the_login(hass: HomeAssistant, page: Any) -> None:
    session = _login(hass)
    session.available_identities = ["Parent", "Teacher"]
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    session.identity_future = future
    response = await page.post("/api/aula_connect/auth/flow/select_identity", json={"identity": "Teacher"})
    assert response.status == 200
    assert future.result() == "Teacher"
    assert session.available_identities is None


@pytest.mark.parametrize("body", [{"other": "Teacher"}, ["Teacher"]])
async def test_a_request_without_an_identity_chooses_none(hass: HomeAssistant, page: Any, body: object) -> None:
    session = _login(hass)
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    session.identity_future = future
    response = await page.post("/api/aula_connect/auth/flow/select_identity", json=body)
    assert response.status == 200
    assert not future.done()


async def test_an_identity_chosen_twice_keeps_the_first(hass: HomeAssistant, page: Any) -> None:
    session = _login(hass)
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    future.set_result("Parent")
    session.identity_future = future
    response = await page.post("/api/aula_connect/auth/flow/select_identity", json={"identity": "Teacher"})
    assert response.status == 200
    assert future.result() == "Parent"
