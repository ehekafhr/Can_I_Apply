"""직무 벡터를 이용한 코사인 유사도 검색.

동작 개요
---------
1. 직무(Position) 벡터는 embed.py가 미리 계산해 position_embeddings에 저장해 둔다.
2. 검색 시에는 이 모듈이
   - 직무 벡터 전체를 메모리에 (정규화된) 행렬로 한 번만 올려두고(_matrix_cache),
   - 검색어는 임베딩해서 그 행렬과 내적(=코사인 유사도)한 뒤,
   - 임계값 이상만 유사도순으로 돌려준다.
3. 자주 쓰는 검색어는 반복 임베딩하지 않도록 2단계 캐시를 둔다.
   - 인메모리 LRU(_query_lru): 프로세스가 사는 동안 가장 빠름
   - DB(query_cache 테이블): 서버를 재시작해도 유지

벡터는 모두 L2 정규화해서 다루므로, 내적이 곧 코사인 유사도(−1~1)다.
"""

import threading
from collections import OrderedDict

import numpy as np

from lib.db import SessionLocal
from lib.embedder import EMBED_MODEL, Embedder
from lib.models import Position, PositionEmbedding, QueryCache

# 검색어 임베딩 인메모리 캐시 최대 개수
_QUERY_LRU_MAX = 256
# 기본 유사도 임계값. 검색어(단어 1개) vs 직무 텍스트(제목+태그+설명)는
# 길이 비대칭 때문에 코사인이 다소 낮게 나와, bge-m3 기준 0.45 안팎이
# "관련 있음"의 실용적 경계였다(예: "인공지능" → AI 연구원 0.46).
DEFAULT_MIN_SIM = 0.45

_lock = threading.Lock()
# 직무 벡터 행렬 캐시: {"model", "ids"(np.int64), "matrix"(정규화 N×dim), "count"}
_matrix_cache: dict | None = None
# 검색어 → 정규화 벡터. 키는 (query, model)
_query_lru: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()
_embedder: Embedder | None = None


def build_position_text(p: Position) -> str:
    """임베딩할 직무 텍스트. 제목·태그·설명을 합쳐 의미를 풍부하게 한다."""
    parts = [p.title or "", p.tags or "", p.description or ""]
    return "\n".join(part for part in parts if part).strip()


def _get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def _normalize(mat: np.ndarray) -> np.ndarray:
    """행 단위 L2 정규화. 0벡터는 그대로 둬서 0 유사도가 되게 한다."""
    norms = np.linalg.norm(mat, axis=-1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return mat / norms


def _load_matrix(model: str) -> dict:
    """position_embeddings를 메모리 행렬로 올린다(모델·건수가 그대로면 캐시 재사용)."""
    global _matrix_cache
    with SessionLocal() as session:
        count = (
            session.query(PositionEmbedding)
            .filter(PositionEmbedding.model == model)
            .count()
        )

    cache = _matrix_cache
    if cache is not None and cache["model"] == model and cache["count"] == count:
        return cache

    with SessionLocal() as session:
        rows = (
            session.query(
                PositionEmbedding.position_id,
                PositionEmbedding.vector,
                PositionEmbedding.dim,
            )
            .filter(PositionEmbedding.model == model)
            .all()
        )

    if not rows:
        cache = {"model": model, "ids": np.zeros(0, np.int64), "matrix": np.zeros((0, 0), np.float32), "count": 0}
    else:
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        dim = rows[0][2]
        matrix = np.empty((len(rows), dim), dtype=np.float32)
        for i, r in enumerate(rows):
            matrix[i] = np.frombuffer(r[1], dtype=np.float32)
        cache = {"model": model, "ids": ids, "matrix": _normalize(matrix), "count": count}

    _matrix_cache = cache
    return cache


def _query_from_db(query: str, model: str) -> np.ndarray | None:
    with SessionLocal() as session:
        row = session.get(QueryCache, {"query": query, "model": model})
        if row is None:
            return None
        row.hit_count += 1
        session.commit()
        return np.frombuffer(row.vector, dtype=np.float32).copy()


def _query_to_db(query: str, model: str, vec: np.ndarray) -> None:
    with SessionLocal() as session:
        if session.get(QueryCache, {"query": query, "model": model}) is not None:
            return
        session.add(
            QueryCache(
                query=query,
                model=model,
                dim=int(vec.shape[0]),
                vector=vec.astype(np.float32).tobytes(),
            )
        )
        session.commit()


def get_query_vector(query: str, model: str = EMBED_MODEL) -> np.ndarray:
    """검색어의 (정규화된) 임베딩 벡터. 메모리 LRU → DB → 임베딩 순으로 조회."""
    query = query.strip()
    key = (query, model)

    with _lock:
        vec = _query_lru.get(key)
        if vec is not None:
            _query_lru.move_to_end(key)  # 최근 사용으로
            return vec

    # DB 영속 캐시
    vec = _query_from_db(query, model)
    if vec is None:
        # 임베딩(정규화해서 저장)
        raw = _get_embedder().embed_one(query)
        vec = _normalize(raw.reshape(1, -1))[0]
        _query_to_db(query, model, vec)

    with _lock:
        _query_lru[key] = vec
        _query_lru.move_to_end(key)
        while len(_query_lru) > _QUERY_LRU_MAX:
            _query_lru.popitem(last=False)  # 가장 오래된 것 제거
    return vec


def search_similar(
    query: str,
    min_sim: float = DEFAULT_MIN_SIM,
    model: str = EMBED_MODEL,
    candidate_ids: set[int] | None = None,
) -> list[tuple[int, float]]:
    """검색어와 유사한 직무를 (position_id, 유사도) 리스트로, 유사도 내림차순 반환.

    candidate_ids가 주어지면 그 집합 안에서만 계산한다(다른 필터와 결합용).
    임계값(min_sim) 미만은 제외한다.
    """
    query = query.strip()
    if not query:
        return []

    cache = _load_matrix(model)
    if cache["count"] == 0:
        return []

    qv = get_query_vector(query, model)
    sims = cache["matrix"] @ qv  # 정규화돼 있으므로 내적 = 코사인 유사도
    ids = cache["ids"]

    keep = sims >= min_sim
    hit_ids = ids[keep]
    hit_sims = sims[keep]

    order = np.argsort(-hit_sims)  # 유사도 내림차순
    results: list[tuple[int, float]] = []
    for idx in order:
        pid = int(hit_ids[idx])
        if candidate_ids is not None and pid not in candidate_ids:
            continue
        results.append((pid, float(hit_sims[idx])))
    return results


def reset_cache() -> None:
    """테스트/재계산 후 메모리 캐시를 비운다."""
    global _matrix_cache
    with _lock:
        _matrix_cache = None
        _query_lru.clear()
