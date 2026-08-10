import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs.db")
# sqlite:///는 db 파일 경로
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
# session 생성자. DB와 상호작용할 때는 반드시 with SessionLocal() as session: ... 구문으로 세션을 열고 닫아야 한다.
# expire_on_commit=False: 커밋 후에도 로드된 값 재사용(CODE_GUIDE 2.1)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)



class Base(DeclarativeBase):
    pass


def init_db():
    from lib import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(engine)
    _ensure_columns()


# create_all은 새 컬럼을 추가하지 않으므로, 기존 DB에 누락된 컬럼만 ALTER로 채운다.
_ADDED_COLUMNS = {
    "announcements": {"posting_body": "TEXT"},
    "attachments": {
        "extracted_text": "TEXT",
        "extraction_method": "TEXT",
        "analyzed_at": "DATETIME",
    },
}


def _ensure_columns():
    insp = inspect(engine)
    for table, columns in _ADDED_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, coltype in columns.items():
            if name not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))
