"""Ollama `/api/embed`로 텍스트를 임베딩하는 클라이언트. 자세한 설명은 CODE_GUIDE 10.2."""

import os

import httpx
import numpy as np

EMBED_URL = os.environ.get("OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")

_BATCH_SIZE = 32  # 한 요청에 넣는 텍스트 개수


class Embedder:
    """텍스트를 float32 벡터로 변환."""

    def __init__(
        self,
        base_url: str = EMBED_URL,
        model: str = EMBED_MODEL,
        timeout: float = 120.0,
    ):
        self.base_url = base_url
        self.model = model
        self.client = httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        """여러 텍스트를 (N, dim) float32 배열로 반환."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]
            resp = self.client.post(
                self.base_url,
                json={"model": self.model, "input": batch},
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings")
            if not embeddings or len(embeddings) != len(batch):
                raise RuntimeError(
                    f"임베딩 응답이 비정상입니다(요청 {len(batch)}건, 응답 "
                    f"{0 if not embeddings else len(embeddings)}건). "
                    f"모델 '{self.model}'이 pull 되어 있는지 확인하세요."
                )
            vectors.extend(embeddings)

        return np.asarray(vectors, dtype=np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        """단일 텍스트를 (dim,) float32 벡터로 반환."""
        return self.embed([text])[0]

    def close(self) -> None:
        self.client.close()
