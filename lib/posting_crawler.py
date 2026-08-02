"""잡알리오 상세 페이지의 "공고 내 영역" 본문 크롤러. 자세한 설명은 CODE_GUIDE 11장."""

import re

import httpx
from bs4 import BeautifulSoup

VIEW_URL = "https://job.alio.go.kr/recruitview.do?idx={}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; gyeongryeok-bot/0.1)"}

# tab-1의 h4 섹션 중 이 이름들만 취한다. 결격사유(법정 보일러플레이트)는 제외.
_KEEP_SECTIONS = ("응시자격", "지원자격", "자격요건", "우대")
# 삭제·오류 페이지 감지 문구
_DEAD_MARKERS = ("존재하지 않", "잘못된 접근", "삭제되었")


def extract_body(html: str) -> str | None:
    """상세 페이지 HTML에서 공고 내 영역 본문(응시자격/우대)만 추출한다."""
    soup = BeautifulSoup(html, "html.parser")
    tab = soup.select_one("#tab-1")
    if tab is None or any(m in soup.get_text() for m in _DEAD_MARKERS):
        return None

    sections: list[str] = []
    for h in tab.find_all(["h3", "h4"]):
        title = h.get_text(" ", strip=True)
        if not any(k in title for k in _KEEP_SECTIONS):
            continue
        parts = []
        for sib in h.find_next_siblings():
            if sib.name in ("h3", "h4"):  # 다음 섹션 시작
                break
            text = sib.get_text("\n", strip=True)
            if text:
                parts.append(text)
        if parts:
            sections.append(f"[{title}]\n" + "\n".join(parts))

    body = re.sub(r"\n{3,}", "\n\n", "\n\n".join(sections)).strip()
    return body or None


def fetch_posting_body(recrut_pblnt_sn: int, client: httpx.Client) -> str | None:
    """공고번호로 잡알리오 상세 페이지를 받아 본문을 추출한다. 실패 시 None."""
    resp = client.get(
        VIEW_URL.format(recrut_pblnt_sn),
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return extract_body(resp.text)
