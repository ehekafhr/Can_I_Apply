"""직무 벡터 사전계산 CLI. 자세한 설명은 CODE_GUIDE 10장.

    python embed.py          # 증분(신규/변경된 직무만)
    python embed.py --all    # 전부 재계산(모델 변경 시)
"""

import argparse
import hashlib
import logging

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from lib.db import SessionLocal, init_db
from lib.embedder import EMBED_MODEL, Embedder
from lib.models import Position, PositionEmbedding
from lib.similarity import build_position_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_UPSERT_BATCH = 64


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_positions(reembed_all: bool = False):
    init_db()
    embedder = Embedder()
    model = EMBED_MODEL

    # 공고를 함께 로드(N+1 방지) + 형제 직무 수를 한 번에 집계
    with SessionLocal() as session:
        positions = (
            session.query(Position).options(joinedload(Position.announcement)).all()
        )
        sibling_counts = dict(
            session.query(Position.announcement_id, func.count(Position.id))
            .group_by(Position.announcement_id)
            .all()
        )
        # 기존 임베딩 (position_id -> (model, text_hash))
        existing = {
            e.position_id: (e.model, e.text_hash)
            for e in session.query(
                PositionEmbedding.position_id,
                PositionEmbedding.model,
                PositionEmbedding.text_hash,
            ).all()
        }

        # 없음 / 모델 다름 / 원문 바뀜 / --all 인 것만
        pending: list[tuple[int, str, str]] = []  # (position_id, text, text_hash)
        for p in positions:
            text = build_position_text(p, sibling_counts.get(p.announcement_id, 1))
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
        help="전부 다시 임베딩(모델 변경 시). 기본은 증분",
    )
    args = parser.parse_args()
    embed_positions(reembed_all=args.all)
