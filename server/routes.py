import logging
import os
import re

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, literal, select

from lib.db import SessionLocal
from lib.models import Announcement, Position
from lib.similarity import DEFAULT_SIM_RATIO, search_similar

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# src_url이 깨진 값일 수 있어 정규화한다. 사례는 CODE_GUIDE 4.9
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-.]*\.[a-zA-Z]{2,}")


def normalize_src_url(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return v
    if _DOMAIN_RE.match(v):  # 스킴 없는 도메인
        return "https://" + v
    return None


def jobalio_url(recrut_pblnt_sn: int | None) -> str | None:
    """공고번호로 잡알리오 상세 페이지 URL을 만든다(src_url 없을 때 폴백)."""
    if recrut_pblnt_sn is None:
        return None
    return f"https://job.alio.go.kr/recruitview.do?idx={recrut_pblnt_sn}"


templates.env.filters["clean_url"] = normalize_src_url
templates.env.filters["jobalio_url"] = jobalio_url

PAGE_SIZE = 50

# 학력 사다리: 낮음 → 높음. 판정 규칙은 CODE_GUIDE 6.6
EDU_LADDER = ["무관", "학사", "석사", "박사"]
# (쿼리값, 표시라벨)
EDU_FILTER_OPTIONS = [
    ("무관", "학력무관"),
    ("학사", "대졸(학사)"),
    ("석사", "석사"),
    ("박사", "박사"),
]

# 내 경력 상태 -> 지원 가능한 공고의 career_level 집합. 근거는 CODE_GUIDE 6.6
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
    sem: str | None = Query(None, description="의미 기반 검색어; 유사한 직무를 유사도순으로 표시"),
    precision: float | None = Query(
        None,
        ge=0.5,
        le=1.0,
        description="의미검색 정밀도. 최고 유사도 대비 이 비율 이상만 표시(높을수록 엄격)",
    ),
    tag: str | None = Query(None, description="태그. 띄어쓰기로 여러 개를 넣으면 모두 포함하는(AND) 직무만 표시"),
    career: str | None = Query(None, description="사용자 본인의 경력 상태(신입/경력); 지원 가능한 공고를 모두 표시"),
    edu: str | None = Query(None, description="사용자 본인의 최종학력; 이 학력으로 지원 가능한 공고를 모두 표시"),
    work_rgn: str | None = Query(None),
    apply_by: str | None = Query(None, description="YYYYMMDD, 이 날짜까지 지원 가능한(마감일이 이 날짜 이후인) 공고만 표시"),
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
            # tags는 "a,b,c" 콤마구분 문자열이라, 그냥 LIKE는 "정규직"이 "비정규직"에도
            # 부분일치해버린다. 양끝에 콤마를 덧붙여 ",태그," 단위로 정확히 매칭한다.
            padded_tags = literal(",").concat(Position.tags).concat(",")
            for t in tag.split():
                base = base.where(padded_tags.like(f"%,{t},%"))
        if career and career in CAREER_ELIGIBILITY:
            base = base.where(Position.career_level.in_(CAREER_ELIGIBILITY[career]))
        if edu and edu in EDU_LADDER:
            # 내 학력 이하의 요건 = 지원 가능한 공고 전부
            eligible = EDU_LADDER[: EDU_LADDER.index(edu) + 1]
            base = base.where(Position.min_education.in_(eligible))
        if work_rgn:
            base = base.where(
                (Announcement.work_rgn_nm_lst.like(f"%{work_rgn}%"))
                | (Position.location.like(f"%{work_rgn}%"))
            )
        if apply_by:
            # 마감일이 이 날짜 이후(포함)인 공고만 = 이 날짜까지는 지원 가능한 공고
            base = base.where(Announcement.pbanc_end_ymd >= apply_by)

        precision_eff = precision if precision is not None else DEFAULT_SIM_RATIO
        sim_map: dict[int, float] = {}
        sem_error = False
        sem_q = (sem or "").strip()

        if sem_q:
            # 다른 필터로 후보를 좁힌 뒤 유사도순 정렬. 흐름은 CODE_GUIDE 10.6
            candidates = session.execute(base).scalars().all()
            pos_map = {p.id: p for p in candidates}
            try:
                ranked = search_similar(sem_q, precision_eff, candidate_ids=set(pos_map))
            except Exception as exc:
                logger.warning("의미검색 실패(Ollama/임베딩 모델 확인 필요): %s", exc)
                ranked = []
                sem_error = True

            total_filtered = len(ranked)
            total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(page, total_pages)
            page_slice = ranked[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
            results = [pos_map[pid] for pid, _ in page_slice]
            sim_map = {pid: sim for pid, sim in page_slice}
        else:
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
            p.announcement.attachments  # noqa: B018 (관계 즉시 로딩 트리거, CODE_GUIDE 6.5)

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
            "sim_map": sim_map,
            "sem_error": sem_error,
            "extraction_status": {
                "total": total_announcements,
                "extracted": extracted_announcements,
            },
            "filters": {
                "keyword": keyword or "",
                "sem": sem or "",
                "precision": precision_eff,
                "tag": tag or "",
                "career": career or "",
                "edu": edu or "",
                "work_rgn": work_rgn or "",
                "apply_by": apply_by or "",
                "sort": sort,
            },
        },
    )
