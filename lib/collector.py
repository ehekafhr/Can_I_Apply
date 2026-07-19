import json
import logging

from lib.db import SessionLocal, init_db
from lib.jobalio_client import JobAlioClient
from lib.models import Announcement

logger = logging.getLogger(__name__)

_FIELD_MAP = {
    "recrutPblntSn": "recrut_pblnt_sn",
    "pblntInstCd": "pblnt_inst_cd",
    "pbadmsStdInstCd": "pbadms_std_inst_cd",
    "instNm": "inst_nm",
    "ncsCdLst": "ncs_cd_lst",
    "ncsCdNmLst": "ncs_cd_nm_lst",
    "hireTypeLst": "hire_type_lst",
    "hireTypeNmLst": "hire_type_nm_lst",
    "workRgnLst": "work_rgn_lst",
    "workRgnNmLst": "work_rgn_nm_lst",
    "recrutSe": "recrut_se",
    "recrutSeNm": "recrut_se_nm",
    "prefCondCn": "pref_cond_cn",
    "recrutNope": "recrut_nope",
    "pbancBgngYmd": "pbanc_bgng_ymd",
    "pbancEndYmd": "pbanc_end_ymd",
    "recrutPbancTtl": "recrut_pbanc_ttl",
    "srcUrl": "src_url",
    "aplyQlfcCn": "aply_qlfc_cn",
    "disqlfcRsn": "disqlfc_rsn",
    "scrnprcdrMthdExpln": "scrnprcdr_mthd_expln",
    "prefCn": "pref_cn",
    "acbgCondLst": "acbg_cond_lst",
    "acbgCondNmLst": "acbg_cond_nm_lst",
    "ongoingYn": "ongoing_yn",
}


# 받아온 item(dict)에서 Announcement 모델 생성자에 넣을 kwargs(dict)를 만든다.
def _item_to_kwargs(item: dict) -> dict:
    kwargs = {model_key: item.get(api_key) for api_key, model_key in _FIELD_MAP.items()}
    kwargs["raw_json"] = json.dumps(item, ensure_ascii=False)
    return kwargs

# update + insert
def _upsert(session, item: dict) -> bool:
    kwargs = _item_to_kwargs(item)
    pk = kwargs["recrut_pblnt_sn"]
    existing = session.get(Announcement, pk)
    if existing is None:
        session.add(Announcement(**kwargs))
        return True # insert
    for key, value in kwargs.items():
        setattr(existing, key, value)
    return False #update

# 접수중인 공고 전체 upsert, 신규 저장된 공고의 recrut_pblnt_sn 목록 반환
def backfill_ongoing(num_rows: int = 100) -> list[int]:
    init_db() #metadata.create_all(engine) 호출
    new_ids: list[int] = []
    with JobAlioClient() as client, SessionLocal() as session:
        for page in client.iter_pages(num_rows=num_rows, ongoingYn="Y"):
            for item in page:
                if _upsert(session, item):
                    new_ids.append(item.get("recrutPblntSn"))
            session.commit()
    logger.info("backfill_ongoing: %d건 신규 저장", len(new_ids))
    return new_ids

# 최신순으로 순회하다 이미 DB에 있는 공고를 만나면 중단
def collect_new(num_rows: int = 100) -> list[int]:
    init_db() #metadata.create_all(engine) 호출
    new_ids: list[int] = []
    with JobAlioClient() as client, SessionLocal() as session:
        stop = False
        for page in client.iter_pages(num_rows=num_rows):
            for item in page:
                pk = item.get("recrutPblntSn")
                if session.get(Announcement, pk) is not None:
                    stop = True
                    break
                if _upsert(session, item):
                    new_ids.append(pk)
            session.commit()
            if stop:
                break
    logger.info("collect_new: %d건 신규 저장", len(new_ids))
    return new_ids
