"""직무 추출 CLI(2단계 하이브리드). 자세한 설명은 CODE_GUIDE 12장.

    python extract.py                 # pending만, 2.4b 추출 후 면허 애매건만 7.8b 재검증
    python extract.py --all           # 이미 추출된 공고까지 전부 재추출
    python extract.py --limit N        # N건만(테스트)
    python extract.py --no-review     # 2.4b만, 재검증 생략(가장 빠름)
    python extract.py --open-only     # 마감(pbanc_end_ymd)이 지나지 않은 공고만 대상
    python extract.py --with-attachments --all
                                       # 첨부파일 텍스트가 있는 공고만 재추출(다른 공고는 입력이
                                       # 그대로라 다시 돌려도 결과가 바뀌지 않으므로 제외)
"""

import argparse
import datetime
import logging
import os
import re

from lib.db import SessionLocal, init_db
from lib.extractor import Extractor
from lib.models import Announcement, Attachment, Position, PositionEmbedding

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 1단계는 빠른 2.4b, 재검증은 품질 좋은 7.8b. 둘 다 GPU 스래싱을 막으려 배치로 분리한다.
PRIMARY_MODEL = os.environ.get("EXTRACT_PRIMARY_MODEL", "exaone3.5:2.4b")
REVIEW_MODEL = os.environ.get("EXTRACT_REVIEW_MODEL", "exaone3.5:7.8b")

# 면허/자격이 필요해 보이는데 뽑아내지 못한 공고를 재검증 대상으로 삼는다(CODE_GUIDE 12.2).
_LICENSE_HINT = re.compile(r"면허|전문의|자격증?\s*소지|국가자격|기술사")


def _needs_review(announcement, positions) -> bool:
    if not positions:
        return True
    text = " ".join(
        filter(None, [announcement.recrut_pbanc_ttl, announcement.aply_qlfc_cn, announcement.posting_body])
    )
    has_hint = bool(_LICENSE_HINT.search(text))
    has_license = any((p.get("required_license") or "").strip() for p in positions)
    return has_hint and not has_license


def _dedupe(positions: list[dict]) -> list[dict]:
    """같은 (title, career_level) 중복 제거(CODE_GUIDE 6.4)."""
    seen: set = set()
    out = []
    for p in positions:
        key = (p.get("title"), p.get("career_level"))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _save(session, announcement_id: int, positions: list[dict], model: str):
    """공고 단위 멱등: 기존 직무를 지우고 새로 넣는다(CODE_GUIDE 6.4).

    Position.delete()는 bulk DELETE라 SQLAlchemy의 cascade="delete-orphan"을 타지 않는다.
    그대로 두면 지워진 직무를 가리키던 PositionEmbedding이 고아 행으로 계속 쌓이므로,
    먼저 명시적으로 함께 지운다.
    """
    old_ids = [
        pid
        for (pid,) in session.query(Position.id).filter(
            Position.announcement_id == announcement_id
        )
    ]
    if old_ids:
        session.query(PositionEmbedding).filter(
            PositionEmbedding.position_id.in_(old_ids)
        ).delete(synchronize_session=False)
    session.query(Position).filter(
        Position.announcement_id == announcement_id
    ).delete(synchronize_session=False)
    for p in positions:
        session.add(
            Position(
                announcement_id=announcement_id,
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
                extraction_model=model,
            )
        )
    session.commit()


def _extract_one(extractor, announcement):
    try:
        return _dedupe(extractor.extract(announcement))
    except Exception as exc:
        logger.warning("추출 실패 id=%s: %s", announcement.recrut_pblnt_sn, exc)
        return None


def extract_pending(
    reextract_all: bool = False,
    limit: int | None = None,
    review: bool = True,
    open_only: bool = False,
    with_attachments: bool = False,
):
    init_db()

    with SessionLocal() as session:
        if reextract_all:
            q = session.query(Announcement.recrut_pblnt_sn)
        else:
            done = {r[0] for r in session.query(Position.announcement_id).distinct()}
            q = session.query(Announcement.recrut_pblnt_sn)
            if done:
                q = q.filter(~Announcement.recrut_pblnt_sn.in_(done))
        if open_only:
            # 마감일(YYYYMMDD) 문자열 비교. 마감일이 없는 공고는 상시채용으로 보고 포함한다.
            today = datetime.date.today().strftime("%Y%m%d")
            q = q.filter(
                (Announcement.pbanc_end_ymd >= today) | (Announcement.pbanc_end_ymd.is_(None))
            )
        if with_attachments:
            # 첨부파일에서 텍스트가 실제로 뽑힌 공고만. 나머지는 이전과 입력이 같아 재추출해도
            # 결과가 바뀌지 않으므로(모델 확률적 편차 제외) 제외한다.
            has_text = {
                r[0]
                for r in session.query(Attachment.announcement_id)
                .filter(Attachment.extracted_text.isnot(None))
                .distinct()
            }
            q = q.filter(Announcement.recrut_pblnt_sn.in_(has_text))
        target_ids = [r[0] for r in q.all()]
    if limit:
        target_ids = target_ids[:limit]

    # 1단계: 빠른 모델로 전량 추출. 면허 애매건은 flagged로 모은다.
    logger.info("1단계 추출 %d건 (모델=%s)", len(target_ids), PRIMARY_MODEL)
    primary = Extractor(model=PRIMARY_MODEL)
    flagged: list[int] = []
    for i, aid in enumerate(target_ids, 1):
        with SessionLocal() as session:
            a = session.get(Announcement, aid)
            positions = _extract_one(primary, a)
            if positions is None:
                continue
            _save(session, aid, positions, PRIMARY_MODEL)
            if review and _needs_review(a, positions):
                flagged.append(aid)
        if i % 20 == 0:
            logger.info("1단계 진행 %d/%d (재검증 대기 %d)", i, len(target_ids), len(flagged))

    # 2단계: 재검증 대상만 품질 모델로 다시 추출(덮어씀).
    if review and flagged:
        logger.info("2단계 재검증 %d건 (모델=%s)", len(flagged), REVIEW_MODEL)
        reviewer = Extractor(model=REVIEW_MODEL)
        for aid in flagged:
            with SessionLocal() as session:
                a = session.get(Announcement, aid)
                positions = _extract_one(reviewer, a)
                if positions is not None:
                    _save(session, aid, positions, REVIEW_MODEL)

    logger.info("완료: 1단계 %d건, 2단계 재검증 %d건", len(target_ids), len(flagged) if review else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="직무 추출(2단계 하이브리드)")
    parser.add_argument("--all", action="store_true", help="이미 추출된 공고까지 전부 재추출")
    parser.add_argument("--limit", type=int, default=None, help="N건만 처리(테스트용)")
    parser.add_argument("--no-review", action="store_true", help="2.4b만, 7.8b 재검증 생략")
    parser.add_argument("--open-only", action="store_true", help="마감이 지나지 않은 공고만 대상")
    parser.add_argument(
        "--with-attachments", action="store_true", help="첨부파일에서 텍스트가 뽑힌 공고만 대상"
    )
    args = parser.parse_args()
    extract_pending(
        reextract_all=args.all,
        limit=args.limit,
        review=not args.no_review,
        open_only=args.open_only,
        with_attachments=args.with_attachments,
    )
