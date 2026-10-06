"""Explicitly authorized synchronous providers; never log headers or error bodies."""
import math
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


class ProviderError(ValueError):
    pass


def load_env(path=None):
    if not path:
        return
    allowed = {"DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "NATIVE_ALLOW_LIVE"}
    values = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in {"DIFY_BASE_URL", "DIFY_APP_API_KEY", "DIFY_DATASET_API_KEY", "DIFY_DATASET_ID", "DIFY_ALLOW_LIVE"} and sep:
            continue  # Legacy template entries do not configure or authorize native APIs.
        if not sep or key not in allowed:
            raise ValueError("Native env file accepts only DEEPSEEK_API_KEY, DASHSCOPE_API_KEY, NATIVE_ALLOW_LIVE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def check_authorization(embedding_only=False):
    if os.environ.get("NATIVE_ALLOW_LIVE", "").lower() != "true":
        raise ValueError("Live disabled: explicitly set NATIVE_ALLOW_LIVE=true for authorized API usage")
    keys = ["DASHSCOPE_API_KEY"] + ([] if embedding_only else ["DEEPSEEK_API_KEY"])
    if any(not os.environ.get(k) for k in keys):
        raise ValueError("Missing local credentials: " + ", ".join(keys) + "; configure locally, never paste keys into chat")


def validate_vector(vector, dimensions):
    if (not isinstance(vector, list) or len(vector) != dimensions
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
            or not any(v != 0 for v in vector)):
        raise ProviderError("invalid_embedding_vector")
    return vector


class Provider:
    def __init__(self, config, key_name, session=None, sleep=time.sleep):
        self.config, self.key_name = config, key_name
        self.session = session or requests.Session()
        self.sleep = sleep
        url = urlparse(config.get("base_url") or "")
        # Restrict credential destinations to the explicitly selected supplier.
        host = url.hostname or ""
        valid_host = host == "api.deepseek.com" if key_name == "DEEPSEEK_API_KEY" else (
            host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} or host.endswith(".maas.aliyuncs.com"))
        if (url.scheme != "https" or not valid_host or url.username or url.password
                or url.query or url.fragment or "{" in config.get("base_url", "")):
            raise ValueError("Set an official supplier HTTPS base_url for your account/region in config")
        if not config.get("model") or "YOUR_" in config["model"]:
            raise ValueError("Set account-authorized API model ID in config; Dify plugin labels are not API IDs")

    def post(self, endpoint, body):
        if not os.environ.get(self.key_name):
            raise ProviderError("missing_credentials")
        for attempt in range(self.config.get("max_retries_429", 2) + 1):
            try:
                r = self.session.post(self.config["base_url"].rstrip("/") + endpoint,
                    json=body, headers={"Authorization": "Bearer " + os.environ[self.key_name]},
                    timeout=self.config.get("timeout_seconds", 60), allow_redirects=False)
            except requests.Timeout:
                raise ProviderError("timeout_completion_unknown") from None
            except requests.RequestException:
                raise ProviderError("network_completion_unknown") from None
            if r.status_code == 429 and attempt < self.config.get("max_retries_429", 2):
                self.sleep(min(2 ** attempt, 8))
                continue
            if r.status_code != 200:
                raise ProviderError(f"http_{r.status_code}")
            try:
                result = r.json()
            except ValueError:
                raise ProviderError("invalid_provider_json") from None
            if not isinstance(result, dict):
                raise ProviderError("invalid_provider_schema")
            return result
        raise ProviderError("http_429")


class Embeddings(Provider):
    def __init__(self, config, **kwargs):
        super().__init__(config, "DASHSCOPE_API_KEY", **kwargs)

    def embed(self, texts):
        if not texts or any(not t or len(t) > self.config["max_input_chars"] for t in texts):
            raise ProviderError("embedding_input_budget_exceeded; create a separate snapshot, never truncate")
        result = self.post("/embeddings", {"model": self.config["model"], "input": texts,
            "dimensions": self.config["dimensions"], "encoding_format": "float"})
        data = result.get("data")
        if (not isinstance(data, list) or len(data) != len(texts)
                or any(not isinstance(v, dict) or type(v.get("index")) is not int for v in data)
                or sorted(v["index"] for v in data) != list(range(len(texts)))):
            raise ProviderError("invalid_embedding_schema")
        ordered = sorted(data, key=lambda v: v["index"])
        return [validate_vector(v.get("embedding"), self.config["dimensions"]) for v in ordered], result.get("usage")


class ChatModel(Provider):
    def __init__(self, config, **kwargs):
        super().__init__(config, "DEEPSEEK_API_KEY", **kwargs)

    def complete(self, messages):
        result = self.post("/chat/completions", {"model": self.config["model"], "messages": messages,
            "temperature": self.config.get("temperature", 0), "max_tokens": self.config.get("max_tokens", 2048),
            "thinking": {"type": "disabled"}, "stream": False, "response_format": {"type": "json_object"}})
        try:
            choice = result["choices"][0]
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or choice.get("finish_reason") != "stop":
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("invalid_or_truncated_chat_response") from None
        return answer, result.get("usage")
