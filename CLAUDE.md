# ResearchProjAssist — 프로젝트 현황 메모

> 세션 시작 시 이 파일을 읽고, 완료 작업과 남은 작업을 사용자에게 정리해서 알려줄 것.

---

## 프로젝트 경로 이력

| 구분 | 경로 |
|------|------|
| 변경 전 | `D:\ResearchProjAssist` |
| 현재 | `D:\ClaudeCodeProj\ResearchProjAssist` |
| 변경일 | 2026-05-30 |

---

## 프로젝트 개요

한국연구재단(NRF), NTIS, IRIS에서 연구과제 공고를 자동 수집하고,
키워드/분야 필터링 후 이메일(텔레그램 예정)로 알림을 보내는 자동화 시스템.

- **스택**: Python 3.14 + FastAPI + SQLite + APScheduler + Playwright + httpx
- **대시보드**: http://localhost:8000
- **실행**: `python src/main.py` (또는 .claude/launch.json 참조)

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

### Phase 5 — D-day 리마인더 (완료)
- [x] `NotificationLog` 테이블 추가 (채널·이벤트별 발송 이력 관리)
- [x] `src/notifiers/email.py` — `send_reminder()` 추가 + 긴급도별 HTML 템플릿
- [x] `src/scheduler.py` — `run_reminder_cycle()` 추가 (D-7/D-3/D-1 중복 방지)
- [x] `src/main.py` — 매일 09:00 리마인더 스케줄 등록 + reload=True 제거
- [x] `src/web/app.py` + `index.html` — 대시보드에 알림 이력 배지 표시
- [x] `scripts/test_reminder.py` — 수동 테스트 스크립트

### Phase 6 — 공고 상세 정보 자동 수집 (완료)
- [x] `AnnouncementData` DTO에 budget, attachments 필드 추가
- [x] DB `Announcement` 모델에 budget, attachments, detail_fetched 컬럼 추가 (ALTER TABLE 마이그레이션)
- [x] **NTIS** `scrape_detail()`: div.se-contents(본문), div.notice_view 파이프 파싱(예산), div.summary_file(첨부) — 10/10 수집 성공
- [x] **IRIS** `scrape_detail()`: bsnsAncmBtinSituListForm 직접 조작(ancmId POST) → retrieveBsnsAncmView.do 이동, li.write[접수기간](마감일), div.se-contents(본문), 본문 텍스트 붙임 패턴(첨부) — 10/10 수집 성공
- [x] **NRF** `scrape_detail()`: Playwright 상세 URL 직접 방문, th/td 키워드 파싱 — 현재 공모 없어 테스트 대기
- [x] `scheduler.py` `_enrich_new_items()` 추가 — 신규 공고 저장 직후 상세 자동 수집
- [x] `scripts/test_detail.py` — 수동 테스트 스크립트

### Phase 7 — GitHub Pages + Actions 구조 전환 (완료)
- [x] GitHub Actions 워크플로 (`scrape.yml`): 6시간 주기 자동 스크래핑 + JSON 내보내기 + main 브랜치 커밋
- [x] `scripts/run_scrape.py` 진입점 생성 (스크래핑 → 리마인더 → export 순서)
- [x] `src/export.py`: DB → `docs/data/announcements.json` + `docs/data/synonyms.json` 내보내기
- [x] `docs/index.html`: GitHub Pages 정적 대시보드 (JSON 직접 소비)
- [x] DB 캐시 (actions/cache): 실행 간 상태 유지로 중복 알림 방지

### Phase 8 — 텔레그램 활성화 + 이메일 버그 수정 (완료)
- [x] 텔레그램 알림 `enabled: true` 전환 (GitHub Secret TELEGRAM_BOT_TOKEN/CHAT_ID 필요)
- [x] 이메일 버그 수정 (접수 중 토글 + 발송 실패 처리 개선)

### Phase 9 — Google Calendar 연동 (완료)
- [x] `src/notifiers/calendar.py` — 서비스 계정 인증, 마감일 종일 이벤트 등록
- [x] `scheduler.py` `_add_calendar_events()` — 필터 통과 신규 공고만 캘린더 등록
- [x] 인증 우선순위: 환경변수 GOOGLE_CALENDAR_CREDENTIALS(Actions) → 로컬 JSON 파일
- [x] GitHub Secret: GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID

### Phase 10 — 필터 재설계 (완료)
- [x] 심리학 전공 맞춤 키워드 재구성 (심리, 인지, 발달, 노년, 노화, 치매, 고령, AI, 인공지능)
- [x] 제외 키워드 기능 추가 (우주, 항공, 방산, 반도체, 나노, 소재 등 20개+)
- [x] `config.yaml` `exclude_keywords` 섹션 신설
- [x] FilterEngine.matches()에 제외 키워드 우선 평가 로직 추가

### Phase 11 — 대시보드 전면 재설계 (완료)
- [x] 탭 레이아웃 (전체 공고 / 내 분야만 / 전체 요약)
- [x] 중복 공고 병합 (동일 제목 NTIS+IRIS 동시 게재 처리)
- [x] 전체 요약 탭: is_excluded 기반 소프트 필터 — 명백히 무관 분야 공고 숨김
- [x] UX 개선: 접수 중 토글, D-day 색상 배지, 관련도 표시

### Phase 12 — 알림 필터 정확도 개선 (완료, 2026-06-06)
- [x] `matches()` 동의어 확장 제거 — `search()`만 동의어 확장 유지
- [x] `config.yaml`: AI 제거(인공지능 유지), 정신건강 추가, 자율주행 exclude 추가
- [x] 검증 15/15 케이스 정확

### Phase 13 — NRF 과거 5년치 패턴 분석 (진행 중)
- [x] Phase A: 사이트 조사 — URL 파라미터 `searchRegYearDttm=YYYY`로 연도별 조회 가능 확인
- [x] Phase B: 수집 파이프라인 구축
  - DB 모델: `HistoricalAnnouncement`, `CollectionCheckpoint`
  - 스크립트: `scripts/collect_nrf_historical.py`
- [x] Phase C: 전체 수집 완료 — **1,719건** (2020-12 ~ 2026-06, 약 2분 소요)
  - posted_date/deadline 100%, category 95.3% 추출
- [x] Phase D: 분야 라벨링 (광범위 — 제목+카테고리 기반)
  - 인문사회 156건 / 심리학 10건 / 노화 7건 / 심리+AI 0건 / 미분류 1553건
  - `scripts/label_historical.py`
- [x] Phase E: 4가지 패턴 분석 (시즌성·예측·트렌드·마감일)
  - `src/analytics/historical.py` + `scripts/analyze_historical.py`
  - 인사이트: 인문사회 정점 3월(47건), 11월 0건 / 마감 중앙값 7일 / 5년 트렌드 +13건/년
- [x] Phase F: 시각화 대시보드 (`docs/analytics.html`)
  - Chart.js 기반 정적 페이지, 5개 라벨 탭 + 4개 차트
  - 메인 대시보드에서 진입 링크 제공
  - 출력: `docs/data/analytics.json`

### Phase 14 — 라벨링 보강 + 분석 고도화 (완료, 2026-06-07)
- [x] 라벨링 v2: 카테고리 직접 매핑 추가 (SSK, 학술연구교수, 인문사회연구소, 학문후속세대 등)
  - 심리학 10건 → **78건** (8배 증가), 노화 7건, 심리+AI 1건, 인문사회 156건
  - 관심 분야 합집합 188건 = 분석 모집단 (무관 1531건 제외)
- [x] "전체" 라벨 제거 → "관심 분야 통합"(interest)으로 재정의
  - 사용자가 지원하지 않을 공고는 분석 모집단에서 제외
- [x] 분석 모듈 고도화:
  - 시즌성 강도 카이제곱 검정 (강함/보통/약함)
  - 정점월±1 누적 비율
  - 반복 사업 신뢰도 (CV 기반) + 다음 공고 신뢰구간 (±1σ)
  - 연도별 단순 선형 회귀 기울기
  - 마감 기간 IQR + 7일/14일 이내 비율
  - **Top-K 사업** (라벨별 빈도 상위 사업, 신규)
- [x] 대시보드 업데이트:
  - 시즌성 강도 배지, 신뢰도 배지
  - Top 사업 막대그래프 카드 신규
  - 반복 사업 테이블에 신뢰구간 표시
  - 마감 임박(7일 이내 ≥40%) 자동 경고

### Phase 17 — 전체 재검토 후 심각 결함 3건 수정 (완료, 2026-07-03)
> 재검토 계기: "알림 메일 시스템"이라는 본래 목적 기준으로 배포 상태 검증
- [x] **리마인더 필터 누락 수정** — 성공 알림 88건 중 86건이 무관 공고 리마인더(나노소재·항공 등)였음
  - `run_reminder_cycle`에 신규 알림과 동일한 FilterEngine.matches() 적용
  - 사용자가 겪던 "불필요한 메일"의 실제 근원 (Phase 12는 신규 알림 경로만 고쳤음)
- [x] **NRF 운영 스크래퍼 0건 수정** — 한 달간 NRF 공고를 전혀 수집하지 못했음
  - 원인: 구식 `table tbody tr` 파싱 (실제 구조는 `div.public-notice-block`)
  - historical 수집기의 검증된 셀렉터·파싱 로직 이식, 접수 중/예정만 반환
  - 목록에서 category([브래킷])·접수기간 추출 → 필터가 분야 매칭 가능해짐
- [x] **Actions의 analytics.json 0건 덮어쓰기 수정** — 6월 8일 이후 배포 분석이 전부 0건이었음
  - 원인: DB가 gitignore라 NRF 5년치가 Actions에 없는데 Phase 16 로직이 분석을 재실행
  - 해결: `docs/data/historical.json` 내보내기 커밋 + 실행 시 부트스트랩 복원
  - 부수 효과: 점진 누적분의 캐시 유실 위험 제거 (git이 영구 저장소 역할)
  - 검증: 빈 DB에서 1,739건 라벨·날짜 완전 복원 확인
- [x] config.yaml keywords에 인문사회·사회과학·인문학 추가
  - categories는 category 필드에만 매칭되어 제목의 "인문사회연구소지원사업"을 못 잡던 문제
  - 검증: 무관 4건 차단 + 핵심 5건 통과 (9/9)

### Phase 16 — NTIS/IRIS 점진 누적 (완료, 2026-06-07)
- 사이트 조사 결과: NTIS/IRIS는 "실시간 R&D 매칭" 정책으로 5년치 직접 수집 불가
  - NTIS: 6가지 연도 파라미터 시도 모두 무시됨, 마감 공고 보존 안 함
  - IRIS: 5가지 파라미터 시도 모두 무시됨, 현재 진행 중 46건만
- 대안: 운영 DB → historical 자동 동기화로 매일 점진적 누적
- [x] `scripts/sync_to_historical.py` — 운영 DB의 NTIS/IRIS/NRF 공고를 historical로 이관
  - URL 기준 중복 회피, 자동 라벨링 적용
  - 즉시 실행: NTIS 10건 + IRIS 10건 이관 완료 (총 1,739건)
- [x] `scripts/run_scrape.py` 업데이트 — GitHub Actions 매 사이클마다:
  1. 스크래핑 → 2. 리마인더 → 3. JSON export → **4. historical 동기화 → 5. 분석 재실행**
- 결과: 관심 분야 통합 모집단 188 → 190건 (IRIS에서 심리 1 + 노화 1 추가)
- 장기 전망: NTIS 연 ~500건 / IRIS 연 ~200건 누적 예상 → 1년 후 본격 분석 가능

### Phase 15 — 반복 사업 재설계: Preparation Calendar (완료, 2026-06-07)
- [x] 분석 모듈 강화 (`src/analytics/historical.py`):
  - 사업별 평균 마감 기간 추출 (180일 outlier 제거)
  - 권장 준비 시작일 = 다음 공고 예상 − 평균 마감기간 − 30일 버퍼
  - 권장 시작일까지 D-day + 시급도(critical/high/medium/low)
  - 공고일 일관성 (exact/stable/loose/scattered, 월 단위 원형 표준편차)
  - 종료 추정 사업 식별 (2 사이클 이상 미공고)
  - 다음 공고 예측이 과거면 다음 사이클로 자동 보정
- [x] 신규 함수: `action_required()` — 90일 이내 권장 시작 사업 필터
- [x] 신규 함수: `monthly_calendar()` — 월별 정점 사업 매핑
- [x] 대시보드 신규 카드:
  - **🎯 지금 준비해야 할 사업** (최상단, 신호등 색상)
  - **🗓 12개월 정점 캘린더** (월별 그리드 + 일관성 색 막대)
  - 반복 사업 테이블: 평균 마감 / 준비 시작 D-day 컬럼 추가, 종료 추정 사업 회색 처리

---

## 남은 작업 (우선순위 순)

| # | 항목 | 난이도 | 메모 |
|---|------|--------|------|
| 1 | ~~categories에 "인문학", "사회과학" 추가~~ | ⭐ | ✅ 완료 (2026-05-30) |
| 2 | **이메일 알림 실패 원인 확인** | ⭐ | GitHub Actions에서 14건 `발송 실패` — Secret 설정 점검 필요 |
| 3 | **과거 데이터 패턴 분석** | ⭐⭐⭐ | 부처별 트렌드, 분야별 통계 시각화 |

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
| `.claude/launch.json` | 서버 실행 설정 |

## 현재 수집 현황 (마지막 확인: 2026-05-30)

- NTIS: 10건 (로컬 DB 기준)
- IRIS: 10건 (로컬 DB 기준)
- NRF: 0건 (현재 신규 공모 없음 — 정상)
- 총 로컬 DB: 20건 (전체 detail_fetched = True)
- GitHub Actions DB(캐시): 약 27건 (docs/data/announcements.json 기준, 2026-05-28 19:30Z)
- 알림 이력: 14건 이메일 발송 시도 → 전부 실패 (GitHub Actions Secret 미설정 추정)
- is_relevant=0: 현재 수집된 공고가 과학기술/에너지 분야 → 필터 정상, 단 관련 공고 미수집 상태

## 이메일 알림 실패 분석

- 14건 모두 `success: False, error: '발송 실패'`
- 원인 추정: GitHub Actions에서 `EMAIL_PASSWORD` Secret이 없거나 만료됨
- 확인 방법: GitHub 저장소 Settings → Secrets → `EMAIL_PASSWORD` 존재 여부 확인
- 참고: 로컬 `.env`에는 비밀번호가 설정되어 있어 로컬 실행은 정상
