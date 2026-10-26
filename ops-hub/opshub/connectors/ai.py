"""LLM providers used to read free text: Claude, Gemini and ChatGPT.

services.ai tries them in AI_PROVIDERS order and falls back to the
rule-based parsers when none is configured or all fail.
"""
from __future__ import annotations

from .base import AIProvider, register


@register
class ClaudeProvider(AIProvider):
    key = "claude"
    name = "Claude (Anthropic)"
    settings_keys = ("ANTHROPIC_API_KEY",)
    docs_url = "https://docs.claude.com/en/api/messages"
    status_note = "Live: Messages API. Model from ANTHROPIC_MODEL."

    def complete(self, system: str, prompt: str) -> str:
        with self.http() as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.setting("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
                json={"model": self.setting("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 1500,
                      "system": system, "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            return "".join(b.get("text", "") for b in resp.json()["content"] if b.get("type") == "text")


@register
class GeminiProvider(AIProvider):
    key = "gemini"
    name = "Gemini (Google)"
    settings_keys = ("GEMINI_API_KEY",)
    docs_url = "https://ai.google.dev/api/generate-content"
    status_note = "Live: generateContent with JSON output. Model from GEMINI_MODEL."

    def complete(self, system: str, prompt: str) -> str:
        model = self.setting("GEMINI_MODEL", "gemini-2.5-flash")
        with self.http() as client:
            resp = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": self.setting("GEMINI_API_KEY")},
                json={"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json"}},
            )
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


@register
class OpenAIProvider(AIProvider):
    key = "openai"
    name = "ChatGPT (OpenAI)"
    settings_keys = ("OPENAI_API_KEY",)
    docs_url = "https://platform.openai.com/docs/api-reference/chat"
    status_note = "Live: Chat Completions with JSON output. Model from OPENAI_MODEL."

    def complete(self, system: str, prompt: str) -> str:
        with self.http() as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.setting('OPENAI_API_KEY')}"},
                json={"model": self.setting("OPENAI_MODEL", "gpt-5-mini"),
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                      "response_format": {"type": "json_object"}},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
