"""
Talking to Telegram's Bot API. The bot only ever messages people who linked it themselves
(they tap "Start" on a one-time link from Planly's settings page).
"""

from __future__ import annotations

import httpx

from app.core import config

API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def available() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_BOT_USERNAME)


def call(method: str, payload: dict) -> dict:
    if not config.TELEGRAM_BOT_TOKEN:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set on the backend.")
    try:
        r = httpx.post(f"{API}/bot{config.TELEGRAM_BOT_TOKEN}/{method}", json=payload, timeout=15)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TelegramError(f"Telegram unreachable ({e.__class__.__name__})")
    if not data.get("ok"):
        raise TelegramError(data.get("description", "Telegram said no"))
    return data.get("result") or {}


def keyboard(buttons: list[tuple[str, str]]) -> dict | None:
    """[(label, callback_data)] -> one button per row."""
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in buttons]}


def send(chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    kb = keyboard(buttons or [])
    if kb:
        payload["reply_markup"] = kb
    return call("sendMessage", payload)


def edit(chat_id: int, message_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True, "reply_markup": keyboard(buttons or []) or {"inline_keyboard": []}}
    try:
        call("editMessageText", payload)
    except TelegramError as e:
        if "not modified" not in str(e):
            raise


def answer(callback_id: str, text: str = "") -> None:
    try:
        call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})
    except TelegramError:
        pass


_webhook_checked = False


def ensure_webhook() -> None:
    """Point Telegram at our webhook (once per process). Needs PUBLIC_API_URL + a webhook secret."""
    global _webhook_checked
    if _webhook_checked or not (available() and config.PUBLIC_API_URL and config.TELEGRAM_WEBHOOK_SECRET):
        return
    url = f"{config.PUBLIC_API_URL}/telegram/webhook"
    info = call("getWebhookInfo", {})
    if info.get("url") != url:
        call("setWebhook", {"url": url, "secret_token": config.TELEGRAM_WEBHOOK_SECRET,
                            "allowed_updates": ["message", "callback_query"]})
    _webhook_checked = True
