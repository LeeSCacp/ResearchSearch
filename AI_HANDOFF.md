# ResearchProjAssist AI Handoff

작성일: 2026-04-24  
대상: 다음 AI 작업자  
작업 기준 디렉토리: `D:\ResearchProjAssist`

## 1. 프로젝트 목적

이 프로젝트는 한국연구재단(NRF), NTIS, IRIS의 연구과제 공고를 자동 수집하고,  
키워드/분야 기준으로 필터링한 뒤 이메일로 알림을 보내는 시스템이다.  
텔레그램 알림은 코드가 있으나 현재 비활성화 상태다.

기본 스택:

- Python 3.14
- FastAPI
- SQLite + SQLAlchemy
- APScheduler
- Playwright
- httpx + BeautifulSoup

대시보드:

- `http://localhost:8000`

실행 진입점:

- `python src/main.py`
- 또는 `python -m src.main`

## 2. 현재 구현 상태 요약

현재 파이프라인은 아래 흐름으로 동작한다.

1. `src/main.py`
   FastAPI 앱 시작, DB 초기화, APScheduler 등록, 시작 직후 안전한 1회 스크래핑 실행
2. `src/scheduler.py`
   전체 스크래핑 -> 신규 공고 저장 -> 필터 적용 -> 알림 발송 -> `is_notified=True`
3. `src/scrapers/*`
   NRF / NTIS / IRIS 공고 수집
4. `src/filters/engine.py`
   동의어 확장 검색 + 관련도 점수 계산
5. `src/notifiers/*`
   이메일 / 텔레그램 알림
6. `src/web/app.py`
   대시보드, JSON API, 수동 스크래핑 API

## 3. 파일별 핵심 역할

### `src/main.py`

- FastAPI lifespan에서 DB 초기화
- APScheduler에 주기 작업 등록
- 시작 직후 `run_scraping_cycle()`를 안전하게 1회 실행

주의:

- `uvicorn.run(..., reload=True)`가 남아 있다.
- 프로젝트 메모에는 Windows `--reload` 문제를 제거했다고 적혀 있으므로 현재 코드와 메모가 완전히 일치하지 않는다.

### `src/scheduler.py`

핵심 배치 로직이 있다.

- `_scrape_all()`
  활성화된 스크래퍼들을 병렬 실행
- `_save_new_items()`
  URL 기준 중복 제거 후 신규 공고만 저장
- `FilterEngine`
  신규 공고만 대상으로 필터 적용
- `_send_notifications()`
  이메일/텔레그램 발송

현재 한계:

- 알림 상태가 `Announcement.is_notified` 하나뿐이다.
- 즉, "신규 공고 1회 알림"만 추적 가능하고 `D-7 / D-3 / D-1` 리마인더 이력은 저장할 수 없다.

### `src/models/announcement.py`

`Announcement` 모델:

- `title`
- `url` (unique)
- `source`
- `category`
- `deadline`
- `posted_date`
- `description`
- `is_notified`
- `created_at`

추가로 `Filter` 모델이 있으나 현재 코드 흐름에서 실사용 흔적이 없다.

### `src/scrapers/base.py`

- `AnnouncementData` DTO
- `BaseScraper` 추상 클래스

### `src/scrapers/ntis.py`

- `httpx + BeautifulSoup`
- `table.basic_list` 파싱
- 현재 세 스크래퍼 중 가장 단순하고 안정적인 구조

### `src/scrapers/iris.py`

- Playwright 사용
- `onclick`에서 사업 ID를 추출해 상세 URL 구성

현재 한계:

- `posted_date`와 `category`만 사실상 수집
- `deadline`, `description`, 상세 데이터는 수집하지 않음

### `src/scrapers/nrf.py`

- Playwright 사용
- SPA 페이지에서 JS로 검색 기간을 조작한 뒤 목록 탐색

현재 한계:

- 전체 `a` 태그를 훑고 휴리스틱으로 공고를 판별
- 선택자 안정성이 낮고 사이트 구조 변경에 취약
- 상세 정보 수집은 거의 없음

### `src/filters/engine.py`

현재 프로젝트에서 중요한 구현 중 하나다.

- `synonyms.yaml` 기반 동의어 확장
- 제목/설명/분야를 합친 텍스트에서 매칭
- `score DESC -> direct_score DESC -> deadline ASC` 정렬

중요한 동작:

- `matches()`는 키워드와 카테고리를 모두 OR 조건으로 처리한다.
- 즉 "키워드 불일치 + 카테고리 일치"도 통과한다.

### `src/notifiers/email.py`

- SMTP 기반 이메일 발송
- `test_connection()` / `send_test()` 존재
- HTML 본문에 D-day 색상 표시 있음

현재 의미:

- 본문 렌더링은 이미 D-day 개념을 사용하고 있음
- 따라서 실제 리마인더 기능 추가 시 템플릿 재사용은 쉽다

### `src/notifiers/telegram.py`

- 구현 자체는 존재
- `.env` 기반 설정 주입 지원
- 현재 `config/config.yaml`에서 비활성화

### `src/web/app.py`

제공 기능:

- `/` 대시보드
- `/api/announcements`
- `/api/synonyms`
- `/api/scrape`

현재 구조:

- DB에서 목록을 읽어온 뒤 Python 레벨에서 검색/정렬/페이지네이션 처리
- `FilterEngine.search()` 결과를 템플릿에 직접 전달

### `src/web/templates/index.html`

- 검색 UI
- 출처 필터
- 관련도 배지
- 마감일 D-day 색상 표시
- 수동 스크래핑 버튼

참고:

- `/api/synonyms` 엔드포인트는 있으나 현재 템플릿에서 실시간 호출해 자동완성처럼 쓰지는 않는다.
- 서버가 렌더링한 힌트만 보여준다.

## 4. 현재 확인된 강점

- 구조가 단순해서 다음 작업자가 이해하기 쉽다.
- 스크래핑, 저장, 필터, 알림, 웹이 모듈별로 분리돼 있다.
- 신규 공고 저장 기준이 URL unique라 중복 적재 방지는 기본적으로 된다.
- 동의어 기반 필터 엔진이 이미 있어서 검색/알림 품질 기반은 갖춰져 있다.
- 이메일 테스트 메서드가 있어 운영 점검이 쉽다.

## 5. 현재 확인된 개선점

### 우선순위 높음

1. 마감일 리마인더 상태 저장 구조가 없다.
   현재 모델만으로는 `D-7 / D-3 / D-1` 중 어느 알림을 이미 보냈는지 관리할 수 없다.

2. 상세 정보 수집이 부족하다.
   `description` 필드는 거의 비어 있을 가능성이 높고, 사업비/지원자격/PDF 링크 등은 미수집 상태다.
   필터 정확도도 제목/분야에 치우친다.

3. 스크래퍼 안정성 편차가 있다.
   NTIS는 상대적으로 안정적이지만, NRF/IRIS는 DOM 구조 변경에 더 취약하다.

4. 테스트 범위가 얕다.
   현재 테스트는 주로 구조/순수 함수 레벨이다.
   실제 HTML 구조 변화, DB 저장 흐름, 스케줄러 통합 흐름은 강하게 검증하지 않는다.

### 우선순위 중간

5. `Filter` 모델이 사실상 미사용이다.
   유지할지 제거할지 결정이 필요하다.

6. 실행 문서와 실제 코드 간 불일치 가능성이 있다.
   `reload=True` 잔존 여부, 실행 명령, 메모 내용이 완전히 정렬돼 있지 않다.

7. 검색이 DB 쿼리 레벨이 아니라 Python 메모리 레벨이다.
   현재 데이터 규모에서는 문제 없지만, 데이터가 늘면 비효율적일 수 있다.

8. 텔레그램은 기능은 있으나 운영 연결이 안 되어 있다.

### 우선순위 낮음

9. 웹 UI는 기능적으로 충분하지만 관리 기능은 아직 제한적이다.
   예: 알림 이력 조회, 필터 편집, 소스별 수집 상태 표시 등

10. 현재 작업 환경에서 한글이 깨져 보이는 경우가 있었다.
    파일 인코딩 자체 문제인지, PowerShell 콘솔 출력 디코딩 문제인지는 별도 확인이 필요하다.
    대규모 텍스트 수정 전 UTF-8 표시 상태를 먼저 점검하는 것이 안전하다.

## 6. 권장 작업 순서

### 1순위: 마감일 D-day 리마인더

가장 자연스러운 다음 작업이다.

권장 방향:

- `Announcement`에 리마인더 상태 컬럼 추가
- 또는 별도 `NotificationLog` / `ReminderLog` 테이블 추가
- 스케줄러에서 "신규 공고 알림"과 "마감 임박 리마인더"를 분리
- 기준:
  - D-7
  - D-3
  - D-1
- 각 단계별 중복 발송 방지 로직 필요

개인적으로는 컬럼 추가보다 별도 로그 테이블이 더 낫다.
이유:

- 신규 공고 알림과 리마인더는 다른 이벤트다.
- 향후 이메일/텔레그램/캘린더 등 채널별 이력 확장에 유리하다.
- 재시도/실패 추적도 가능해진다.

### 2순위: 텔레그램 연결

작업량이 작고 즉시 가치가 있다.

필요 작업:

- `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 입력
- `config/config.yaml`에서 telegram enabled 전환
- 테스트 스크립트 또는 API 경로 추가 검토

### 3순위: 상세 정보 수집

이 작업이 들어가면 필터 품질이 올라간다.

수집 후보:

- 상세 설명
- 지원 자격
- 사업비/규모
- 공고 번호
- 첨부 PDF 링크

추천 방식:

- 기존 list scrape 후 detail scrape를 선택적으로 추가
- 사이트별로 DTO를 확장하되, 공통 필드는 `Announcement`로 수렴

### 4순위: Google Calendar 연동

리마인더 이후에 붙이는 것이 자연스럽다.

이유:

- 마감일 데이터와 알림 로직이 먼저 안정화되어야 한다.
- 리마인더에서 정제한 마감일 기준을 그대로 캘린더 생성에 재사용할 수 있다.

### 5순위: 과거 데이터 분석

지금도 가능은 하지만 우선순위는 낮다.
수집 데이터 밀도가 더 높아진 뒤 하는 편이 유의미하다.

## 7. 리마인더 기능 설계 초안

다음 AI가 바로 설계/구현에 들어갈 수 있도록 초안을 남긴다.

### 최소 구현안

`Announcement`에 컬럼 추가:

- `notified_new_at`
- `reminded_d7_at`
- `reminded_d3_at`
- `reminded_d1_at`

장점:

- 구현이 빠르다.

단점:

- 채널별 이력 분리 어려움
- 알림 실패/재시도 추적 한계

### 권장 구현안

별도 테이블 예시:

- `NotificationLog`
  - `id`
  - `announcement_id`
  - `channel` (`email`, `telegram`)
  - `event_type` (`new`, `d7`, `d3`, `d1`)
  - `sent_at`
  - `success`
  - `error_message`

권장 흐름:

1. 신규 공고 저장
2. 신규 공고 필터 적용 후 `event_type="new"` 발송
3. 별도 함수에서 DB 전체 또는 최근 데이터 중 마감일 임박 대상 조회
4. 아직 같은 `event_type` 로그가 없는 항목만 발송

## 8. 테스트 관련 메모

현재 `tests/`에는 다음이 있다.

- `test_filters.py`
- `test_notifiers.py`
- `test_scrapers.py`

의미:

- 순수 로직/구조 확인에는 도움 된다.
- 그러나 실제 운영 리스크를 막기엔 부족하다.

추가 권장 테스트:

1. `scheduler.run_scraping_cycle()` 통합 테스트
2. URL 중복 저장 방지 테스트
3. 리마인더 중복 발송 방지 테스트
4. 공고 마감일 경계값 테스트
   - D-7
   - D-3
   - D-1
   - 마감일 당일
   - 이미 마감된 항목
5. 스크래퍼 샘플 HTML fixture 테스트

## 9. 설정 및 운영 관련 메모

### `config/config.yaml`

현재 확인된 주요 설정:

- 스크래핑 주기: 6시간
- 사이트: NRF / NTIS / IRIS 모두 활성
- 이메일 알림: 활성
- 텔레그램 알림: 비활성

### `.env`

민감정보가 들어 있으므로 다음 AI는 값 자체를 노출하지 말 것.

이미 지원되는 환경변수:

- `EMAIL_SENDER`
- `EMAIL_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 10. 현재 코드 기준으로 확인한 불일치/주의 포인트

1. 프로젝트 메모에는 테스트 25개 통과라고 되어 있으나, 실제 테스트 개수는 현재 파일만 보면 그보다 적어 보인다.
   정확한 수치는 실제 `pytest` 실행으로 재확인 필요.

2. 프로젝트 메모에는 Windows reload 이슈를 제거했다고 적혀 있으나, `src/main.py`에는 `reload=True`가 남아 있다.

3. README, AGENTS 메모, 실제 코드 간 설명이 일부 어긋날 수 있으므로 문서 정리가 필요하다.

4. 현재 콘솔에서는 한글이 깨져 보이는 출력이 있었다.
   편집 전 `UTF-8` 저장/출력 상태를 확인하는 것이 안전하다.

## 11. 다음 AI에게 권장하는 첫 액션

가장 먼저 할 일:

1. `python -m pytest tests -q` 실행으로 현재 테스트 상태 확인
2. `python -m src.main` 또는 기존 런치 설정으로 서버 기동 확인
3. DB 스키마와 실제 `announcements` 데이터 샘플 확인
4. 리마인더 구현 방향을 "컬럼 추가" vs "로그 테이블 추가" 중 하나로 확정

내 권장:

- 빠른 구현이 목표면 컬럼 추가
- 이후 확장성과 운영 추적까지 고려하면 로그 테이블 추가

## 12. 요약

현재 프로젝트는 기본 기능이 이미 완성도 있게 연결되어 있다.  
가장 큰 빈칸은 "마감일 기반 리마인더 상태 관리"다.  
다음 작업자는 이 기능을 중심으로 데이터 모델과 스케줄러를 확장하면 된다.  
그 다음 우선순위는 텔레그램 활성화와 상세 정보 수집이다.
