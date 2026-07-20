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

import re
import threading
from collections import OrderedDict
from difflib import SequenceMatcher

import numpy as np

from lib.db import SessionLocal
from lib.embedder import EMBED_MODEL, Embedder
from lib.models import Position, PositionEmbedding, QueryCache

# 검색어 임베딩 인메모리 캐시 최대 개수
_QUERY_LRU_MAX = 256

# 컷오프는 "최고점 대비 비율"로 잡는다(절대 임계값 아님).
#
# 검색어마다 유사도 분포가 크게 다르기 때문이다. "간호"·"사무직"처럼 흔한
# 직무어는 모든 채용공고와 어느 정도 비슷해 전반적으로 높게 나오고,
# "인공지능"처럼 이 데이터에 드문 말은 최고점부터 낮다. 그래서 고정
# 임계값 하나(예: 0.45)로는 한쪽이 반드시 망가진다 —
# 실측에서 0.45는 "간호" 232건(의료 전반이 딸려옴) / "인공지능" 1건이었다.
#
# 최고점 대비 비율로 자르면 분포에 자동으로 맞춰진다. 0.90에서 "간호"는
# 22건 전부가 간호 직무(비간호 혼입 0건), "인공지능"은 실제 AI 직무만 남았다.
DEFAULT_SIM_RATIO = 0.90
# 절대 바닥. 최고점 자체가 낮으면(=아무것도 안 맞으면) 비율 컷오프는 그
# 낮은 분포의 위쪽을 그대로 통과시키므로, 이 밑은 무조건 버린다.
#
# 0.42는 실측으로 잡은 값이라 모델·데이터가 바뀌면 다시 재야 한다.
# 아래가 그 경계다(bge-m3, 직무 910건 기준):
#   없는 직무 "우주비행사" 최고 0.373 · 무의미 입력 "ㅁㄴㅇㄹ" 최고 0.415  → 버려야 함
#   실제로 있는 드문 직무 "인공지능" 1·2위 0.460 / 0.430                → 살려야 함
# 더 낮추면 없는 직무를 검색해도 20건씩 나온다.
SIM_FLOOR = 0.42

_lock = threading.Lock()
# 직무 벡터 행렬 캐시: {"model", "ids"(np.int64), "matrix"(정규화 N×dim), "count"}
_matrix_cache: dict | None = None
# 검색어 → 정규화 벡터. 키는 (query, model)
_query_lru: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()
_embedder: Embedder | None = None


# 공고 원문 중 "직무의 내용"을 설명하는 필드들만 임베딩에 넣는다.
# 기관명·고용형태·근무지역·학력조건은 일부러 뺐다. 직무의 의미와 무관한데
# 모든 공고에 비슷하게 깔려서 유사도 바닥을 끌어올리고(무관한 공고끼리도
# 0.3 이상이 됨) 임계값 분리력을 망친다. 그 조건들은 이미 SQL 필터로 거른다.
# 자격요건(aply_qlfc_cn)만은 직무별 내용이 섞여 있어 아래에서 따로 발췌한다.
_COMMON_DOC_FIELDS = [
    ("공고제목", "recrut_pbanc_ttl"),
    ("직무분야", "ncs_cd_nm_lst"),
    ("우대사항", "pref_cn"),
    ("우대조건", "pref_cond_cn"),
]
# "없음" 류는 의미 없는 잡음이라 임베딩 텍스트에서 뺀다.
_NOISE_VALUES = {"없음", "해당없음", "해당 없음", "-", ".", "무", "N/A"}
# 임베딩 텍스트 상한(bge-m3는 8192토큰까지 받지만 불필요하게 길릴 필요는 없다)
_MAX_TEXT_CHARS = 4000
# 자격요건 항목 매칭 최소 확신도. 이 밑이면 발췌를 포기하고 전체로 폴백한다.
_ITEM_MATCH_MIN = 0.6


def _norm(text: str | None) -> str:
    """공백을 모두 제거한 비교용 문자열(LLM이 인용하며 공백을 다듬는 걸 흡수)."""
    return re.sub(r"\s+", "", text or "")


def _split_items(text: str) -> list[str]:
    """자격요건 원문을 직무별 항목으로 쪼갠다. 줄바꿈 우선, 없으면 번호/불릿 마커."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        return lines
    parts = re.split(r"(?=(?:\d{1,2}\s*[.)]|[○●▪·◦□■※]))", text)
    return [p.strip() for p in parts if p.strip()]


def _item_label(item: str) -> str:
    """항목에서 직무명에 해당하는 라벨을 뽑는다: "2. 소아청소년과(...): 자격..." → "소아청소년과(...)"."""
    body = re.sub(r"^\s*(?:\d{1,2}\s*[.)]|[○●▪·◦□■※\-])\s*", "", item)
    return (body.split(":", 1)[0] if ":" in body else body).strip()


def _item_score(title_n: str, evidence_n: str, item: str) -> float:
    """항목이 이 직무의 것일 가능성 점수.

    항목 라벨(콜론 앞)과 직무명의 유사도가 1차 신호다. 단순 부분일치만 쓰면
    "소아청소년과"가 다른 직무의 자격요건 문장에도 들어 있어 엉뚱한 항목에
    걸리므로, 라벨 대 직무명을 통째로 비교한다.
    """
    label_n = _norm(_item_label(item))
    if not label_n or not title_n:
        return 0.0

    if title_n == label_n:
        score = 1.0
    elif title_n in label_n or label_n in title_n:
        score = 0.9
    else:
        score = SequenceMatcher(None, title_n, label_n).ratio()

    # 원문 인용(evidence)이 그 항목 안에 있으면 확신을 더한다.
    if evidence_n and evidence_n in _norm(item):
        score += 0.15
    return score


def _position_qualification(p: Position) -> tuple[str, bool]:
    """이 직무에 해당하는 자격요건 구간을 원문에서 발췌한다.

    항목별 점수를 매겨 가장 잘 맞는 하나를 고르고, 확신이 낮으면(임계값 미만)
    자격요건 전체로 폴백한다. 반환값의 2번째는 발췌 성공 여부.
    """
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
    """임베딩할 직무 텍스트를 공고 원문으로 구성한다.

    AI 요약(description/requirements) 대신 원문을 쓴다. 한 공고에 직무가
    여러 개면 자격요건에서 이 직무에 해당하는 구간만 발췌해 형제 직무의
    내용이 섞이지 않게 하고, 발췌에 실패하면 자격요건 전체로 폴백한다.
    evidence는 LLM이 원문에서 그대로 인용한 구절이라 함께 넣는다.
    """
    a = p.announcement
    if sibling_count is None:
        sibling_count = len(a.positions)

    lines = [f"직무명: {p.title}"]
    if p.tags:
        lines.append(f"태그: {p.tags}")

    # 직무가 하나뿐이면 자격요건 전체가 곧 이 직무의 것이다.
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
    ratio: float = DEFAULT_SIM_RATIO,
    model: str = EMBED_MODEL,
    candidate_ids: set[int] | None = None,
) -> list[tuple[int, float]]:
    """검색어와 유사한 직무를 (position_id, 유사도) 리스트로, 유사도 내림차순 반환.

    컷오프 = max(SIM_FLOOR, 후보 중 최고점 × ratio). ratio가 높을수록 최고점에
    가까운 것만 남아 정밀해진다. candidate_ids가 주어지면 그 집합 안에서만
    계산하며, 최고점도 그 안에서 잡는다(필터를 건 뒤에도 결과가 남도록).
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

    order = np.argsort(-hit_sims)  # 유사도 내림차순
    return [(int(hit_ids[i]), float(hit_sims[i])) for i in order]


def reset_cache() -> None:
    """테스트/재계산 후 메모리 캐시를 비운다."""
    global _matrix_cache
    with _lock:
        _matrix_cache = None
        _query_lru.clear()
