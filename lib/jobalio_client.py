import os

import httpx

LIST_URL = "https://apis.data.go.kr/1051000/recruitment/list"
KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "key.txt")


def _load_key() -> str:
    with open(KEY_PATH, encoding="utf-8") as f:
        return f.read().strip()


class JobAlioClient:
    """잡알리오 공공기관 채용정보 조회 API 클라이언트.

    Swagger 문서를 확인할 수 없어 실제 호출로 검증한 파라미터만 사용한다:
    pageNo/numOfRows로 페이지네이션하며, 결과는 항상 recrutPblntSn 내림차순(최신순)이다.
    recrutSe/pbancBgngYmd/workRgnLst/hireTypeLst/ncsCdLst/pblntInstCd/ongoingYn은
    완전일치 필터로 동작하지만, 날짜 구간 검색 파라미터는 존재하지 않는다.
    """

    def __init__(self, service_key: str | None = None, timeout: float = 15.0):
        self.service_key = service_key or _load_key()
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})

    def list_page(self, page_no: int, num_rows: int = 100, **filters) -> dict:
        params = {"serviceKey": self.service_key, "pageNo": page_no, "numOfRows": num_rows}
        params.update({k: v for k, v in filters.items() if v is not None})
        resp = self.client.get(LIST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("resultCode") not in (200, "200"):
            raise RuntimeError(f"jobalio API error: {data}")
        return data

    def iter_pages(self, num_rows: int = 100, **filters):
        """필터 조건에 맞는 전체 결과를 최신순으로 페이지 단위로 yield한다."""
        page_no = 1
        while True:
            data = self.list_page(page_no, num_rows=num_rows, **filters)
            result = data.get("result", [])
            if not result:
                return
            yield result
            if len(result) < num_rows:
                return
            page_no += 1

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
