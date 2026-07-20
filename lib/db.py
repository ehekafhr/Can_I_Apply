import os

from sqlalchemy import create_engine
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
