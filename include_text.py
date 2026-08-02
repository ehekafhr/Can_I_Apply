"""공고 본문(잡알리오 공고 내 영역) 수집 CLI. 자세한 설명은 CODE_GUIDE 11장.

    python include_text.py            # posting_body 없는 공고만 (증분)
    python include_text.py --all      # 전부 다시 크롤링
    python include_text.py --limit N  # N건만 (테스트용)
"""

import argparse
import logging
import time

import httpx

from lib.db import SessionLocal, init_db
from lib.models import Announcement
from lib.posting_crawler import fetch_posting_body

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SLEEP_SEC = 0.3  # 잡알리오 서버 예의상 요청 간 간격


def include_text(refetch_all: bool = False, limit: int | None = None):
    init_db()
    with SessionLocal() as session:
        q = session.query(Announcement.recrut_pblnt_sn)
        if not refetch_all:
            q = q.filter(Announcement.posting_body.is_(None))
        sns = [row[0] for row in q.all()]
    if limit:
        sns = sns[:limit]

    logger.info("공고 본문 수집 대상 %d건", len(sns))
    ok = empty = fail = 0
    with httpx.Client() as client:
        for i, sn in enumerate(sns, 1):
            try:
                body = fetch_posting_body(sn, client)
            except Exception as exc:
                logger.info("본문 수집 실패 (id=%s): %s", sn, exc)
                fail += 1
                continue

            with SessionLocal() as session:
                a = session.get(Announcement, sn)
                a.posting_body = body
                session.commit()
            ok += 1 if body else 0
            empty += 0 if body else 1
            if i % 20 == 0:
                logger.info("진행 %d/%d", i, len(sns))
            time.sleep(_SLEEP_SEC)

    logger.info("완료: 본문있음 %d / 본문없음 %d / 실패 %d", ok, empty, fail)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="공고 본문(잡알리오) 수집")
    parser.add_argument("--all", action="store_true", help="전부 다시 크롤링. 기본은 증분")
    parser.add_argument("--limit", type=int, default=None, help="N건만 처리(테스트용)")
    args = parser.parse_args()
    include_text(refetch_all=args.all, limit=args.limit)
