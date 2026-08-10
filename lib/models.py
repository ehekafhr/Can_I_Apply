import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lib.db import Base


class Announcement(Base):
    __tablename__ = "announcements"

    recrut_pblnt_sn: Mapped[int] = mapped_column(Integer, primary_key=True)

    pblnt_inst_cd: Mapped[str | None] = mapped_column(String(20))
    pbadms_std_inst_cd: Mapped[str | None] = mapped_column(String(20))
    inst_nm: Mapped[str | None] = mapped_column(String(200), index=True)

    ncs_cd_lst: Mapped[str | None] = mapped_column(String(500))
    ncs_cd_nm_lst: Mapped[str | None] = mapped_column(String(500))

    hire_type_lst: Mapped[str | None] = mapped_column(String(200))
    hire_type_nm_lst: Mapped[str | None] = mapped_column(String(200))

    work_rgn_lst: Mapped[str | None] = mapped_column(String(200))
    work_rgn_nm_lst: Mapped[str | None] = mapped_column(String(200), index=True)

    recrut_se: Mapped[str | None] = mapped_column(String(20))
    recrut_se_nm: Mapped[str | None] = mapped_column(String(50), index=True)

    pref_cond_cn: Mapped[str | None] = mapped_column(Text)
    recrut_nope: Mapped[int | None] = mapped_column(Integer)

    pbanc_bgng_ymd: Mapped[str | None] = mapped_column(String(8), index=True)
    pbanc_end_ymd: Mapped[str | None] = mapped_column(String(8), index=True)

    recrut_pbanc_ttl: Mapped[str | None] = mapped_column(String(500), index=True)
    src_url: Mapped[str | None] = mapped_column(String(1000))

    aply_qlfc_cn: Mapped[str | None] = mapped_column(Text)
    disqlfc_rsn: Mapped[str | None] = mapped_column(Text)
    scrnprcdr_mthd_expln: Mapped[str | None] = mapped_column(Text)
    pref_cn: Mapped[str | None] = mapped_column(Text)

    acbg_cond_lst: Mapped[str | None] = mapped_column(String(200))
    acbg_cond_nm_lst: Mapped[str | None] = mapped_column(String(200))

    ongoing_yn: Mapped[str | None] = mapped_column(String(1), index=True)

    posting_body: Mapped[str | None] = mapped_column(Text)

    raw_json: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )
    positions: Mapped[list["Position"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )

class Position(Base):
    """AI가 공고문에서 분해해 낸 직무 단위 레코드. 배경은 CODE_GUIDE 6.3."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.recrut_pblnt_sn"), index=True
    )

    title: Mapped[str] = mapped_column(String(300))
    career_level: Mapped[str] = mapped_column(String(20), index=True)
    min_education: Mapped[str | None] = mapped_column(String(20), index=True)
    required_license: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    headcount: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(200))
    requirements: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    extraction_model: Mapped[str | None] = mapped_column(String(50))
    extracted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    announcement: Mapped["Announcement"] = relationship(back_populates="positions")
    # 의미검색용 임베딩(1:1). lazy 로딩이라 일반 검색에는 영향 없음
    embedding: Mapped["PositionEmbedding | None"] = relationship(
        back_populates="position", cascade="all, delete-orphan", uselist=False
    )


class PositionEmbedding(Base):
    """직무 임베딩 벡터(직무당 1행). 저장 형식은 CODE_GUIDE 10.2."""

    __tablename__ = "position_embeddings"

    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(50))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    text_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    position: Mapped["Position"] = relationship(back_populates="embedding")


class QueryCache(Base):
    """검색어 임베딩 캐시(DB 영속). 2단계 캐시 구조는 CODE_GUIDE 10.5."""

    __tablename__ = "query_cache"

    query: Mapped[str] = mapped_column(String(200), primary_key=True)
    model: Mapped[str] = mapped_column(String(50), primary_key=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.recrut_pblnt_sn"), index=True
    )

    file_url: Mapped[str] = mapped_column(String(1000))
    file_name: Mapped[str | None] = mapped_column(String(300))
    file_ext: Mapped[str | None] = mapped_column(String(20))
    local_path: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    crawl_status: Mapped[str] = mapped_column(String(20))
    crawled_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    # PDF/HWP/이미지 첨부문서 분석 결과. extraction_method는 "pdf_text"|"pdf_vlm"|"hwp_html"|"hwpx_text"|"image_vlm" 중 하나.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str | None] = mapped_column(String(20))
    analyzed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    announcement: Mapped["Announcement"] = relationship(back_populates="attachments")
