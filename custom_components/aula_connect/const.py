"""The integration's constants, and the unique id of an entry."""

from datetime import timedelta
import hashlib

from aula_connect import AnomalyKind

MANUFACTURER = "Aula"
DOMAIN = "aula_connect"

CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_MITID_USERNAME = "mitid_username"
CONF_AUTH_METHOD = "auth_method"
AUTH_METHOD_APP = "APP"
CONF_ENTITY_PREFIX = "entity_prefix"
"""The name the entry was set up under, which its entities' unique ids start with."""


def account_id(mitid_username: str) -> str:
    """The unique id of the entry for a MitID account; the user ID itself is a credential."""
    return hashlib.sha256(mitid_username.strip().encode()).hexdigest()


ANOMALY_WINDOW = timedelta(days=1)
"""How long what Aula sent that is not known keeps its repair issue after it was last met."""


def anomaly_issue_id(kind: str, entry_id: str) -> str:
    """The id of the repair issue for one kind of anomaly of an entry."""
    return f"{kind}_{entry_id}"


CONTRIBUTING_URL = "https://github.com/HairingX/ha_aula_connect/blob/main/CONTRIBUTING.md"
"""How to report a problem: search first, then the form that fits."""

ANOMALY_HELP: dict[AnomalyKind, str] = {
    AnomalyKind.API_VERSION_CHANGED: f"{CONTRIBUTING_URL}#aulas-api-has-a-new-version",
    AnomalyKind.UNKNOWN_NOTIFICATION_AREA: f"{CONTRIBUTING_URL}#aula-sends-notifications-aula-connect-does-not-know",
    AnomalyKind.UNREADABLE_RESPONSE: f"{CONTRIBUTING_URL}#aula-connect-could-not-read-an-answer-from-aula",
}
"""Where each kind of repair issue tells what to do."""
