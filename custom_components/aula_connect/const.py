"""The integration's constants, and the unique id of an entry."""

from datetime import timedelta
import hashlib

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


def anomaly_issue_id(kind: str, device_id: str) -> str:
    """The id of the repair issue for one kind of anomaly of an entry."""
    return f"{kind}_{device_id}"
