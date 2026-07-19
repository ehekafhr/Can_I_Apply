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

## 외부 공개 (외부 인터넷 접속)
`--host 0.0.0.0`만으로는 밖에서 닿지 않기 때문에, Cloudflare 터널로 공개한다.

```bash
# 0) cloudflared 설치 (최초 1회, sudo 불필요)
mkdir -p ~/.local/bin
curl -fL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared

# 1) 웹서버 실행 (위 3단계)
uvicorn app:app --host 0.0.0.0 --port 8420

# 2) 다른 터미널에서 터널 실행 → https://<랜덤>.trycloudflare.com 주소가 뜬다
~/.local/bin/cloudflared tunnel --url http://localhost:8420
```

- 임시 URL이라 터널을 재시작하면 주소가 바뀐다. 인증은 없으니 URL을 아는
  누구나 접속 가능하다..
- 종료: `pkill -f "cloudflared tunnel"` (서버까지: `pkill -f "uvicorn app:app"`)
- [TODO] 인증, 고정 도메인