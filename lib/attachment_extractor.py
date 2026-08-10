"""첨부파일(PDF/HWP/HWPX/이미지) 본문 추출.

PDF는 텍스트 레이어가 있으면 그대로 쓰고, 없으면(스캔본) 앞 몇 페이지를 이미지로
렌더링해 로컬 Ollama VLM(비전 모델)으로 OCR한다. HWP/HWPX는 텍스트를 직접 뽑고,
이미지 첨부파일은 통째로 VLM에 넘긴다.
"""

import base64
import io
import logging
import os
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree

import httpx
import pymupdf
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

VLM_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
# minicpm-v는 실측(공공기관 채용공고 스캔 이미지)에서 표 내용을 통째로 지어내는
# 환각이 심해 기본값에서 제외했다. qwen2.5vl은 같은 이미지에서 표를 정확히 옮겼다.
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")

# PDF 페이지당 이 정도 텍스트도 안 나오면 텍스트 레이어가 없는 스캔본으로 간주한다.
_PDF_MIN_CHARS_PER_PAGE = 20
# 스캔본 VLM 분석은 앞 N페이지까지만(대부분의 자격요건은 앞쪽에 있고, 비용/시간을 제한하기 위함).
_PDF_MAX_VLM_PAGES = 5

_VLM_PROMPT = (
    "이 이미지는 한국 공공기관 채용공고문의 일부다. 이미지에 보이는 텍스트를 "
    "빠짐없이 그대로 옮겨 적어라(OCR). 표가 있으면 행/열 내용을 문장으로 풀어써라. "
    "다른 설명 없이 옮겨 적은 텍스트만 출력해라."
)


def _vlm_ocr_single(image_bytes: bytes, timeout: float = 120.0) -> str | None:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        resp = httpx.post(
            VLM_URL,
            json={
                "model": VLM_MODEL,
                "messages": [{"role": "user", "content": _VLM_PROMPT, "images": [b64]}],
                "stream": False,
                # 기본 컨텍스트(4096)는 고해상도 이미지 하나의 비전 토큰만으로도
                # 넘치는 경우가 많아(실측: 4150 토큰 요청이 4096 한도에서 400 에러)
                # 넉넉히 늘려준다.
                "options": {"temperature": 0.1, "num_ctx": 8192},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"].strip()
        return text or None
    except Exception as exc:
        logger.warning("VLM OCR 실패: %s", exc)
        return None


# 세로/가로 비율이 이보다 크면(스크롤형 스캔 포스터 등) VLM이 반복·환각을 일으키기
# 쉬워서, 정상 비율에 가까운 조각으로 잘라 각각 OCR한 뒤 이어붙인다.
_TILE_MAX_ASPECT = 1.4
_TILE_OVERLAP = 0.08  # 조각 경계에서 문장이 잘리는 것을 줄이기 위한 겹침 비율
_TILE_MAX_COUNT = 6  # 비용/시간 상한


def _split_tall_image(image_bytes: bytes) -> list[bytes]:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if h <= w * _TILE_MAX_ASPECT:
        return [image_bytes]

    tile_h = int(w * _TILE_MAX_ASPECT)
    step = max(1, int(tile_h * (1 - _TILE_OVERLAP)))
    tiles = []
    y = 0
    while y < h and len(tiles) < _TILE_MAX_COUNT:
        bottom = min(y + tile_h, h)
        buf = io.BytesIO()
        img.crop((0, y, w, bottom)).save(buf, format="PNG")
        tiles.append(buf.getvalue())
        if bottom >= h:
            break
        y += step
    return tiles


def _vlm_ocr(image_bytes: bytes) -> str | None:
    """긴 이미지는 조각내어 각각 OCR한 뒤 이어붙인다."""
    parts = [t for t in (_vlm_ocr_single(tile) for tile in _split_tall_image(image_bytes)) if t]
    return "\n\n".join(parts) or None


def _extract_pdf(path: str) -> tuple[str | None, str]:
    doc = pymupdf.open(path)
    try:
        page_texts = [page.get_text().strip() for page in doc]
        joined = "\n\n".join(t for t in page_texts if t)
        if len(joined) >= _PDF_MIN_CHARS_PER_PAGE * max(len(doc), 1):
            return joined, "pdf_text"

        # 텍스트 레이어가 빈약함 = 스캔본일 가능성. 앞 페이지만 VLM으로 OCR.
        ocr_parts = []
        for page in list(doc)[:_PDF_MAX_VLM_PAGES]:
            pix = page.get_pixmap(dpi=150)
            text = _vlm_ocr(pix.tobytes("png"))
            if text:
                ocr_parts.append(text)
        return ("\n\n".join(ocr_parts) or None), "pdf_vlm"
    finally:
        doc.close()


def _extract_hwp(path: str) -> tuple[str | None, str]:
    """pyhwp의 hwp5html로 변환 후 텍스트만 뽑는다.

    hwp5txt(단순 텍스트 변환)는 표를 "<표>" 자리표시자로만 남기고 셀 내용을
    버린다 — 채용공고의 자격요건/제출서류는 거의 항상 표 안에 있으므로 이러면
    핵심 정보가 통째로 사라진다. hwp5html은 표를 HTML <table>로 제대로
    변환하므로, HTML을 파싱해 텍스트만 뽑으면 표 내용까지 살아남는다.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            subprocess.run(
                ["hwp5html", "--output", tmp_dir, path],
                check=True,
                capture_output=True,
                timeout=60,
            )
            html_path = os.path.join(tmp_dir, "index.xhtml")
            with open(html_path, encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            text = soup.get_text("\n", strip=True)
            return (text or None), "hwp_html"
        except Exception as exc:
            logger.warning("HWP 텍스트 추출 실패 (%s): %s", path, exc)
            return None, "hwp_html"


def _extract_hwpx(path: str) -> tuple[str | None, str]:
    """HWPX는 zip 안에 XML 섹션들로 구성된다. 태그를 걷어내고 텍스트 노드만 모은다."""
    try:
        parts = []
        with zipfile.ZipFile(path) as z:
            section_names = sorted(
                n for n in z.namelist()
                if n.startswith("Contents/section") and n.endswith(".xml")
            )
            for name in section_names:
                with z.open(name) as f:
                    root = ElementTree.parse(f).getroot()
                    parts.append("".join(root.itertext()))
        joined = "\n".join(p.strip() for p in parts if p.strip())
        return (joined or None), "hwpx_text"
    except Exception as exc:
        logger.warning("HWPX 텍스트 추출 실패 (%s): %s", path, exc)
        return None, "hwpx_text"


def _extract_image(path: str) -> tuple[str | None, str]:
    with open(path, "rb") as f:
        data = f.read()
    return _vlm_ocr(data), "image_vlm"


_DISPATCH = {
    ".pdf": _extract_pdf,
    ".hwp": _extract_hwp,
    ".hwpx": _extract_hwpx,
    ".jpg": _extract_image,
    ".jpeg": _extract_image,
    ".png": _extract_image,
}


def extract_attachment(local_path: str, file_ext: str | None) -> tuple[str | None, str | None]:
    """첨부파일에서 텍스트를 추출한다. (텍스트, 추출방식) 반환. 지원하지 않는 형식은 (None, None)."""
    ext = (file_ext or os.path.splitext(local_path)[1]).lower()
    handler = _DISPATCH.get(ext)
    if handler is None:
        return None, None
    return handler(local_path)
