"""
Sentence embeddings with all-MiniLM-L6-v2 (384 dimensions).

Backends:
  * sentence-transformers (default) - runs the HF model with PyTorch on GPU or CPU.
  * ollama (fallback)               - the same model served by Ollama as `all-minilm`.
    Used automatically if PyTorch / sentence-transformers cannot be imported
    (e.g. a Windows Application Control policy blocking a torch DLL).

Vectors are L2-normalised, so inner product == cosine similarity in FAISS.
"""
from __future__ import annotations

import logging

import httpx
import numpy as np

log = logging.getLogger("embeddings")


class Embedder:
    def __init__(self, backend: str = "auto", model: str = "sentence-transformers/all-MiniLM-L6-v2",
                 device: str = "auto", ollama_url: str = "http://127.0.0.1:11434",
                 ollama_model: str = "all-minilm"):
        self.requested = backend
        self.model_name = model
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.backend = ""
        self.device = "n/a"
        self._st = None

        if backend in ("auto", "sentence-transformers"):
            try:
                self._init_st(device)
            except Exception as exc:  # noqa: BLE001
                if backend == "sentence-transformers":
                    raise
                log.warning("sentence-transformers unavailable (%s); falling back to Ollama '%s'",
                            exc, ollama_model)
        if not self.backend:
            self._init_ollama()

    # ---- backends --------------------------------------------------------
    def _init_st(self, device: str) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, lazy

        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:  # noqa: BLE001
                device = "cpu"
        self._st = SentenceTransformer(self.model_name, device=device)
        self.backend = "sentence-transformers"
        self.device = device
        log.info("Embeddings: %s on %s", self.model_name, device)

    def _init_ollama(self) -> None:
        r = httpx.post(f"{self.ollama_url}/api/embed",
                       json={"model": self.ollama_model, "input": ["ping"]}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(
                f"Ollama embedding model '{self.ollama_model}' is not available "
                f"({r.status_code}: {r.text[:200]}). Run: ollama pull {self.ollama_model}"
            )
        self.backend = "ollama"
        self.device = "ollama"
        log.info("Embeddings: Ollama %s", self.ollama_model)

    # ---- public ----------------------------------------------------------
    @property
    def signature(self) -> str:
        """Identifies the vector space; an index built with another signature is rebuilt."""
        name = self.model_name if self.backend == "sentence-transformers" else self.ollama_model
        return f"{self.backend}:{name}"

    def encode(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype="float32")
        if self.backend == "sentence-transformers":
            vecs = self._st.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)
            return np.asarray(vecs, dtype="float32")
        out = []
        for i in range(0, len(texts), batch_size):
            r = httpx.post(f"{self.ollama_url}/api/embed",
                           json={"model": self.ollama_model, "input": texts[i:i + batch_size]},
                           timeout=120)
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
        vecs = np.asarray(out, dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.clip(norms, 1e-12, None)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
