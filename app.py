from fastapi import FastAPI

from lib.db import init_db
from server.routes import router

app = FastAPI(title="경력직이신가요?")
app.include_router(router)


@app.on_event("startup")
def _startup():
    init_db()
