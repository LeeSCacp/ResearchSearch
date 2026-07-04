"""한국연구재단(NRF) 사업공고 스크래퍼.

NRF 사업공고 페이지(/page/362)를 URL 파라미터 방식으로 직접 조회한다.
과거 5년치 수집기(scripts/collect_nrf_historical.py)에서 검증된
`div.public-notice-block` 구조를 동일하게 사용한다.

수집 정책 (운영 알림용):
  - 최신 등록순 상위 2페이지(200건)를 읽어
  - "접수중"/"접수예정" 상태이거나 마감일이 아직 남은 공고만 반환
  - 목록에서 category([브래킷])와 접수기간(시작~마감)까지 추출
    → 필터 엔진이 인문사회 등 분야 카테고리로 매칭 가능
"""

import re
from datetime import datetime, date

from src.scrapers.base import BaseScraper, AnnouncementData

NRF_NOTICE_URL = "https://www.nrf.re.kr/biz/info/notice/list"
NRF_BASE_URL   = "https://www.nrf.re.kr"

# 운영 수집: 최신 등록순 상위 N페이지 (100건/페이지)
_LIST_PAGES = 2

# 공고 유형 라벨 (블록 텍스트 맨 앞에 등장)
_NOTICE_TYPES = [
    "접수중", "접수예정", "접수마감",
    "보고서제출관련공지", "사업관리(기타)", "선정결과안내", "기타",
]


def _build_list_url(page_num: int, page_size: int = 100) -> str:
    """전체 기간 + 최신 등록순 목록 URL (historical 수집기와 동일 구조)."""
    return (
        f"{NRF_BASE_URL}/page/362?menuNo=362&bizNo=0&bizNotGubn=guide"
        f"&pageNum={page_num}"
        "&searchRegChoiceDttm=M"
        "&bizSearchRegDttmAllYn=Y"
        "&searchRegYearDttm=&searchRegStartMonthDttm=&searchRegEndMonthDttm="
        "&orderType=REG_DTTM&orderTypeAt=DESC"
        "&orderTypeBiz=BIZ_DYSC_END_DATE_ORDER&orderTypeBizAt=DESC"
        "&bizCompleteNm=&myBizCheckYn=&myDeptBizCheckYn="
        "&bizChgMbrNo=&bizChgMbrPostNm=&searchSplitBizNo="
        "&bizSearchRegDttmAllYnInput=Y"
        "&bizSelectSearchRegDttm=&regStartDttm=&regEndDttm=&keyword=&bizCatNm="
        f"&pageSize={page_size}"
    )


class NRFScraper(BaseScraper):
    source_name = "nrf"

    # ------------------------------------------------------------------
    # 목록 수집
    # ------------------------------------------------------------------

    async def scrape(self) -> list[AnnouncementData]:
        results: list[AnnouncementData] = []
        try:
            results = await self._scrape_list()
        except Exception as e:
            self.log_error(f"목록 수집 실패: {e}")
        self.log_info(f"{len(results)}건 수집 완료 (접수 중/예정만)")
        return results

    async def _scrape_list(self) -> list[AnnouncementData]:
        from playwright.async_api import async_playwright

        raw_blocks: list[dict] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            for page_num in range(1, _LIST_PAGES + 1):
                await page.goto(_build_list_url(page_num),
                                wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(1500)
                blocks = await page.evaluate("""() => {
                    const blocks = document.querySelectorAll('div.public-notice-block');
                    return Array.from(blocks).map(b => {
                        const a = b.querySelector('a.title-name');
                        let token = '';
                        if (a) {
                            const oc = a.getAttribute('onclick') || '';
                            const m = oc.match(/[\\d]{4,}/);
                            if (m) token = m[0];
                        }
                        return {
                            text: (b.innerText || '').replace(/\\s+/g, ' ').trim(),
                            title: a ? (a.innerText || '').trim() : '',
                            token: token,
                        };
                    });
                }""")
                if not blocks:
                    self.log_warning(f"p{page_num}: public-notice-block 미발견")
                    break
                raw_blocks.extend(blocks)
            await browser.close()

        today = date.today()
        results: list[AnnouncementData] = []
        for blk in raw_blocks:
            if not blk["title"]:
                continue
            ann = self._parse_block(blk["text"], blk["title"], blk["token"])
            if ann is None:
                continue
            # 접수 중/예정이거나 마감일이 남은 공고만 알림 대상
            is_open = (
                blk["text"].startswith(("접수중", "접수예정"))
                or (ann.deadline is not None and ann.deadline >= today)
            )
            if is_open:
                results.append(ann)
        return results

    def _parse_block(self, text: str, title: str, token: str) -> AnnouncementData | None:
        """공고 블록 텍스트에서 메타 정보 추출 (historical 수집기와 동일 로직)."""
        t = re.sub(r"\s+", " ", text).strip()

        # D-day / NEW 마커 제거
        t = re.sub(r"^D-\d+\s*", "", t)
        t = re.sub(r"\sNEW($|\s)", " ", t)

        # 카테고리: [ ... ]
        category = ""
        cat_match = re.search(r"\[([^\[\]]+)\]", t)
        if cat_match:
            category = cat_match.group(1).strip()

        # 접수일자: YYYY-MM-DD ~ YYYY-MM-DD
        posted_date, deadline = None, None
        date_match = re.search(
            r"접수일자\s*[:：]\s*(\d{4}-\d{2}-\d{2})[^~]*~\s*(\d{4}-\d{2}-\d{2})", t
        )
        if date_match:
            try:
                posted_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
                deadline    = datetime.strptime(date_match.group(2), "%Y-%m-%d").date()
            except ValueError:
                pass

        url = (
            f"{NRF_BASE_URL}/biz/info/notice/view?menuNo=1&notiSn={token}"
            if token else NRF_NOTICE_URL
        )

        return AnnouncementData(
            title=title, url=url, source="nrf",
            category=category, posted_date=posted_date, deadline=deadline,
        )

    # ------------------------------------------------------------------
    # 상세 수집
    # ------------------------------------------------------------------

    async def scrape_detail(self, item: AnnouncementData) -> AnnouncementData:
        """NRF 상세 페이지에서 description, category, budget, attachments 수집."""
        # 목록 URL과 동일하면 상세 정보를 가져올 수 없음
        if item.url == NRF_NOTICE_URL:
            return item

        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page    = await browser.new_page()
                await page.goto(item.url, wait_until="networkidle", timeout=40000)
                content   = await page.content()
                body_text = await page.inner_text("body")
                await browser.close()

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "lxml")

            item.description = self._extract_description(soup, body_text)
            item.budget      = self._extract_budget(soup, body_text)
            item.attachments = self._extract_attachments(soup)

            # 카테고리 보완
            if not item.category:
                item.category = self._extract_category(soup, body_text)

            # 마감일 보완
            if not item.deadline:
                item.deadline = self._extract_deadline(soup, body_text)

        except Exception as e:
            self.log_error(f"상세 수집 실패 [{item.title[:30]}]: {e}")

        return item

    @staticmethod
    def _extract_description(soup, body_text: str) -> str:
        keywords = ["사업목적", "사업개요", "지원목적", "사업내용", "개요", "목적"]
        for th in soup.find_all("th"):
            if any(k in th.get_text() for k in keywords):
                td = th.find_next_sibling("td")
                if td:
                    return td.get_text(separator=" ", strip=True)[:1000]
        for dt in soup.find_all("dt"):
            if any(k in dt.get_text() for k in keywords):
                dd = dt.find_next_sibling("dd")
                if dd:
                    return dd.get_text(separator=" ", strip=True)[:1000]

        # 본문에서 키워드 이후 텍스트 추출
        for kw in keywords:
            idx = body_text.find(kw)
            if idx != -1:
                snippet = body_text[idx + len(kw):idx + len(kw) + 500].strip()
                snippet = re.sub(r"\s+", " ", snippet)
                if len(snippet) > 50:
                    return snippet
        return ""

    @staticmethod
    def _extract_budget(soup, body_text: str) -> str:
        keywords = ["지원규모", "사업비", "지원금액", "총사업비", "예산"]
        for th in soup.find_all("th"):
            if any(k in th.get_text() for k in keywords):
                td = th.find_next_sibling("td")
                if td:
                    return td.get_text(separator=" ", strip=True)[:300]
        for kw in keywords:
            idx = body_text.find(kw)
            if idx != -1:
                snippet = body_text[idx:idx + 100]
                snippet = re.sub(r"\s+", " ", snippet).strip()
                if any(c in snippet for c in ["원", "억", "천만"]):
                    return snippet[:200]
        return ""

    @staticmethod
    def _extract_deadline(soup, body_text: str) -> date | None:
        keywords = ["마감일", "접수마감", "신청마감", "공모마감"]
        for th in soup.find_all("th"):
            if any(k in th.get_text() for k in keywords):
                td = th.find_next_sibling("td")
                if td:
                    m = re.search(r"(\d{4}[-./]\d{2}[-./]\d{2})", td.get_text())
                    if m:
                        t = m.group(1).replace(".", "-").replace("/", "-")
                        try:
                            return datetime.strptime(t, "%Y-%m-%d").date()
                        except ValueError:
                            pass
        return None

    @staticmethod
    def _extract_category(soup, body_text: str) -> str:
        keywords = ["사업분야", "연구분야", "지원분야", "학문분야"]
        for th in soup.find_all("th"):
            if any(k in th.get_text() for k in keywords):
                td = th.find_next_sibling("td")
                if td:
                    return td.get_text(separator=" ", strip=True)[:100]
        return ""

    @staticmethod
    def _extract_attachments(soup) -> str:
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            name = a.get_text(strip=True)
            if any(ext in href.lower() for ext in [".pdf", ".hwp", ".docx", ".xlsx", "download", "fileDown"]):
                base = NRF_BASE_URL if not href.startswith("http") else ""
                links.append(f"{name}|{base + href}")
        return "\n".join(links[:10])

    @staticmethod
    def _parse_date(text: str) -> date | None:
        text = text.strip().replace("/", "-").replace(".", "-")
        for fmt in ("%Y-%m-%d", "%y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None
