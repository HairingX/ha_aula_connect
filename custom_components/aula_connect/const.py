MANUFACTURER = "Aula"
DOMAIN = "aula_connect"

# MitID authentication constants
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_MITID_USERNAME = "mitid_username"
CONF_AUTH_METHOD = "auth_method"
AUTH_METHOD_APP = "APP"
AUTH_METHOD_TOKEN = "TOKEN"
CONF_APPROVAL = "mitid_approval"
APPROVAL_APP_HERE = "app_here"
APPROVAL_APP_ELSEWHERE = "app_elsewhere"
APPROVAL_CODE_DISPLAY = "code_display"


def auth_method(approval: str) -> str:
    """The MitID method the login uses for the way the user approves."""
    return AUTH_METHOD_TOKEN if approval == APPROVAL_CODE_DISPLAY else AUTH_METHOD_APP


def approval_of(data: dict[str, str]) -> str:
    """How the user of an entry approves, from what it stored."""
    if CONF_APPROVAL in data:
        return data[CONF_APPROVAL]
    if data.get(CONF_AUTH_METHOD) == AUTH_METHOD_TOKEN:
        return APPROVAL_CODE_DISPLAY
    return APPROVAL_APP_ELSEWHERE
