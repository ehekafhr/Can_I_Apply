import json
import os

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODEL = os.environ.get("EXTRACTOR_MODEL", "exaone3.5:7.8b")

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "positions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "직무명"},
                    "career_level": {
                        "type": "string",
                        "enum": ["신입", "경력", "신입+경력", "무관"],
                    },
                    "min_education": {
                        "type": "string",
                        "enum": ["무관", "학사", "석사", "박사"],
                        "description": "이 직무 지원에 필요한 최소 학력. 학력 제한이 없거나 고졸/초대졸 수준이면 '무관', "
                        "4년제 대졸 이상이면 '학사'. '신입'이어도 석사 이상을 요구하면 '석사'로 표기",
                    },
                    "required_license": {
                        "type": "string",
                        "description": "지원에 반드시 필요한 국가자격/면허명(예: '의사 면허', '간호사 면허', '공인회계사'). 없으면 빈 문자열",
                    },
                    "headcount": {"type": "integer", "description": "모집인원, 모르면 0"},
                    "location": {"type": "string", "description": "근무지, 모르면 빈 문자열"},
                    "requirements": {"type": "string", "description": "이 직무의 자격요건 요약"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "검색용 키워드 태그(직무 분야, 고용형태, 지역, 경력구분 등 5~10개)",
                    },
                    "description": {
                        "type": "string",
                        "description": "이 직무/공고에 대한 3~5문장 설명. 어떤 일을 하는 자리인지, 지원 시 알아야 할 핵심 조건을 정리",
                    },
                    "evidence": {"type": "string", "description": "career_level 판단의 근거가 된 원문 문장"},
                    "confidence": {"type": "number", "description": "0~1 사이 확신도"},
                },
                "required": [
                    "title",
                    "career_level",
                    "min_education",
                    "required_license",
                    "headcount",
                    "location",
                    "requirements",
                    "tags",
                    "description",
                    "evidence",
                    "confidence",
                ],
            },
        }
    },
    "required": ["positions"],
}

# 프롬프트. schema를 그대로 Ollama에 전달하면, 모델이 JSON 스키마에 맞는 JSON만 반환하도록 유도할 수 있다.

SYSTEM_PROMPT = """너는 한국 공공기관 채용공고 분석가다. 하나의 채용공고 원문을 받아,
그 공고에 포함된 채용 "직무" 단위로 분해해라.

핵심 배경: 공공기관 채용공고는 공고 하나에 여러 직무·전형이 섞여 있는 경우가 많고,
API의 경력구분(recrutSe) 필드는 공고 전체에 하나만 붙어있어 "신입+경력"처럼 뭉뚱그려져 있다.
네 역할은 공고 원문(자격요건/우대사항 등)을 읽고 실제로 몇 개의 직무가 있는지,
직무별로 경력구분이 정말 어떻게 다른지 판단하는 것이다.

규칙:
- 공고에 직무 구분이 명확히 없고 단일 채용이면 positions는 1개만 반환한다.
- career_level 판단이 애매하면 "신입"으로 분류하지 말고 원문에 가장 가깝게 판단하되,
  낮은 confidence를 부여해라(신입인데 경력으로 분류돼 검색에서 누락되는 false negative가 최악).
- **career_level="신입"이라고 해서 아무나 지원 가능하다는 뜻이 아니다.** "신입(석사 이상)",
  "학사 이상 소지자(신입 지원 가능)"처럼 학위 조건이 붙어있으면, 그 최소 학위를
  반드시 min_education에 정확히 반영해라. 이걸 놓치면 학사 졸업 구직자가 지원 불가능한
  자리를 "신입"만 보고 지원 가능하다고 오해하게 된다.
- min_education은 실제 지원 가능한 최소 학력으로 정확히 판단해라. 학력 제한이 없거나
  고졸/초대졸 수준이면 "무관", "대졸/학사 이상"이면 "학사", "석사 이상"이면 "석사",
  "박사"면 "박사". 애매하면 더 낮은 학력 쪽으로 판단해 구직자가 검색에서 누락되지 않게
  해라(학력 요건을 실제보다 높게 잡는 것이 최악).
- **의료직·전문자격직은 반드시 required_license를 채워라.** 의사/간호사/약사/임상병리사/
  방사선사/물리치료사/영양사 면허, 공인회계사/세무사/변호사/노무사/기술사/기사 등
  자격요건에 "면허 소지자", "자격증 소지자", "~사 자격" 형태로 명시된 국가자격/면허는
  누락하지 말고 정확한 명칭으로 적어라. 그런 요건이 없으면 반드시 빈 문자열("")로 채워라.
- evidence에는 반드시 원문에 실제로 있는 문장/구절을 인용해라. 지어내지 마라.
- description은 구직자가 이 직무 카드만 보고도 지원 여부를 판단할 수 있도록 실질적인 정보를 담아라.
  min_education이나 required_license가 있으면 description에도 그 조건을 명시해라.
- tags는 검색에 쓰일 것이므로 직무분야, 고용형태, 지역, 경력구분, 학위조건, 자격면허 등
  구체적인 한국어 키워드로 작성해라.
- 반드시 지정된 JSON 스키마에 맞는 JSON만 출력해라. 다른 설명 문장을 덧붙이지 마라.
"""


def _build_user_content(announcement) -> str:
    fields = {
        "기관명": announcement.inst_nm,
        "공고제목": announcement.recrut_pbanc_ttl,
        "API상 경력구분": announcement.recrut_se_nm,
        "고용형태": announcement.hire_type_nm_lst,
        "근무지역": announcement.work_rgn_nm_lst,
        "직무분야(NCS)": announcement.ncs_cd_nm_lst,
        "모집인원": announcement.recrut_nope,
        "학력조건": announcement.acbg_cond_nm_lst,
        "우대조건": announcement.pref_cond_cn,
        "자격요건": announcement.aply_qlfc_cn,
        "우대사항": announcement.pref_cn,
    }
    lines = [f"[{k}]\n{v}" for k, v in fields.items() if v not in (None, "")]
    return "\n\n".join(lines)


class Extractor:
    """로컬 Ollama 서버(exaone3.5)를 통해 공고를 직무 단위로 분해한다."""

    def __init__(self, base_url: str = OLLAMA_URL, model: str = MODEL, timeout: float = 300.0):
        self.base_url = base_url
        self.model = model
        self.client = httpx.Client(timeout=timeout)

    def extract(self, announcement) -> list[dict]:
        resp = self.client.post(
            self.base_url,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_content(announcement)},
                ],
                "format": _OUTPUT_SCHEMA,
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)["positions"]
