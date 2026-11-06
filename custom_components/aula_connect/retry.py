"""Reading a part of Aula once more after a pause, before taking it as failed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging

from aula_connect.api.aula_errors import AulaCredentialError

_LOGGER = logging.getLogger(__name__)

RETRY_DELAY = timedelta(seconds=10)
"""How long to wait before reading a part of Aula once more."""


def _always(result: object) -> bool:
    return True


async def read_twice[T](what: str, read: Callable[[], Awaitable[T]], complete: Callable[[T], bool] = _always) -> T:
    """What `read` gives, read once more after `RETRY_DELAY` where it failed or was not `complete`.

    Raises:
        AulaCredentialError: Aula refused the login; it is never read again here.
        Exception: The second read failed as well.
    """
    try:
        result = await read()
    except AulaCredentialError:
        raise
    except Exception as err:
        _LOGGER.debug("Reading %s failed (%s); trying once more", what, type(err).__name__)
    else:
        if complete(result):
            return result
        _LOGGER.debug("Reading %s gave only a part; trying once more", what)
    await asyncio.sleep(RETRY_DELAY.total_seconds())
    return await read()
