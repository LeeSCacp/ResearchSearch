"""NRF 5년치 과거 공고 일회성 수집 스크립트.

검색 방식:
  - URL 파라미터 searchRegYearDttm=YYYY 로 연도별 검색
  - bizSearchRegDttmAllYn=N + 시작월 01 ~ 종료월 12 로 연단위 조회
  - pageSize=100 으로 한 페이지에 100건씩

진행 관리:
  - CollectionCheckpoint 테이블에 (source, year, page) 단위로 기록
  - 이미 completed=True인 (year, page)는 건너뜀 — 중단/재개 안전
  - HistoricalAnnouncement.url을 UNIQUE로 두어 중복 삽입 방지

실행:
  python scripts/collect_nrf_historical.py            # 2021~현재 전체
  python scripts/collect_nrf_historical.py 2023 2024  # 특정 연도만
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nrf_historical")

from src.config import load_config
from src.models.announcement import (
    init_db, get_session, HistoricalAnnouncement, CollectionCheckpoint,
)

NRF_BASE = "https://www.nrf.re.kr"
PAGE_SIZE = 100
PAGE_WAIT_MS = 1500    # 페이지 로드 후 대기
INTER_PAGE_SEC = 1.0   # 페이지 간 휴식 (서버 부담 줄이기)


# ----------------------------------------------------------------------
# URL 빌더
# ----------------------------------------------------------------------

def build_year_page_url(year: int, page: int, page_size: int = PAGE_SIZE) -> str:
    """연도/페이지 지정 NRF 사업공고 검색 URL."""
    return (
        f"{NRF_BASE}/page/362?menuNo=362&bizNo=0&bizNotGubn=guide"
        f"&pageNum={page}"
        "&searchRegChoiceDttm=M"
        "&bizSearchRegDttmAllYn=N"
        f"&searchRegYearDttm={year}"
        "&searchRegStartMonthDttm=01"
        "&searchRegEndMonthDttm=12"
        "&orderType=REG_DTTM&orderTypeAt=DESC"
        "&orderTypeBiz=BIZ_DYSC_END_DATE_ORDER&orderTypeBizAt=DESC"
        "&bizCompleteNm=&myBizCheckYn=&myDeptBizCheckYn="
        "&bizChgMbrNo=&bizChgMbrPostNm=&searchSplitBizNo="
        "&bizSearchRegDttmAllYnInput=N"
        "&bizSelectSearchRegDttm=&regStartDttm=&regEndDttm=&keyword=&bizCatNm="
        f"&pageSize={page_size}"
    )


# ----------------------------------------------------------------------
# 페이지 파싱
# ----------------------------------------------------------------------

# 공고 상태/유형 분류 키워드
NOTICE_TYPE_PATTERNS = [
    ("접수중", "접수중"),
    ("접수마감", "접수마감"),
    ("보고서제출관련공지", "보고서제출"),
    ("사업관리(기타)", "사업관리"),
    ("선정결과안내", "선정결과"),
    ("기타", "기타"),
]


def parse_block_text(text: str) -> dict:
    """공고 블록의 innerText에서 메타 정보 추출.

    Format 예시:
      "접수마감 2025년도 ... 공고 [인문사회분야 학술연구지원사업 > 개인연구군 > 인문사회학술연구교수(A유형)]
       접수일자 : 2025-07-03 00:00 ~ 2025-07-16 18:00"
    """
    text = re.sub(r'\s+', ' ', text).strip()

    # 1. 공고 유형 추출 (텍스트 맨 앞)
    notice_type = ""
    for label, normalized in NOTICE_TYPE_PATTERNS:
        if text.startswith(label) or f' {label} ' in text[:30]:
            notice_type = normalized
            text = text[len(label):].strip() if text.startswith(label) else text
            break

    # 2. D-day 마커 제거 ("D-5", "D-208")
    text = re.sub(r'^D-\d+\s*', '', text)
    # 3. NEW 마커 제거
    text = re.sub(r'\sNEW($|\s)', ' ', text)

    # 4. 카테고리 추출: [ ... ]
    category = ""
    cat_match = re.search(r'\[([^\[\]]+)\]', text)
    if cat_match:
        category = cat_match.group(1).strip()

    # 5. 접수일자 추출
    posted_date, deadline = None, None
    date_match = re.search(
        r'접수일자\s*[:：]\s*(\d{4}-\d{2}-\d{2})[^~]*~\s*(\d{4}-\d{2}-\d{2})',
        text,
    )
    if date_match:
        try:
            posted_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
            deadline    = datetime.strptime(date_match.group(2), "%Y-%m-%d").date()
        except ValueError:
            pass

    return {
        "notice_type": notice_type,
        "category":    category,
        "posted_date": posted_date,
        "deadline":    deadline,
    }


# ----------------------------------------------------------------------
# Playwright 수집
# ----------------------------------------------------------------------

async def fetch_page(playwright, year: int, page_num: int) -> list[dict]:
    """단일 (연도, 페이지)의 공고 목록을 추출."""
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    url = build_year_page_url(year, page_num)
    try:
        await page.goto(url, wait_until='networkidle', timeout=60000)
        await page.wait_for_timeout(PAGE_WAIT_MS)

        # 공고 블록 추출
        items = await page.evaluate("""() => {
            const blocks = document.querySelectorAll('div.public-notice-block');
            return Array.from(blocks).map(b => {
                const a = b.querySelector('a.title-name');
                let url = '';
                if (a) {
                    const oc = a.getAttribute('onclick') || '';
                    const m = oc.match(/[\\d]{4,}/);
                    if (m) url = m[0];
                }
                return {
                    text: (b.innerText || '').replace(/\\s+/g, ' ').trim(),
                    title: a ? (a.innerText || '').trim() : '',
                    url_token: url,
                };
            });
        }""")

        results = []
        for it in items:
            if not it['title']:
                continue
            meta = parse_block_text(it['text'])
            url_token = it['url_token']
            # 상세 URL: notiSn 기반
            ann_url = (
                f"{NRF_BASE}/biz/info/notice/view?menuNo=1&notiSn={url_token}"
                if url_token else
                # url_token 실패 시 fallback: title을 키로 사용 (URL UNIQUE 제약 위반 방지)
                f"{NRF_BASE}/biz/info/notice/list#{year}-{page_num}-{hash(it['title']) & 0xFFFFFF}"
            )
            results.append({
                "title":       it['title'],
                "url":         ann_url,
                "category":    meta['category'],
                "notice_type": meta['notice_type'],
                "posted_date": meta['posted_date'],
                "deadline":    meta['deadline'],
                "year":        year,
            })
        return results
    finally:
        await browser.close()


async def get_total_pages(playwright, year: int) -> int:
    """연도별 전체 페이지 수 조회."""
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    try:
        await page.goto(build_year_page_url(year, 1), wait_until='networkidle', timeout=60000)
        await page.wait_for_timeout(PAGE_WAIT_MS)
        body = await page.inner_text('body')
        m = re.search(r'현재\s*:\s*\d+\s*/\s*(\d+)', body)
        if m:
            return int(m.group(1))
        t = re.search(r'전체\s*([0-9,]+)\s*건', body)
        if t:
            total = int(t.group(1).replace(',', ''))
            return (total + PAGE_SIZE - 1) // PAGE_SIZE
        return 0
    finally:
        await browser.close()


# ----------------------------------------------------------------------
# DB 저장
# ----------------------------------------------------------------------

def save_items(session, items: list[dict]) -> int:
    """공고 리스트를 DB에 저장 (URL 기준 중복 회피). 신규 저장 건수 반환."""
    saved = 0
    for it in items:
        if not it.get('title') or not it.get('url'):
            continue
        existing = (
            session.query(HistoricalAnnouncement)
            .filter_by(url=it['url']).first()
        )
        if existing:
            continue
        ann = HistoricalAnnouncement(
            title       = it['title'][:2000],
            url         = it['url'],
            source      = "nrf",
            category    = it.get('category', '')[:500],
            notice_type = it.get('notice_type', ''),
            posted_date = it.get('posted_date'),
            deadline    = it.get('deadline'),
            year        = it.get('year'),
        )
        session.add(ann)
        saved += 1
    session.commit()
    return saved


def upsert_checkpoint(session, year: int, page: int,
                      completed: bool, items_count: int,
                      error: str = "") -> None:
    """체크포인트 생성/갱신."""
    cp = (
        session.query(CollectionCheckpoint)
        .filter_by(source="nrf", year=year, page=page).first()
    )
    if not cp:
        cp = CollectionCheckpoint(source="nrf", year=year, page=page)
        session.add(cp)
    cp.completed     = completed
    cp.items_count   = items_count
    cp.last_attempt  = datetime.now()
    cp.error_message = error[:500] if error else None
    session.commit()


def is_page_done(session, year: int, page: int) -> bool:
    cp = (
        session.query(CollectionCheckpoint)
        .filter_by(source="nrf", year=year, page=page, completed=True).first()
    )
    return cp is not None


# ----------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------

async def run(years: list[int], skip_done: bool = True) -> None:
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    from playwright.async_api import async_playwright

    total_new = 0
    async with async_playwright() as pw:
        for year in years:
            logger.info(f"━━━━━━ {year}년 ━━━━━━")
            try:
                total_pages = await get_total_pages(pw, year)
            except Exception as e:
                logger.error(f"  {year}년 페이지 수 조회 실패: {e}")
                continue
            if total_pages == 0:
                logger.warning(f"  {year}년 공고 없음 또는 페이지 인식 실패 — 건너뜀")
                continue
            logger.info(f"  총 {total_pages}페이지")

            for page_num in range(1, total_pages + 1):
                if skip_done and is_page_done(session, year, page_num):
                    logger.info(f"  [{year}-p{page_num}] 이미 완료 — 건너뜀")
                    continue

                try:
                    items = await fetch_page(pw, year, page_num)
                    saved = save_items(session, items)
                    total_new += saved
                    upsert_checkpoint(session, year, page_num, True, saved)
                    logger.info(
                        f"  [{year}-p{page_num}] {len(items)}건 추출 → {saved}건 신규 저장 "
                        f"(누적 신규 {total_new}건)"
                    )
                except Exception as e:
                    logger.error(f"  [{year}-p{page_num}] 실패: {e}")
                    upsert_checkpoint(session, year, page_num, False, 0, str(e))

                await asyncio.sleep(INTER_PAGE_SEC)

    session.close()
    logger.info(f"━━━━━━ 완료 — 총 신규 저장 {total_new}건 ━━━━━━")


def parse_args():
    p = argparse.ArgumentParser(description="NRF 과거 공고 수집")
    p.add_argument("years", nargs="*", type=int,
                   help="수집할 연도 목록 (생략 시 2021~현재)")
    p.add_argument("--no-skip", action="store_true",
                   help="이미 완료된 페이지도 재수집")
    return p.parse_args()


def main():
    args = parse_args()
    if args.years:
        years = sorted(set(args.years))
    else:
        current = date.today().year
        years = list(range(2021, current + 1))
    logger.info(f"수집 대상 연도: {years}")
    asyncio.run(run(years, skip_done=not args.no_skip))


if __name__ == "__main__":
    main()
