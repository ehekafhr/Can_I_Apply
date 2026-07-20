## OLLAMA 띄우기
```bash
export OLLAMA_MODELS=~/.local/ollama/models
~/.local/ollama/bin/ollama serve        
ollama pull exaone3.5:7.8b              
ollama pull bge-m3                      
```
## 실행

```bash
# 0) 준비
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# Ollama 서버 + 모델(exaone3.5:7.8b, bge-m3)이 로컬에서 떠 있어야 한다.

# 1) 수집
python collect.py --backfill      # 최초: 진행중 공고 전체
python collect.py                 # 이후: 신규만

# 2) 추출
python extract.py                 # positions 없는 공고를 직무로 분해

# 2.5) 임베딩 (의미검색용 직무 벡터)
python embed.py                   # 새/변경된 직무만 임베딩 (증분)
python embed.py --all             # 전부 다시 (임베딩 모델을 바꿨을 때)

# 3) 웹서버
uvicorn app:app --host 0.0.0.0 --port 8420
# http://127.0.0.1:8420 접속
```

### 의미(유사도) 검색
웹 화면의 **의미 검색** 칸에 단어(예: `인공지능`)를 넣으면, 글자가 정확히
일치하지 않아도 의미가 비슷한 직무를 코사인 유사도순으로 찾아준다.
결과가 너무 많거나 적으면 **정밀도**(최고 유사도 대비 비율)를 조절한다. 

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