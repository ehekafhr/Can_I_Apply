import argparse
import logging
import time

from lib.attachment_crawler import crawl_announcement
from lib.collector import backfill_ongoing, collect_new
from lib.db import SessionLocal
from lib.models import Announcement

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CRAWL_DELAY_SECONDS = 1.0


def crawl_attachments_for(ids: list[int]):
    if not ids:
        return
    with SessionLocal() as session:
        for i, ann_id in enumerate(ids):
            announcement = session.get(Announcement, ann_id)
            if announcement is None:
                continue
            status = crawl_announcement(session, announcement)
            logger.info("첨부파일 크롤링 [%d/%d] id=%s -> %s", i + 1, len(ids), ann_id, status)
            if i < len(ids) - 1:
                time.sleep(CRAWL_DELAY_SECONDS)


def main():
    parser = argparse.ArgumentParser(description="잡알리오 채용정보 수집기")
    parser.add_argument(
        "--backfill",
        action="store_true", #기본값 false
        help="최초 적재: 현재 접수중(ongoingYn=Y)인 공고 전체를 가져온다",
    )
    parser.add_argument(
        "--no-attachments",
        action="store_true", #기본값 false
        help="첨부파일 크롤링을 건너뛰고 공고 메타데이터만 수집한다",
    )
    args = parser.parse_args()

    if args.backfill:
        new_ids = backfill_ongoing() # 전체
    else:
        new_ids = collect_new() # 최신순으로 순회하다 이미 DB에 있는 공고를 만나면 중단

    logger.info("신규 공고 %d건 수집 완료", len(new_ids))

    if not args.no_attachments:
        crawl_attachments_for(new_ids)


if __name__ == "__main__":
    main()
