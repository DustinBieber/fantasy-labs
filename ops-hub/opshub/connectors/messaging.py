"""Telegram and WhatsApp: alert channels, and two-way chat (see services.chatops)."""
from __future__ import annotations

from .base import Notifier, register


@register
class TelegramConnector(Notifier):
    key = "telegram"
    name = "Telegram"
    category = "messaging"
    settings_keys = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    docs_url = "https://core.telegram.org/bots/api#sendmessage"
    status_note = ("Live: alerts to TELEGRAM_CHAT_ID, and the bot answers commands (alerts, order, stock, ship) "
                   "and logs carrier updates.")

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.setting('TELEGRAM_BOT_TOKEN')}/{method}"

    def send(self, text: str, chat_id: str | None = None) -> None:
        with self.http() as client:
            for start in range(0, len(text), 4000):  # Telegram caps messages at 4096 characters
                client.post(self._url("sendMessage"), json={"chat_id": chat_id or self.setting("TELEGRAM_CHAT_ID"),
                                                            "text": text[start:start + 4000],
                                                            "disable_web_page_preview": True}).raise_for_status()

    def test(self) -> str:
        with self.http() as client:
            me = client.get(self._url("getMe"))
            me.raise_for_status()
        bot = me.json()["result"]
        self.send("\u2705 Ops Hub is connected. Send \"help\" for commands.")
        return f"Bot @{bot.get('username')} sent a test message to chat {self.setting('TELEGRAM_CHAT_ID')}"

    def get_updates(self, offset: int) -> list[dict]:
        with self.http() as client:
            resp = client.get(self._url("getUpdates"), params={"offset": offset, "timeout": 0,
                                                               "allowed_updates": '["message","edited_message"]'})
            resp.raise_for_status()
            return resp.json().get("result", [])


@register
class WhatsAppConnector(Notifier):
    key = "whatsapp"
    name = "WhatsApp"
    category = "messaging"
    settings_keys = ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TO")
    docs_url = "https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages"
    status_note = ("Live: WhatsApp Cloud API alerts; replies to staff and carriers through the /webhooks/whatsapp "
                   "webhook. Messages the business starts need an approved template (WHATSAPP_TEMPLATE) unless "
                   "the recipient wrote in within 24 hours.")

    def test(self) -> str:
        version = self.setting("WHATSAPP_GRAPH_VERSION", "v23.0")
        with self.http() as client:
            resp = client.get(f"https://graph.facebook.com/{version}/{self.setting('WHATSAPP_PHONE_NUMBER_ID')}",
                              params={"fields": "display_phone_number,verified_name,quality_rating"},
                              headers={"Authorization": f"Bearer {self.setting('WHATSAPP_TOKEN')}"})
            resp.raise_for_status()
        d = resp.json()
        tip = "" if self.setting("WHATSAPP_TEMPLATE") else "; set an approved template so alerts can start conversations"
        return f"Number {d.get('display_phone_number')} ({d.get('verified_name')}) is ready{tip}"

    def send(self, text: str, to: str | None = None) -> None:
        version = self.setting("WHATSAPP_GRAPH_VERSION", "v23.0")
        url = f"https://graph.facebook.com/{version}/{self.setting('WHATSAPP_PHONE_NUMBER_ID')}/messages"
        template = None if to else self.setting("WHATSAPP_TEMPLATE")  # replies are inside the 24h window
        recipients = [to] if to else [t.strip() for t in self.setting("WHATSAPP_TO", "").split(",") if t.strip()]
        with self.http() as client:
            for number in recipients:
                if template:
                    # The template needs one body variable; WhatsApp caps variables at 1024 characters.
                    payload = {"messaging_product": "whatsapp", "to": number, "type": "template",
                               "template": {"name": template,
                                            "language": {"code": self.setting("WHATSAPP_TEMPLATE_LANG", "en_US")},
                                            "components": [{"type": "body", "parameters": [
                                                {"type": "text", "text": text[:1024]}]}]}}
                else:
                    payload = {"messaging_product": "whatsapp", "to": number, "type": "text",
                               "text": {"body": text[:4096], "preview_url": False}}
                client.post(url, json=payload,
                            headers={"Authorization": f"Bearer {self.setting('WHATSAPP_TOKEN')}"}).raise_for_status()
