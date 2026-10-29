"""The integration's constants, and the unique id of an entry."""

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
