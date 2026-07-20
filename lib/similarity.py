"""직무 벡터 코사인 유사도 검색. 자세한 설명은 CODE_GUIDE 10장."""

import re
import threading
from collections import OrderedDict
from difflib import SequenceMatcher

import numpy as np

from lib.db import SessionLocal
from lib.embedder import EMBED_MODEL, Embedder
from lib.models import Position, PositionEmbedding, QueryCache

_QUERY_LRU_MAX = 256  # 검색어 임베딩 인메모리 캐시 최대 개수
# 컷오프 = max(SIM_FLOOR, 최고점 × ratio). 절대 임계값이 아닌 이유는 CODE_GUIDE 10.7.
DEFAULT_SIM_RATIO = 0.90
SIM_FLOOR = 0.42  # 실측으로 잡은 절대 바닥. 모델/데이터가 바뀌면 재측정(10.7)

_lock = threading.Lock()
_matrix_cache: dict | None = None  # {"model","ids","matrix"(정규화 N×dim),"count"}
_query_lru: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()
_embedder: Embedder | None = None


# 직무 내용을 설명하는 필드만 넣는다. 기관·지역·학력 등을 뺀 이유는 CODE_GUIDE 10.3.
_COMMON_DOC_FIELDS = [
    ("공고제목", "recrut_pbanc_ttl"),
    ("직무분야", "ncs_cd_nm_lst"),
    ("우대사항", "pref_cn"),
    ("우대조건", "pref_cond_cn"),
]
# "없음" 류 잡음값은 임베딩 텍스트에서 제외
_NOISE_VALUES = {"없음", "해당없음", "해당 없음", "-", ".", "무", "N/A"}
_MAX_TEXT_CHARS = 4000  # 임베딩 텍스트 상한
_ITEM_MATCH_MIN = 0.6  # 자격요건 항목 매칭 최소 확신도. 미만이면 전체 폴백


def _norm(text: str | None) -> str:
    """공백을 제거한 비교용 문자열."""
    return re.sub(r"\s+", "", text or "")


def _split_items(text: str) -> list[str]:
    """자격요건 원문을 항목 단위로 분리."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        return lines
    parts = re.split(r"(?=(?:\d{1,2}\s*[.)]|[○●▪·◦□■※]))", text)
    return [p.strip() for p in parts if p.strip()]


def _item_label(item: str) -> str:
    """항목의 라벨(번호/불릿 제거 후 콜론 앞)을 반환."""
    body = re.sub(r"^\s*(?:\d{1,2}\s*[.)]|[○●▪·◦□■※\-])\s*", "", item)
    return (body.split(":", 1)[0] if ":" in body else body).strip()


def _item_score(title_n: str, evidence_n: str, item: str) -> float:
    """항목이 이 직무의 것일 가능성 점수. 채점 방식은 CODE_GUIDE 10.3."""
    label_n = _norm(_item_label(item))
    if not label_n or not title_n:
        return 0.0

    if title_n == label_n:
        score = 1.0
    elif title_n in label_n or label_n in title_n:
        score = 0.9
    else:
        score = SequenceMatcher(None, title_n, label_n).ratio()

    if evidence_n and evidence_n in _norm(item):
        score += 0.15
    return score


def _position_qualification(p: Position) -> tuple[str, bool]:
    """이 직무의 자격요건 구간을 발췌. 실패 시 전체 폴백. → (텍스트, 발췌성공)"""
    qual = (p.announcement.aply_qlfc_cn or "").strip()
    if not qual:
        return "", False

    items = _split_items(qual)
    if len(items) <= 1:
        return qual, False  # 쪼갤 수 없으면 전체가 곧 해당 구간

    title_n = _norm(p.title)
    evidence_n = _norm(p.evidence)
    best, best_score = None, 0.0
    for item in items:
        s = _item_score(title_n, evidence_n, item)
        if s > best_score:
            best, best_score = item, s

    if best is not None and best_score >= _ITEM_MATCH_MIN:
        return best, True
    return qual, False


def build_position_text(p: Position, sibling_count: int | None = None) -> str:
    """임베딩할 직무 텍스트를 공고 원문으로 구성. 구성 근거는 CODE_GUIDE 10.3."""
    a = p.announcement
    if sibling_count is None:
        sibling_count = len(a.positions)

    lines = [f"직무명: {p.title}"]
    if p.tags:
        lines.append(f"태그: {p.tags}")

    # 직무가 하나뿐이면 자격요건 전체가 곧 이 직무의 것
    if sibling_count > 1:
        qual, _matched = _position_qualification(p)
    else:
        qual = (a.aply_qlfc_cn or "").strip()
    if qual and qual not in _NOISE_VALUES:
        lines.append(f"자격요건: {qual}")

    if p.evidence:
        lines.append(f"근거: {p.evidence}")

    for label, field in _COMMON_DOC_FIELDS:
        value = (getattr(a, field, None) or "").strip()
        if value and value not in _NOISE_VALUES:
            lines.append(f"{label}: {value}")

    return "\n".join(lines).strip()[:_MAX_TEXT_CHARS]


def _get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def _normalize(mat: np.ndarray) -> np.ndarray:
    """행 단위 L2 정규화(0벡터는 그대로)."""
    norms = np.linalg.norm(mat, axis=-1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return mat / norms


def _load_matrix(model: str) -> dict:
    """직무 벡터를 메모리 행렬로 로드. 모델·건수가 같으면 캐시 재사용."""
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
    """검색어의 정규화 임베딩. 메모리 LRU → DB → 임베딩 순(CODE_GUIDE 10.5)."""
    query = query.strip()
    key = (query, model)

    with _lock:
        vec = _query_lru.get(key)
        if vec is not None:
            _query_lru.move_to_end(key)
            return vec

    vec = _query_from_db(query, model)
    if vec is None:
        raw = _get_embedder().embed_one(query)
        vec = _normalize(raw.reshape(1, -1))[0]
        _query_to_db(query, model, vec)

    with _lock:
        _query_lru[key] = vec
        _query_lru.move_to_end(key)
        while len(_query_lru) > _QUERY_LRU_MAX:
            _query_lru.popitem(last=False)
    return vec


def search_similar(
    query: str,
    ratio: float = DEFAULT_SIM_RATIO,
    model: str = EMBED_MODEL,
    candidate_ids: set[int] | None = None,
) -> list[tuple[int, float]]:
    """유사한 직무를 (position_id, 유사도) 내림차순 반환.

    candidate_ids를 주면 그 안에서만 계산한다. 컷오프는 CODE_GUIDE 10.7.
    """
    query = query.strip()
    if not query:
        return []

    cache = _load_matrix(model)
    if cache["count"] == 0:
        return []

    qv = get_query_vector(query, model)
    sims = cache["matrix"] @ qv  # 정규화돼 있어 내적 = 코사인 유사도
    ids = cache["ids"]

    if candidate_ids is not None:
        mask = np.fromiter(
            (int(i) in candidate_ids for i in ids), dtype=bool, count=len(ids)
        )
        ids, sims = ids[mask], sims[mask]
    if sims.size == 0:
        return []

    cutoff = max(SIM_FLOOR, float(sims.max()) * ratio)
    keep = sims >= cutoff
    hit_ids, hit_sims = ids[keep], sims[keep]

    order = np.argsort(-hit_sims)
    return [(int(hit_ids[i]), float(hit_sims[i])) for i in order]


def reset_cache() -> None:
    """메모리 캐시 초기화."""
    global _matrix_cache
    with _lock:
        _matrix_cache = None
        _query_lru.clear()
