# 연구과제 자동 알림 시스템 (ResearchProjAssist)

한국연구재단(NRF), NTIS, IRIS에서 새로운 연구과제 공고를 자동으로 수집하고,
키워드/분야 필터링 후 이메일과 텔레그램으로 알림을 보내는 시스템입니다.

## 주요 기능

- **자동 스크래핑**: NRF, NTIS, IRIS 사업공고를 주기적으로 수집
- **키워드/분야 필터링**: 관심 분야의 공고만 선별하여 알림
- **이메일 알림**: HTML 포맷의 공고 요약 메일 발송
- **텔레그램 봇 알림**: 실시간 채팅 알림
- **웹 대시보드**: 공고 목록 조회, 검색, 수동 스크래핑

## 설치

```bash
# 의존성 설치
pip install -r requirements.txt

# Playwright 브라우저 설치 (NTIS, IRIS 스크래핑에 필요)
playwright install chromium
```

## 설정

### 1. config/config.yaml 수정

```yaml
filters:
  keywords: ["AI", "인공지능", "바이오"]   # 관심 키워드
  categories: ["공학", "자연과학"]          # 관심 분야
```

### 2. .env 파일 생성 (민감 정보)

```bash
cp .env.example .env
# .env 파일을 열어 아래 값 입력
```

| 변수 | 설명 |
|------|------|
| `EMAIL_PASSWORD` | 이메일 앱 비밀번호 (Gmail: 앱 비밀번호 사용) |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 BotFather에서 발급받은 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 채팅/채널 ID |

## 실행

```bash
# 웹 대시보드 + 자동 스크래핑 시작
python -m src.main

# 브라우저에서 http://localhost:8000 접속
```

## API

| 엔드포인트 | 메서드 | 설명 |
|------------|--------|------|
| `/` | GET | 웹 대시보드 (공고 목록) |
| `/api/announcements` | GET | 공고 목록 JSON API |
| `/api/scrape` | POST | 수동 스크래핑 실행 |

## 테스트

```bash
python -m pytest tests/ -v
```

## 프로젝트 구조

```
ResearchProjAssist/
├── config/config.yaml      # 설정 파일
├── src/
│   ├── main.py             # 앱 진입점
│   ├── config.py           # 설정 로더
│   ├── scheduler.py        # 스크래핑 스케줄러
│   ├── scrapers/           # NRF, NTIS, IRIS 스크래퍼
│   ├── models/             # DB 모델
│   ├── filters/            # 키워드/분야 필터 엔진
│   ├── notifiers/          # 이메일, 텔레그램 알림
│   └── web/                # FastAPI 대시보드
├── tests/                  # 단위 테스트
└── data/                   # SQLite DB (자동 생성)
```
