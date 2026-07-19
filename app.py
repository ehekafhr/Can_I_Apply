from contextlib import asynccontextmanager

from fastapi import FastAPI

from lib.db import init_db
from server.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 기동 시 DB 테이블을 준비한다.
    init_db()
    yield


app = FastAPI(title="경력직이신가요?", lifespan=lifespan)
app.include_router(router)
