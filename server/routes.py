import os

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from lib.db import SessionLocal
from lib.models import Announcement

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@router.get("/")
def search(
    request: Request,
    keyword: str | None = Query(None),
    recrut_se: str | None = Query(None),
    work_rgn: str | None = Query(None),
    bgn_date: str | None = Query(None, description="YYYYMMDD, 공고 시작일 이후"),
    end_date: str | None = Query(None, description="YYYYMMDD, 공고 시작일 이전"),
    sort: str = Query("latest", pattern="^(latest|deadline)$"),
):
    with SessionLocal() as session:
        stmt = select(Announcement)

        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                (Announcement.recrut_pbanc_ttl.like(like)) | (Announcement.inst_nm.like(like))
            )
        if recrut_se:
            stmt = stmt.where(Announcement.recrut_se_nm == recrut_se)
        if work_rgn:
            stmt = stmt.where(Announcement.work_rgn_nm_lst.like(f"%{work_rgn}%"))
        if bgn_date:
            stmt = stmt.where(Announcement.pbanc_bgng_ymd >= bgn_date)
        if end_date:
            stmt = stmt.where(Announcement.pbanc_bgng_ymd <= end_date)

        if sort == "deadline":
            stmt = stmt.order_by(Announcement.pbanc_end_ymd.asc())
        else:
            stmt = stmt.order_by(Announcement.recrut_pblnt_sn.desc())

        results = session.execute(stmt.limit(200)).scalars().all()

        career_options = [
            row[0]
            for row in session.execute(
                select(Announcement.recrut_se_nm).distinct().where(Announcement.recrut_se_nm.is_not(None))
            ).all()
        ]

        for r in results:
            r.attachments  # noqa: B018 (관계 즉시 로딩 트리거, expire_on_commit=False라 세션 종료 후에도 접근 가능)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "results": results,
            "total": len(results),
            "career_options": sorted(career_options),
            "filters": {
                "keyword": keyword or "",
                "recrut_se": recrut_se or "",
                "work_rgn": work_rgn or "",
                "bgn_date": bgn_date or "",
                "end_date": end_date or "",
                "sort": sort,
            },
        },
    )
