import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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

    raw_json: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
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

    announcement: Mapped["Announcement"] = relationship(back_populates="attachments")
