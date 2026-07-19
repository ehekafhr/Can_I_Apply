import hashlib
import logging
import os
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from lib.models import Attachment

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")

ATTACHMENT_EXTS = {".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".jpg", ".jpeg", ".png"}
DOWNLOAD_KEYWORDS = ("download", "attach", "fileDown", "file_down", "cmm/fms")

_headers = {"User-Agent": "Mozilla/5.0 (compatible; gyeongryeok-bot/0.1)"}


def _looks_like_attachment(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in ATTACHMENT_EXTS):
        return True
    return any(kw.lower() in url.lower() for kw in DOWNLOAD_KEYWORDS)


def find_attachment_links(src_url: str, client: httpx.Client) -> list[str]:
    resp = client.get(src_url, headers=_headers, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        absolute = urljoin(src_url, href)
        if _looks_like_attachment(absolute):
            links.add(absolute)
    return sorted(links)


def download_attachment(url: str, announcement_id: int, client: httpx.Client) -> tuple[str | None, str | None]:
    """(local_path, content_hash) 반환. 실패 시 (None, None)."""
    resp = client.get(url, headers=_headers, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    content = resp.content

    file_name = os.path.basename(urlparse(url).path) or "attachment"
    dest_dir = os.path.join(DOWNLOAD_DIR, str(announcement_id))
    os.makedirs(dest_dir, exist_ok=True)
    local_path = os.path.join(dest_dir, file_name)
    with open(local_path, "wb") as f:
        f.write(content)

    content_hash = hashlib.sha256(content).hexdigest()
    return local_path, content_hash


def crawl_announcement(session, announcement) -> str:
    """공고 하나의 srcUrl을 방문해 첨부파일을 best-effort로 수집한다.
    성공/실패 모두 예외를 밖으로 던지지 않고 상태 문자열을 반환한다."""
    if not announcement.src_url:
        return "no_src_url"

    with httpx.Client() as client:
        try:
            links = find_attachment_links(announcement.src_url, client)
        except Exception as exc:
            logger.info("srcUrl fetch 실패 (id=%s): %s", announcement.recrut_pblnt_sn, exc)
            return "fetch_failed"

        if not links:
            return "no_links_found"

        for link in links:
            ext = os.path.splitext(urlparse(link).path)[1].lower() or None
            try:
                local_path, content_hash = download_attachment(
                    link, announcement.recrut_pblnt_sn, client
                )
                status = "downloaded"
            except Exception as exc:
                logger.info("첨부파일 다운로드 실패 (%s): %s", link, exc)
                local_path, content_hash = None, None
                status = "fetch_failed"

            session.add(
                Attachment(
                    announcement_id=announcement.recrut_pblnt_sn,
                    file_url=link,
                    file_name=os.path.basename(urlparse(link).path) or None,
                    file_ext=ext,
                    local_path=local_path,
                    content_hash=content_hash,
                    crawl_status=status,
                )
            )
        session.commit()
        return "found"
