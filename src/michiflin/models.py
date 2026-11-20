from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from .config import Settings
from .errors import AppError


class ModelGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def catalogue(self) -> list[dict]:
        result = []
        if self.settings.local_model_url:
            result.append({"id": "local", "name": "Local model", "local": True})
        for provider in self.settings.online_providers:
            if all(provider.get(key) for key in ("id", "name", "url", "model", "key")):
                result.append({"id": provider["id"], "name": provider["name"], "local": False})
        return result

    def complete(self, text: object, model: object, history: object, *, local_only: bool = False) -> dict:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
            raise AppError("invalid_prompt")
        if not isinstance(model, str) or not isinstance(history, list):
            raise AppError("invalid_prompt")
        local = model == "local" or local_only
        if local:
            endpoint = self.settings.local_model_url
            if not endpoint:
                raise AppError("local_model_unavailable")
            parsed = urlsplit(endpoint)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise AppError("local_model_configuration_invalid")
            name, token, model_id = "Local model", "", "local"
        else:
            provider = next((item for item in self.settings.online_providers if item.get("id") == model), None)
            if not provider:
                raise AppError("model_unavailable")
            endpoint, token, model_id = (str(provider.get(key, "")) for key in ("url", "key", "model"))
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or parsed.username or parsed.password or not token:
                raise AppError("model_configuration_invalid")
            name = str(provider.get("name", model))
        messages = [{"role": "system", "content": "You are Michiflin AI. Be helpful and precise. Never claim to have used a tool unless a verified tool result was supplied. Reply in the user's language."}]
        budget = 18000
        retained = []
        for turn in reversed(history[-12:]):
            if not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant"}:
                continue
            content = turn.get("content")
            if not isinstance(content, str):
                continue
            content = content[:6000]
            if len(content) > budget:
                break
            budget -= len(content)
            retained.insert(0, {"role": turn["role"], "content": content})
        messages.extend(retained)
        messages.append({"role": "user", "content": text})
        try:
            with requests.Session() as session:
                session.trust_env = False
                headers = {"Authorization": "Bearer " + token} if token else {}
                with session.post(endpoint, json={"model": model_id, "messages": messages, "max_tokens": 1400},
                                  headers=headers, timeout=(5, 120), allow_redirects=False, stream=True) as response:
                    if response.status_code == 429:
                        raise AppError("model_rate_limited", retryable=True)
                    if response.status_code != 200:
                        raise AppError("model_unavailable", retryable=True)
                    body = bytearray()
                    deadline = time.monotonic() + 125
                    for chunk in response.iter_content(16384):
                        body.extend(chunk)
                        if len(body) > 300000 or time.monotonic() > deadline:
                            raise AppError("model_response_limit")
                    import json
                    data = json.loads(body)
            choice = data["choices"][0]
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise AppError("model_response_invalid")
            return {"text": answer[:48000], "model": name, "local": local,
                    "truncated": choice.get("finish_reason") == "length" or len(answer) > 48000}
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            raise AppError("model_outcome_unconfirmed", retryable=False) from None
