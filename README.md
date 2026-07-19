## OLLAMA 띄우기
```bash
export OLLAMA_MODELS=~/.local/ollama/models
~/.local/ollama/bin/ollama serve        # 11434 포트에서 대기
```
## 실행

```bash
# 0) 준비
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# Ollama 서버 + 모델(exaone3.5:7.8b)이 로컬에서 떠 있어야 추출이 된다.

# 1) 수집
python collect.py --backfill      # 최초: 진행중 공고 전체
python collect.py                 # 이후: 신규만

# 2) 추출
python extract.py                 # positions 없는 공고를 직무로 분해

# 3) 웹서버
uvicorn app:app --host 0.0.0.0 --port 8420
# http://127.0.0.1:8420 접속
```