"""
Sends the compact Telegram digest via the Bot API.

Uses requests (already a project dependency) with a direct POST — no extra
libraries needed. HTML parse mode is used to avoid MarkdownV2 escaping issues.

Requires env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
All loaded from scraper.config.
"""

import logging

import requests

from scraper import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_CHARS = 4096


def send(text: str) -> None:
    """
    Send an HTML-formatted message to the configured Telegram chat.

    If the message exceeds 4 096 characters it is split and sent as
    two sequential messages (rare for a top-5 digest).

    Raises:
        RuntimeError: if bot token or chat ID are not configured.
        requests.HTTPError: on a non-2xx API response.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env."
        )

    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN)

    # Split into chunks if message exceeds Telegram's hard limit
    chunks = _split(text, _MAX_CHARS)

    for i, chunk in enumerate(chunks, 1):
        logger.info("Sending Telegram message part %d/%d (%d chars) …", i, len(chunks), len(chunk))
        resp = requests.post(
            url,
            data={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()

    logger.info("Telegram message sent successfully.")


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
