"""Telegram and WhatsApp alert channels."""
from __future__ import annotations

from .base import Notifier, register


@register
class TelegramConnector(Notifier):
    key = "telegram"
    name = "Telegram"
    category = "messaging"
    settings_keys = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    docs_url = "https://core.telegram.org/bots/api#sendmessage"
    status_note = "Live: sends alerts from a bot (create one with @BotFather) to a chat or group."

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.setting('TELEGRAM_BOT_TOKEN')}/sendMessage"
        with self.http() as client:
            client.post(url, json={"chat_id": self.setting("TELEGRAM_CHAT_ID"), "text": text,
                                   "disable_web_page_preview": True}).raise_for_status()


@register
class WhatsAppConnector(Notifier):
    key = "whatsapp"
    name = "WhatsApp"
    category = "messaging"
    settings_keys = ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TO")
    docs_url = "https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages"
    status_note = ("Live: WhatsApp Cloud API. Messages the business starts need an approved template "
                   "(WHATSAPP_TEMPLATE) unless the recipient wrote in within 24 hours.")

    def send(self, text: str) -> None:
        version = self.setting("WHATSAPP_GRAPH_VERSION", "v23.0")
        url = f"https://graph.facebook.com/{version}/{self.setting('WHATSAPP_PHONE_NUMBER_ID')}/messages"
        template = self.setting("WHATSAPP_TEMPLATE")
        with self.http() as client:
            for to in [t.strip() for t in self.setting("WHATSAPP_TO", "").split(",") if t.strip()]:
                if template:
                    # The template needs one body variable; WhatsApp caps variables at 1024 characters.
                    payload = {"messaging_product": "whatsapp", "to": to, "type": "template",
                               "template": {"name": template,
                                            "language": {"code": self.setting("WHATSAPP_TEMPLATE_LANG", "en_US")},
                                            "components": [{"type": "body", "parameters": [
                                                {"type": "text", "text": text[:1024]}]}]}}
                else:
                    payload = {"messaging_product": "whatsapp", "to": to, "type": "text",
                               "text": {"body": text[:4096], "preview_url": False}}
                client.post(url, json=payload,
                            headers={"Authorization": f"Bearer {self.setting('WHATSAPP_TOKEN')}"}).raise_for_status()
