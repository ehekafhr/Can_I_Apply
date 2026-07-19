"""직무(Position) 벡터 사전계산 CLI.

각 직무의 텍스트(제목+태그+설명)를 임베딩해 position_embeddings에 저장한다.
검색(유사도) 서비스는 이 표를 메모리에 올려 코사인 유사도를 계산한다.

사용법
------
    python embed.py            # 아직 임베딩이 없거나 원문이 바뀐 직무만 (증분)
    python embed.py --all      # 전부 다시 계산 (모델을 바꿨을 때)

모델은 EMBED_MODEL 환경변수로 바꾼다(기본 bge-m3). Ollama 서버가 떠 있고
해당 모델이 pull 되어 있어야 한다.
"""

import argparse
import hashlib
import logging

import numpy as np

from lib.db import SessionLocal, init_db
from lib.embedder import EMBED_MODEL, Embedder
from lib.models import Position, PositionEmbedding
from lib.similarity import build_position_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_UPSERT_BATCH = 64  # 한 번에 임베딩·저장하는 직무 수


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_positions(reembed_all: bool = False):
    init_db()
    embedder = Embedder()
    model = EMBED_MODEL

    with SessionLocal() as session:
        positions = session.query(Position).all()
        # 기존 임베딩 (position_id -> (model, text_hash))
        existing = {
            e.position_id: (e.model, e.text_hash)
            for e in session.query(
                PositionEmbedding.position_id,
                PositionEmbedding.model,
                PositionEmbedding.text_hash,
            ).all()
        }

    # 임베딩이 필요한 직무만 추린다: 없음 / 모델 다름 / 원문 바뀜 / --all
    pending: list[tuple[int, str, str]] = []  # (position_id, text, text_hash)
    for p in positions:
        text = build_position_text(p)
        if not text:
            continue
        h = _text_hash(text)
        prev = existing.get(p.id)
        if reembed_all or prev is None or prev[0] != model or prev[1] != h:
            pending.append((p.id, text, h))

    logger.info(
        "직무 %d개 중 임베딩 대상 %d개 (모델=%s)", len(positions), len(pending), model
    )
    if not pending:
        logger.info("최신 상태입니다. 할 일 없음.")
        return

    done = 0
    for start in range(0, len(pending), _UPSERT_BATCH):
        chunk = pending[start : start + _UPSERT_BATCH]
        texts = [t for _, t, _ in chunk]
        try:
            vectors = embedder.embed(texts)
        except Exception as exc:
            logger.warning("임베딩 실패 [%d~%d]: %s", start, start + len(chunk), exc)
            continue

        dim = int(vectors.shape[1])
        with SessionLocal() as session:
            for (pid, _text, h), vec in zip(chunk, vectors):
                # upsert: 있으면 갱신, 없으면 삽입
                row = session.get(PositionEmbedding, pid)
                blob = vec.astype(np.float32).tobytes()
                if row is None:
                    session.add(
                        PositionEmbedding(
                            position_id=pid,
                            model=model,
                            dim=dim,
                            vector=blob,
                            text_hash=h,
                        )
                    )
                else:
                    row.model = model
                    row.dim = dim
                    row.vector = blob
                    row.text_hash = h
            session.commit()

        done += len(chunk)
        logger.info("임베딩 진행 %d/%d", done, len(pending))

    logger.info("완료: %d개 직무 임베딩 저장(dim=%d, 모델=%s)", done, dim, model)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="직무 벡터 사전계산")
    parser.add_argument(
        "--all",
        action="store_true",
        help="전부 다시 임베딩(모델 변경 시). 기본은 증분.",
    )
    args = parser.parse_args()
    embed_positions(reembed_all=args.all)
