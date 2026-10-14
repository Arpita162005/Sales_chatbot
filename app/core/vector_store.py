"""
FAISS vector store (exact inner-product search over normalised vectors).

Each store is saved as three files in storage/index/:
  <name>.faiss       FAISS IndexFlatIP
  <name>.npy         the raw vectors (lets search work even without faiss)
  <name>.meta.json   payloads + embedding signature
If the faiss package cannot be imported, a NumPy dot-product search is used
transparently - results are identical for a flat index.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("vector_store")

try:
    import faiss  # type: ignore
    HAS_FAISS = True
except Exception:  # noqa: BLE001
    faiss = None
    HAS_FAISS = False
    log.warning("faiss not importable - using NumPy search fallback")


class VectorStore:
    def __init__(self, name: str, index_dir: Path):
        self.name = name
        self.dir = Path(index_dir)
        self.payloads: list[dict] = []
        self.signature = ""
        self._vecs: np.ndarray | None = None
        self._index = None

    # ---- paths -----------------------------------------------------------
    @property
    def _faiss_path(self) -> Path:
        return self.dir / f"{self.name}.faiss"

    @property
    def _npy_path(self) -> Path:
        return self.dir / f"{self.name}.npy"

    @property
    def _meta_path(self) -> Path:
        return self.dir / f"{self.name}.meta.json"

    @property
    def engine(self) -> str:
        return "faiss" if self._index is not None else "numpy"

    def __len__(self) -> int:
        return len(self.payloads)

    # ---- build / persist ---------------------------------------------------
    def build(self, texts: list[str], payloads: list[dict], embedder) -> "VectorStore":
        assert len(texts) == len(payloads)
        vecs = embedder.encode(texts)
        self._vecs = vecs
        self.payloads = payloads
        self.signature = embedder.signature
        self._make_index()
        self.save()
        log.info("Built vector store '%s' with %d vectors (%s)", self.name, len(texts), self.engine)
        return self

    def _make_index(self) -> None:
        self._index = None
        if HAS_FAISS and self._vecs is not None and len(self._vecs):
            idx = faiss.IndexFlatIP(self._vecs.shape[1])
            idx.add(self._vecs)
            self._index = idx

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        np.save(self._npy_path, self._vecs)
        if self._index is not None:
            faiss.write_index(self._index, str(self._faiss_path))
        self._meta_path.write_text(
            json.dumps({"signature": self.signature, "payloads": self.payloads}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def exists(self) -> bool:
        return self._meta_path.exists() and self._npy_path.exists()

    def load(self) -> "VectorStore":
        meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
        self.payloads = meta["payloads"]
        self.signature = meta.get("signature", "")
        self._vecs = np.load(self._npy_path).astype("float32")
        if HAS_FAISS and self._faiss_path.exists():
            self._index = faiss.read_index(str(self._faiss_path))
        else:
            self._make_index()
        return self

    # ---- search ------------------------------------------------------------
    def search(self, query_vec: np.ndarray, k: int = 4, min_score: float = -1.0,
               where: dict | None = None) -> list[tuple[float, dict]]:
        if not self.payloads:
            return []
        q = np.asarray(query_vec, dtype="float32").reshape(1, -1)
        fetch = len(self.payloads) if where else min(k, len(self.payloads))
        if self._index is not None:
            scores, ids = self._index.search(q, fetch)
            pairs = zip(scores[0].tolist(), ids[0].tolist())
        else:
            sims = (self._vecs @ q[0])
            order = np.argsort(-sims)[:fetch]
            pairs = ((float(sims[i]), int(i)) for i in order)
        out = []
        for score, i in pairs:
            if i < 0 or score < min_score:
                continue
            p = self.payloads[i]
            if where and any(p.get(key) != val for key, val in where.items()):
                continue
            out.append((float(score), p))
            if len(out) >= k:
                break
        return out
