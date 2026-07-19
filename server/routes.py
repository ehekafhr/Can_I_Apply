import os
import re

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from lib.db import SessionLocal
from lib.models import Announcement, Position

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# src_url에 실제 URL 대신 '없음.', '.', ',' 같은 값이나 스킴 없는 도메인이 들어오는 경우가 있어,
# 링크로 렌더링하기 전에 정규화한다. 유효하지 않으면 None을 반환해 링크를 만들지 않는다.
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-.]*\.[a-zA-Z]{2,}")


def normalize_src_url(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return v
    if _DOMAIN_RE.match(v):  # 스킴 없는 도메인(www.example.com 등)
        return "https://" + v
    return None


templates.env.filters["clean_url"] = normalize_src_url

PAGE_SIZE = 50

# 학력 사다리: 낮음 → 높음. 공고의 min_education 요건이 사용자 학력보다 낮거나 같으면 지원 가능.
# (고졸/초대졸은 별도 구분 없이 '무관'으로 취급한다.)
EDU_LADDER = ["무관", "학사", "석사", "박사"]
# 드롭다운(사용자 본인의 최종학력) — (쿼리값, 표시라벨)
EDU_FILTER_OPTIONS = [
    ("무관", "학력무관"),
    ("학사", "대졸(학사)"),
    ("석사", "석사"),
    ("박사", "박사"),
]

# 경력: 사용자 본인의 경력 상태 -> 지원 가능한 공고의 career_level 집합.
# '무관'/'신입+경력'은 누구에게나 열려 있고, 신입은 경력전용을, 경력자는 신입전용을 지원 불가.
CAREER_ELIGIBILITY = {
    "신입": ["무관", "신입", "신입+경력"],
    "경력": ["무관", "경력", "신입+경력"],
}
CAREER_FILTER_OPTIONS = [
    ("신입", "신입 (경력 없음)"),
    ("경력", "경력자"),
]


@router.get("/")
def search(
    request: Request,
    keyword: str | None = Query(None),
    tag: str | None = Query(None),
    career: str | None = Query(None, description="사용자 본인의 경력 상태(신입/경력); 지원 가능한 공고를 모두 표시"),
    edu: str | None = Query(None, description="사용자 본인의 최종학력; 이 학력으로 지원 가능한 공고를 모두 표시"),
    work_rgn: str | None = Query(None),
    bgn_date: str | None = Query(None, description="YYYYMMDD, 공고 시작일 이후"),
    end_date: str | None = Query(None, description="YYYYMMDD, 공고 시작일 이전"),
    sort: str = Query("latest", pattern="^(latest|deadline)$"),
    page: int = Query(1, ge=1),
):
    with SessionLocal() as session:
        base = select(Position).join(Announcement, Position.announcement_id == Announcement.recrut_pblnt_sn)

        if keyword:
            like = f"%{keyword}%"
            base = base.where(
                (Position.title.like(like))
                | (Announcement.recrut_pbanc_ttl.like(like))
                | (Announcement.inst_nm.like(like))
            )
        if tag:
            base = base.where(Position.tags.like(f"%{tag}%"))
        if career and career in CAREER_ELIGIBILITY:
            base = base.where(Position.career_level.in_(CAREER_ELIGIBILITY[career]))
        if edu and edu in EDU_LADDER:
            # 내 학력(edu) 이하의 요건을 가진 공고 = 지원 가능한 공고 전부
            eligible = EDU_LADDER[: EDU_LADDER.index(edu) + 1]
            base = base.where(Position.min_education.in_(eligible))
        if work_rgn:
            base = base.where(
                (Announcement.work_rgn_nm_lst.like(f"%{work_rgn}%"))
                | (Position.location.like(f"%{work_rgn}%"))
            )
        if bgn_date:
            base = base.where(Announcement.pbanc_bgng_ymd >= bgn_date)
        if end_date:
            base = base.where(Announcement.pbanc_bgng_ymd <= end_date)

        total_filtered = session.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)

        if sort == "deadline":
            stmt = base.order_by(Announcement.pbanc_end_ymd.asc(), Position.id.asc())
        else:
            stmt = base.order_by(Announcement.recrut_pblnt_sn.desc(), Position.id.asc())
        stmt = stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)

        results = session.execute(stmt).scalars().all()

        for p in results:
            p.announcement.attachments  # noqa: B018 (관계 즉시 로딩 트리거, expire_on_commit=False라 세션 종료 후에도 접근 가능)

        total_announcements = session.query(Announcement).count()
        extracted_announcements = session.query(Position.announcement_id).distinct().count()

    range_start = 0 if total_filtered == 0 else (page - 1) * PAGE_SIZE + 1
    range_end = min(page * PAGE_SIZE, total_filtered)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "results": results,
            "total": total_filtered,
            "range_start": range_start,
            "range_end": range_end,
            "page": page,
            "total_pages": total_pages,
            "career_options": CAREER_FILTER_OPTIONS,
            "education_options": EDU_FILTER_OPTIONS,
            "extraction_status": {
                "total": total_announcements,
                "extracted": extracted_announcements,
            },
            "filters": {
                "keyword": keyword or "",
                "tag": tag or "",
                "career": career or "",
                "edu": edu or "",
                "work_rgn": work_rgn or "",
                "bgn_date": bgn_date or "",
                "end_date": end_date or "",
                "sort": sort,
            },
        },
    )
