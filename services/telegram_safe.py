import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)


async def _retry_send(request: Callable[[], Awaitable[Any]]) -> str:
    for attempt in range(3):
        try:
            await request()
            return "sent"
        except TelegramRetryAfter as exc:
            if attempt == 2:
                logger.warning("Telegram rate limit persisted after retries")
                return "failed"
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramForbiddenError:
            return "blocked"
        except TelegramBadRequest:
            return "bad_request"
        except TelegramNetworkError:
            if attempt == 2:
                logger.warning("Telegram network failure persisted after retries")
                return "failed"
            await asyncio.sleep(2**attempt)
        except Exception as exc:
            # Never log the exception text; Telegram request errors may include
            # details that should not be written to application logs.
            logger.error("Telegram send failed (%s)", type(exc).__name__)
            return "failed"

    return "failed"


async def safe_send(bot: Bot, chat_id: int | str, text: str, **kw: Any) -> str:
    """Send a Telegram message with bounded retries and a stable status result.

    Supplying ``photo=...`` in ``kw`` sends a photo with ``text`` as its
    caption. Other keyword arguments are passed to the corresponding Bot API
    method.
    """
    photo = kw.pop("photo", None)
    if photo is not None:
        return await _retry_send(lambda: bot.send_photo(chat_id=chat_id, photo=photo, caption=text, **kw))
    return await _retry_send(lambda: bot.send_message(chat_id=chat_id, text=text, **kw))


async def safe_send_document(bot: Bot, chat_id: int | str, path: str, caption: str) -> str:
    """Send a local file as a Telegram document with the same retry policy."""
    return await _retry_send(
        lambda: bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(path),
            caption=caption,
        )
    )


async def safe_send_photo(bot: Bot, chat_id: int | str, photo: Any, **kw: Any) -> str:
    """Send a photo with no caption while applying the shared retry policy."""
    return await _retry_send(lambda: bot.send_photo(chat_id=chat_id, photo=photo, **kw))
