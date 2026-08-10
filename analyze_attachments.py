"""첨부파일(PDF/HWP/HWPX/이미지) 본문 추출 CLI.

    python analyze_attachments.py            # 미분석 첨부파일만 (증분)
    python analyze_attachments.py --all      # 전부 다시 분석
    python analyze_attachments.py --limit N  # N건만 (테스트용)
"""

import argparse
import datetime
import logging

from lib.attachment_extractor import extract_attachment
from lib.db import SessionLocal, init_db
from lib.models import Attachment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def analyze_pending(reanalyze_all: bool = False, limit: int | None = None):
    init_db()
    with SessionLocal() as session:
        q = session.query(Attachment.id).filter(Attachment.crawl_status == "downloaded")
        if not reanalyze_all:
            q = q.filter(Attachment.extracted_text.is_(None))
        ids = [r[0] for r in q.all()]
    if limit:
        ids = ids[:limit]

    logger.info("첨부파일 분석 대상 %d건", len(ids))
    ok = empty = fail = 0
    for i, aid in enumerate(ids, 1):
        with SessionLocal() as session:
            att = session.get(Attachment, aid)
            if att is None or not att.local_path:
                continue
            try:
                text, method = extract_attachment(att.local_path, att.file_ext)
            except Exception as exc:
                logger.warning("분석 실패 (id=%s, %s): %s", aid, att.file_name, exc)
                fail += 1
                continue
            att.extracted_text = text
            att.extraction_method = method
            att.analyzed_at = datetime.datetime.utcnow()
            session.commit()
            ok += 1 if text else 0
            empty += 0 if text else 1
        if i % 10 == 0:
            logger.info("진행 %d/%d", i, len(ids))

    logger.info("완료: 추출됨 %d / 빈 결과 %d / 실패 %d", ok, empty, fail)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="첨부파일 본문 추출(PDF/HWP/이미지)")
    parser.add_argument("--all", action="store_true", help="전부 다시 분석. 기본은 증분")
    parser.add_argument("--limit", type=int, default=None, help="N건만 처리(테스트용)")
    args = parser.parse_args()
    analyze_pending(reanalyze_all=args.all, limit=args.limit)
