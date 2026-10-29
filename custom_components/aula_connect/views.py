"""The MitID login page, and what it asks Home Assistant while the login runs."""

from __future__ import annotations

import time
from typing import cast

from aiohttp import web
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .auth_session import auth_session, qr_codes_svg


class AulaAuthView(HomeAssistantView):
    """The page that shows the QR code of a MitID login and how far it has come."""

    url = "/api/aula_connect/auth/{flow_id}"
    name = "api:aula_connect:auth"
    # The page is opened from the setup dialog in a new tab, without Home Assistant's token; the
    # flow id in its address is known only to the one who started the setup.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        """The login page of the flow `flow_id`, or 404 when it has no login."""
        if auth_session(self.hass, flow_id) is None:
            return web.Response(status=404, text="Session not found or expired.")
        return web.Response(text=self._get_html(flow_id), content_type="text/html", charset="utf-8")

    def _get_html(self, flow_id: str) -> str:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Aula - MitID</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; text-align: center; padding: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        #qr-container {{ margin: 20px auto; display: flex; align-items: center; justify-content: center; }}
        #qr-container svg {{ max-width: 100%; height: auto; }}
        .hidden {{ display: none !important; }}
        #status {{ font-weight: 500; margin: 20px 0; font-size: 1.3em; color: #333; line-height: 1.4; }}
        #code {{ font-size: 3em; font-weight: 700; letter-spacing: 0.2em; margin: 10px 0; }}
        button {{ background-color: #03a9f4; color: white; border: none; padding: 12px 20px; margin: 5px; border-radius: 4px; cursor: pointer; font-size: 18px; }}
        button:hover {{ background-color: #0288d1; }}
        .error {{ color: #d32f2f; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>MitID</h1>
        <div id="status"></div>
        <div id="code" class="hidden"></div>
        <div id="qr-container" class="hidden"></div>
        <div id="identity-selection" class="hidden">
            <h3 id="identity-title"></h3>
            <div id="identity-buttons"></div>
        </div>
    </div>

    <script>
        const flowId = "{flow_id}";
        const TEXTS = {{
            da: {{
                open_app: "Åbn MitID-appen på din telefon. QR-koden, du skal scanne, kommer frem her.",
                scan: "Scan QR-koden med MitID-appen på din telefon.",
                code: "Skriv denne kode i MitID-appen:",
                approve: "Godkend nu i MitID-appen.",
                finishing: "Godkendt. Logger ind på Aula …",
                identity: "Vælg, hvem du vil logge ind som",
                identity_chosen: "Valgt. Logger ind …",
                completed: "Du er logget ind. Du kan lukke denne side.",
                close: "Luk siden",
            }},
            en: {{
                open_app: "Open the MitID app on your phone. The QR code to scan appears here.",
                scan: "Scan the QR code with the MitID app on your phone.",
                code: "Type this code in the MitID app:",
                approve: "Approve in the MitID app now.",
                finishing: "Approved. Logging in to Aula …",
                identity: "Choose who to log in as",
                identity_chosen: "Chosen. Logging in …",
                completed: "You are logged in. You can close this page.",
                close: "Close the page",
            }},
        }};
        const t = TEXTS[(navigator.language || "").toLowerCase().startsWith("da") ? "da" : "en"];
        const show = (id, visible) => document.getElementById(id).classList.toggle("hidden", !visible);

        document.getElementById("identity-title").innerText = t.identity;

        function render(data) {{
            const status = document.getElementById("status");
            show("code", data.stage === "code");
            show("qr-container", data.stage === "scan");
            if (data.stage === "open_app") {{
                status.innerText = t.open_app;
            }} else if (data.stage === "scan") {{
                status.innerText = t.scan;
                document.getElementById("qr-container").innerHTML = data.qr_svg;
            }} else if (data.stage === "code") {{
                status.innerText = t.code;
                document.getElementById("code").innerText = data.code;
            }} else if (data.stage === "approve") {{
                status.innerText = t.approve;
            }} else if (data.stage === "finishing") {{
                status.innerText = t.finishing;
            }} else if (data.message) {{
                status.innerText = data.message;
            }}
        }}

        function updateStatus() {{
            fetch(`/api/aula_connect/auth/${{flowId}}/status`)
                .then(response => response.json())
                .then(data => {{
                    if (data.error) {{
                        const status = document.getElementById("status");
                        status.innerText = data.error;
                        status.className = "error";
                        ["code", "qr-container", "identity-selection"].forEach(id => show(id, false));
                        return;
                    }}

                    if (data.identities && data.identities.length > 0) {{
                        const container = document.getElementById("identity-buttons");
                        container.innerHTML = "";
                        data.identities.forEach((name, index) => {{
                            const btn = document.createElement("button");
                            btn.innerText = name;
                            btn.onclick = () => selectIdentity(index + 1);
                            container.appendChild(btn);
                        }});
                        ["code", "qr-container"].forEach(id => show(id, false));
                        document.getElementById("status").innerText = "";
                        show("identity-selection", true);
                    }} else if (data.completed) {{
                        ["code", "qr-container", "identity-selection"].forEach(id => show(id, false));
                        document.getElementById("status").innerText = t.completed;
                        if (!document.getElementById("close-btn")) {{
                            const btn = document.createElement("button");
                            btn.id = "close-btn";
                            btn.innerText = t.close;
                            btn.onclick = () => window.close();
                            document.querySelector(".container").appendChild(btn);
                        }}
                        return;
                    }} else {{
                        show("identity-selection", false);
                        render(data);
                    }}

                    setTimeout(updateStatus, 1000);
                }})
                .catch(err => {{
                    console.error(err);
                    setTimeout(updateStatus, 2000);
                }});
        }}

        function selectIdentity(index) {{
            fetch(`/api/aula_connect/auth/${{flowId}}/select_identity`, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ identity: index }})
            }});
            show("identity-selection", false);
            document.getElementById("status").innerText = t.identity_chosen;
        }}

        updateStatus();
    </script>
</body>
</html>
        """


OTP_PREFIX = "Please use the following OTP code in the app: "
"""How the MitID client says which code to type in the app."""


def login_stage(message: str, qr_shown: bool) -> tuple[str, str | None]:
    """The step of the MitID login the page shows, from the MitID client's status message.

    Returns the step - open_app, scan, code, approve or finishing - and the code to type in
    the app at the code step.
    """
    if message.startswith(OTP_PREFIX):
        return "code", message.removeprefix(OTP_PREFIX).strip()
    if qr_shown:
        return "scan", None
    if message.startswith("The OTP/QR code has been verified"):
        return "approve", None
    if message.startswith(("App login was accepted", "Password accepted", "Authentication successful")):
        return "finishing", None
    return "open_app", None


class AulaAuthStatusView(HomeAssistantView):
    """How far a MitID login has come, as the login page asks for it each second."""

    url = "/api/aula_connect/auth/{flow_id}/status"
    name = "api:aula_connect:auth:status"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        """The login's step, its QR code or code, and whether it has completed or failed."""
        session = auth_session(self.hass, flow_id)
        if session is None:
            return web.json_response({"error": "Session expired or not found"}, status=404)

        qr_svg: str | None = None
        if not session.available_identities:
            codes = await self.hass.async_add_executor_job(qr_codes_svg, session.client)
            if codes is not None:
                # MitID shows two QR codes in turn, a second each.
                qr_svg = codes[int(time.time()) % 2]

        stage, code = login_stage(session.status_message, qr_svg is not None)
        return web.json_response(
            {
                "message": session.status_message,
                "stage": stage,
                "code": code,
                "qr_svg": qr_svg,
                "identities": session.available_identities or [],
                "completed": session.completed,
                "error": session.error,
            }
        )


class AulaAuthSelectIdentityView(HomeAssistantView):
    """The identity the user chose, of several MitID offered."""

    url = "/api/aula_connect/auth/{flow_id}/select_identity"
    name = "api:aula_connect:auth:select_identity"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request, flow_id: str) -> web.Response:
        """Hand the chosen identity to the login waiting for it."""
        session = auth_session(self.hass, flow_id)
        if session is None:
            return web.json_response({"error": "Session not found"}, status=404)

        body: object = await request.json()
        identity: object = cast(dict[str, object], body).get("identity") if isinstance(body, dict) else None
        future = session.identity_future
        if identity is not None and future is not None and not future.done():
            future.set_result(str(identity))
            session.available_identities = None
        return web.json_response({"status": "ok"})
