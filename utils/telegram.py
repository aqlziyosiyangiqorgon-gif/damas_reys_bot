import asyncio
import logging
from typing import Any, Callable

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger(__name__)


async def safe_telegram_call(
    method: Callable[..., Any],
    *args: Any,
    retries: int = 3,
    **kwargs: Any,
) -> bool:
    """Retry transient Telegram failures without logging request URLs or tokens."""
    for attempt in range(max(1, retries)):
        try:
            await method(*args, **kwargs)
            return True
        except TelegramRetryAfter as exc:
            if attempt + 1 >= retries:
                logger.warning("Telegram rate limit persisted after retries")
                return False
            await asyncio.sleep(min(exc.retry_after, 60))
        except (TelegramNetworkError, TelegramServerError):
            if attempt + 1 >= retries:
                logger.warning("Telegram transient API failure persisted after retries")
                return False
            await asyncio.sleep(min(0.5 * (2 ** attempt), 5))
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.info("Telegram rejected a message (%s)", type(exc).__name__)
            return False
    return False


async def safe_refresh_edit(callback: Any, text: str, reply_markup: Any) -> None:
    """Refresh an inline view, treating Telegram's no-change response as success."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).casefold():
            raise
    finally:
        await safe_telegram_call(callback.answer, "Yangilandi!")
