"""
Minimal async client for the Ollama REST API (no extra SDK needed).

Endpoints used: /api/chat (stream and non-stream, JSON-schema output),
/api/tags (installed models), /api/ps (loaded models + VRAM), /api/version.
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    """Remove Qwen3 <think>...</think> blocks (older Ollama builds inline them)."""
    text = _THINK_RE.sub("", text)
    if "<think>" in text:              # unterminated block
        text = text.split("<think>")[0]
    return text.strip()


class ThinkFilter:
    """Streaming filter that hides <think>...</think> spans token by token."""

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, piece: str) -> str:
        self.buf += piece
        out = []
        while self.buf:
            if self.inside:
                end = self.buf.find("</think>")
                if end == -1:
                    self.buf = self.buf[-8:]   # keep a tail in case the tag is split
                    return "".join(out)
                self.buf = self.buf[end + len("</think>"):]
                self.inside = False
            else:
                start = self.buf.find("<think>")
                if start == -1:
                    # hold back a possible partial "<think" at the end
                    safe = len(self.buf)
                    for k in range(1, 7):
                        if "<think>".startswith(self.buf[-k:]):
                            safe = len(self.buf) - k
                            break
                    out.append(self.buf[:safe])
                    self.buf = self.buf[safe:]
                    return "".join(out)
                out.append(self.buf[:start])
                self.buf = self.buf[start + len("<think>"):]
                self.inside = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, num_ctx: int = 8192, think: bool = False,
                 keep_alive: str = "30m", timeout_s: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.think = think
        self.keep_alive = keep_alive
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(self, messages: list[dict], stream: bool, temperature: float,
                 max_tokens: int | None, fmt: dict | str | None) -> dict:
        options = {"temperature": temperature, "num_ctx": self.num_ctx}
        if max_tokens:
            options["num_predict"] = max_tokens
        body = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": options,
            "keep_alive": self.keep_alive,
            "think": self.think,
        }
        if fmt is not None:
            body["format"] = fmt
        return body

    async def chat(self, messages: list[dict], temperature: float = 0.3,
                   max_tokens: int | None = None, fmt: dict | str | None = None) -> str:
        body = self._payload(messages, False, temperature, max_tokens, fmt)
        try:
            r = await self._client.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        if r.status_code != 200:
            raise OllamaError(f"Ollama error {r.status_code}: {r.text[:300]}")
        data = r.json()
        return strip_think(data.get("message", {}).get("content", ""))

    async def chat_json(self, messages: list[dict], schema: dict, temperature: float = 0.05,
                        max_tokens: int | None = 700) -> dict:
        """Structured output: Ollama constrains generation to the JSON schema."""
        raw = await self.chat(messages, temperature=temperature, max_tokens=max_tokens, fmt=schema)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            raise OllamaError(f"Model did not return valid JSON: {raw[:200]}")

    async def stream_chat(self, messages: list[dict], temperature: float = 0.3,
                          max_tokens: int | None = None) -> AsyncIterator[str]:
        body = self._payload(messages, True, temperature, max_tokens, None)
        flt = ThinkFilter()
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode("utf-8", "replace")
                    raise OllamaError(f"Ollama error {r.status_code}: {text[:300]}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise OllamaError(data["error"])
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        visible = flt.feed(piece)
                        if visible:
                            yield visible
                    if data.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        tail = flt.flush()
        if tail:
            yield tail

    # ---- status ----------------------------------------------------------
    async def status(self) -> dict:
        info = {"reachable": False, "url": self.base_url, "model": self.model,
                "model_installed": False, "model_loaded": False, "vram_gb": None, "version": None}
        try:
            v = await self._client.get("/api/version", timeout=3.0)
            info["version"] = v.json().get("version")
            tags = (await self._client.get("/api/tags", timeout=3.0)).json().get("models", [])
            info["reachable"] = True
            names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
            want = self.model if ":" in self.model else f"{self.model}:latest"
            info["model_installed"] = want in names or self.model in names
            ps = (await self._client.get("/api/ps", timeout=3.0)).json().get("models", [])
            for m in ps:
                if m.get("name") in (self.model, want) or m.get("model") in (self.model, want):
                    info["model_loaded"] = True
                    if m.get("size_vram") is not None:
                        info["vram_gb"] = round(m["size_vram"] / 1024 ** 3, 2)
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)
        return info

    async def warmup(self) -> None:
        """Load the model into VRAM so the first question is fast."""
        await self._client.post("/api/chat", json={
            "model": self.model, "messages": [], "keep_alive": self.keep_alive})
