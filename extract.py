import logging

from lib.db import SessionLocal, init_db
from lib.extractor import MODEL, Extractor
from lib.models import Announcement, Position

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def extract_pending():
    """positions가 아직 없는 공고를 찾아 AI로 직무 단위 분해 후 저장한다."""
    init_db()
    extractor = Extractor()

    with SessionLocal() as session:
        already_done = {row[0] for row in session.query(Position.announcement_id).distinct()}
        pending = (
            session.query(Announcement)
            .filter(~Announcement.recrut_pblnt_sn.in_(already_done))
            .all()
            if already_done
            else session.query(Announcement).all()
        )

        logger.info("추출 대상 %d건", len(pending))
        for i, announcement in enumerate(pending):
            try:
                positions = extractor.extract(announcement)
            except Exception as exc:
                logger.warning(
                    "추출 실패 [%d/%d] id=%s: %s",
                    i + 1,
                    len(pending),
                    announcement.recrut_pblnt_sn,
                    exc,
                )
                continue

            # EXAONE이 한 응답에서 같은 직무를 중복 반환하는 경우가 있어 제거한다.
            seen: set = set()
            deduped = []
            for p in positions:
                key = (p.get("title"), p.get("career_level"))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(p)
            positions = deduped

            # 공고 단위 멱등: 이 공고의 기존 직무를 먼저 지우고 새로 넣는다.
            # 추출이 중간에 끊기거나 두 번 실행돼도 같은 공고가 중복 삽입되지 않는다.
            session.query(Position).filter(
                Position.announcement_id == announcement.recrut_pblnt_sn
            ).delete(synchronize_session=False)
            for p in positions:
                session.add(
                    Position(
                        announcement_id=announcement.recrut_pblnt_sn,
                        title=p["title"],
                        career_level=p["career_level"],
                        min_education=p.get("min_education"),
                        required_license=p.get("required_license") or None,
                        tags=",".join(p.get("tags") or []),
                        description=p.get("description"),
                        headcount=p.get("headcount"),
                        location=p.get("location"),
                        requirements=p.get("requirements"),
                        evidence=p.get("evidence"),
                        confidence=p.get("confidence"),
                        extraction_model=MODEL,
                    )
                )
            session.commit()
            logger.info(
                "추출 완료 [%d/%d] id=%s -> 직무 %d개",
                i + 1,
                len(pending),
                announcement.recrut_pblnt_sn,
                len(positions),
            )


if __name__ == "__main__":
    extract_pending()
