# ResearchProjAssist — 프로젝트 현황 메모

> 세션 시작 시 이 파일을 읽고, 완료 작업과 남은 작업을 사용자에게 정리해서 알려줄 것.

---

## 프로젝트 개요

한국연구재단(NRF), NTIS, IRIS에서 연구과제 공고를 자동 수집하고,
키워드/분야 필터링 후 이메일(텔레그램 예정)로 알림을 보내는 자동화 시스템.

- **스택**: Python 3.14 + FastAPI + SQLite + APScheduler + Playwright + httpx
- **대시보드**: http://localhost:8000
- **실행**: `python src/main.py` (또는 .Codex/launch.json 참조)

---

## 완료된 작업

### Phase 1 — 기반 구축
- [x] 전체 아키텍처 설계 및 프로젝트 구조 생성
- [x] SQLAlchemy 모델 (`Announcement`, `Filter`)
- [x] BaseScraper 추상 클래스 + AnnouncementData DTO
- [x] NRF 스크래퍼 (Playwright SPA 방식)
- [x] NTIS 스크래퍼 (httpx + BeautifulSoup, `table.basic_list` 타겟)
- [x] IRIS 스크래퍼 (Playwright, onclick 패턴 파싱)
- [x] FilterEngine 기본 구현
- [x] EmailNotifier (SMTP)
- [x] TelegramNotifier (비활성화 상태)
- [x] APScheduler 기반 스케줄러 (6시간 간격)
- [x] FastAPI 웹 대시보드

### Phase 2 — 버그 수정 및 안정화
- [x] TemplateResponse API 수정 (Starlette 최신 버전 호환)
- [x] Windows --reload 모드 OSError 수정 (launch.json에서 제거)
- [x] NTIS td 인덱스 정확 매핑 (td[3]=제목, td[4]=부처, td[5]=접수일, td[6]=마감일)
- [x] NRF URL 수정 + Playwright 전환
- [x] IRIS URL 수정 (`retrieveBsnsAncmBtinSituListView.do`) + onclick 파싱
- [x] asyncio.get_event_loop() → asyncio.run() (Python 3.14)
- [x] config.py DB 경로 절대경로 변환
- [x] 초기 스크래핑 safe wrapper (서버 기동 보호)
- [x] 테스트 25개 전부 통과

### Phase 3 — 이메일 알림 연결 (우선순위 1번 완료)
- [x] Gmail SMTP 연결 (smtp.gmail.com:587)
- [x] 발신/수신 이메일: tirano1019@gmail.com
- [x] Gmail 앱 비밀번호 설정 (.env 파일)
- [x] test_connection() / send_test() 메서드 추가
- [x] 이메일 HTML 템플릿 개선 (D-day 색상 표시)
- [x] scripts/test_email.py 테스트 스크립트
- [x] 실제 테스트 메일 수신 확인 완료

### Phase 4 — 복합 필터 엔진 (우선순위 3번 완료)
- [x] `src/filters/synonyms.yaml` — 동의어 사전 (13그룹, 130여 단어)
- [x] `src/filters/engine.py` 전면 재작성
  - 동의어 확장 검색 (예: "AI" → 인공지능, 머신러닝, ICT 등)
  - 관련도 스코어링 (매칭 키워드 수로 점수 계산)
  - 정렬: score DESC → 직접매칭 우선 → 마감일 ASC
- [x] `src/web/app.py` — Python 레벨 검색 + /api/synonyms 엔드포인트
- [x] `src/web/templates/index.html` — 관련도 배지 + 동의어 힌트 UI
- [x] 스케줄러(scheduler.py)도 동의어 확장 필터링 적용

---

## 남은 작업 (우선순위 순)

| # | 항목 | 난이도 | 메모 |
|---|------|--------|------|
| 1 | **마감일 D-day 리마인더** | ⭐⭐ | D-7, D-3, D-1 자동 알림. 다음 작업으로 추천. |
| 2 | **텔레그램 알림 연결** | ⭐ | .env에 TELEGRAM_BOT_TOKEN/CHAT_ID 입력 후 활성화 |
| 3 | **공고 상세 정보 자동 수집** | ⭐⭐⭐ | 사업비, 지원자격, PDF 첨부파일 크롤링 |
| 4 | **Google Calendar 연동** | ⭐⭐ | 마감일 → 캘린더 자동 등록 |
| 5 | **과거 데이터 패턴 분석** | ⭐⭐⭐ | 부처별 트렌드, 분야별 통계 시각화 |

---

## 동의어 사전 업데이트 대기 항목

> 사용자 요청: 작업 중간중간 업데이트 제안 포함할 것

- `보호연구` 그룹: NRF 실제 공고 수집 후 표현 확인 ("기본연구", "우수신진" 등 추가 검토)
- `이공` 그룹: "STEM", "이공계" 등 영문/변형 표현 추가 여부 결정 필요
- NRF 공고 수집 후 자주 등장하는 단어 → 자동 제안 예정

---

## 주요 설정 파일

| 파일 | 내용 |
|------|------|
| `.env` | EMAIL_SENDER, EMAIL_PASSWORD (Gmail 앱 비밀번호) |
| `config/config.yaml` | 스크래핑 주기, 필터 키워드, 알림 on/off |
| `src/filters/synonyms.yaml` | 동의어 사전 (사용자 직접 편집 가능) |
| `.Codex/launch.json` | 서버 실행 설정 |

## 현재 수집 현황 (마지막 확인: 2026-04-19)

- NTIS: 10건 정상 수집
- IRIS: 10건 정상 수집
- NRF: 0건 (현재 신규 공모 없음 — 정상)
- 총 DB: 20건
