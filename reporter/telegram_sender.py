"""
Sends the compact Telegram digest via the Bot API.

Uses requests (already a project dependency) with a direct POST — no extra
libraries needed. HTML parse mode is used to avoid MarkdownV2 escaping issues.

Requires env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
TELEGRAM_CHAT_ID supports multiple recipients as a comma-separated list:
    TELEGRAM_CHAT_ID=25338446,987654321,-1001234567890

All loaded from scraper.config.
"""

import logging

import requests

from scraper import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_CHARS = 4096


def _chat_ids() -> list[str]:
    """Parse TELEGRAM_CHAT_ID — supports comma-separated list of IDs."""
    raw = config.TELEGRAM_CHAT_ID or ""
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def send(text: str) -> None:
    """
    Send an HTML-formatted message to all configured Telegram chats.

    TELEGRAM_CHAT_ID can be a single ID or comma-separated list:
        25338446
        25338446,987654321,-1001234567890

    Each recipient gets the same message. If the message exceeds 4 096
    characters it is split and sent as sequential messages.

    Raises:
        RuntimeError: if bot token or chat IDs are not configured.
        requests.HTTPError: on a non-2xx API response.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN must be set in .env.")

    chat_ids = _chat_ids()
    if not chat_ids:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID must be set in .env. "
            "Use a comma-separated list for multiple recipients."
        )

    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN)
    chunks = _split(text, _MAX_CHARS)

    for chat_id in chat_ids:
        logger.info("Sending Telegram digest to chat_id=%s …", chat_id)
        for i, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                logger.info("  part %d/%d (%d chars)", i, len(chunks), len(chunk))
            resp = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            resp.raise_for_status()
        logger.info("  ✓ sent to %s", chat_id)

    logger.info("Telegram digest sent to %d recipient(s).", len(chat_ids))


def _split(text: str, max_len: int) -> list[str]:
    """Split text on newlines, keeping each chunk ≤ max_len characters."""
    if len(text) <= max_len:
        return [text]

    chunks, current = [], []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > max_len and current:
            chunks.append("".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks
