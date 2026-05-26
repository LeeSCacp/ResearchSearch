"""한국연구재단(NRF) 사업공고 스크래퍼.

NRF 사이트(nrf.re.kr)는 SPA 기반으로, Playwright를 사용하여
사업공고 목록을 수집한다. 검색 기간을 6개월로 설정한 뒤 조회한다.

페이지: /biz/info/notice/list
검색 조건:
  - bizSelectSearchRegDttm: '6M' (최근 6개월)
  - searchBizQueryBtn 클릭으로 검색 실행
"""

import re
from datetime import datetime, date

from src.scrapers.base import BaseScraper, AnnouncementData

NRF_NOTICE_URL = "https://www.nrf.re.kr/biz/info/notice/list"
NRF_BASE_URL   = "https://www.nrf.re.kr"


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
        self.log_info(f"{len(results)}건 수집 완료")
        return results

    async def _scrape_list(self) -> list[AnnouncementData]:
        from playwright.async_api import async_playwright
        from bs4 import BeautifulSoup

        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page    = await browser.new_page()
            await page.goto(NRF_NOTICE_URL, wait_until="networkidle", timeout=60000)

            # 검색 기간 '최근 6개월' 설정
            await page.evaluate("""() => {
                var sel = document.querySelector('select[name=bizSelectSearchRegDttm]');
                if (sel) {
                    sel.value = '6M';
                    sel.dispatchEvent(new Event('change'));
                    if (typeof fnBizSelectSearchRegDttm === 'function')
                        fnBizSelectSearchRegDttm(sel);
                }
            }""")
            await page.wait_for_timeout(1500)
            await page.evaluate("""() => {
                var btn = document.querySelector('.searchBizQueryBtn');
                if (btn) btn.click();
            }""")
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle")

            body_text = await page.inner_text("body")
            total_match = re.search(r"전체\s*(\d+)\s*건", body_text)
            total = int(total_match.group(1)) if total_match else 0

            if total == 0:
                self.log_info("현재 시점에 NRF 신규 사업공모가 없습니다")
                await browser.close()
                return results

            self.log_info(f"NRF 검색 결과: {total}건")

            # HTML 파싱으로 공고 목록 추출 (SPA 대응)
            content = await page.content()
            await browser.close()

        soup = BeautifulSoup(content, "lxml")
        results = self._parse_list_html(soup)
        self.log_info(f"목록 파싱: {len(results)}건 추출")
        return results

    def _parse_list_html(self, soup) -> list[AnnouncementData]:
        """BeautifulSoup으로 NRF 공고 목록 파싱."""
        results = []

        # NRF 공고 목록: table 또는 ul 기반 구조 탐색
        # 우선 table > tbody > tr 시도
        for tr in soup.select("table tbody tr"):
            ann = self._parse_row(tr)
            if ann:
                results.append(ann)

        # table 방식으로 수집 못 한 경우 li 기반 시도
        if not results:
            for li in soup.select("ul.board-list li, ul.list-type li, .bbs-list li"):
                ann = self._parse_li(li)
                if ann:
                    results.append(ann)

        return results

    def _parse_row(self, tr) -> AnnouncementData | None:
        """table tr 행에서 공고 정보 추출."""
        cells = tr.find_all("td")
        if len(cells) < 2:
            return None

        # 제목 셀에서 링크 찾기
        title_td = None
        for td in cells:
            a = td.find("a")
            if a and len(a.get_text(strip=True)) > 15:
                title_td = td
                break
        if not title_td:
            return None

        a_tag = title_td.find("a")
        title = a_tag.get_text(strip=True)
        url   = self._extract_url_from_tag(a_tag)

        # 행 전체 텍스트에서 날짜 추출
        row_text = tr.get_text()
        dates = re.findall(r"(\d{4}[-./]\d{2}[-./]\d{2})", row_text)

        return AnnouncementData(
            title=title, url=url, source="nrf",
            posted_date=self._parse_date(dates[0]) if dates else None,
            deadline=self._parse_date(dates[-1]) if len(dates) > 1 else None,
        )

    def _parse_li(self, li) -> AnnouncementData | None:
        """list item에서 공고 정보 추출."""
        a_tag = li.find("a")
        if not a_tag:
            return None
        title = a_tag.get_text(strip=True)
        if len(title) < 15:
            return None

        url     = self._extract_url_from_tag(a_tag)
        li_text = li.get_text()
        dates   = re.findall(r"(\d{4}[-./]\d{2}[-./]\d{2})", li_text)

        return AnnouncementData(
            title=title, url=url, source="nrf",
            posted_date=self._parse_date(dates[0]) if dates else None,
            deadline=self._parse_date(dates[-1]) if len(dates) > 1 else None,
        )

    def _extract_url_from_tag(self, a_tag) -> str:
        """<a> 태그에서 URL 추출. SPA onclick/href 패턴 모두 처리."""
        href    = (a_tag.get("href") or "").strip()
        onclick = (a_tag.get("onclick") or "").strip()

        # 1. href가 실제 경로인 경우
        if href and not href.startswith(("javascript", "#")):
            return href if href.startswith("http") else NRF_BASE_URL + href

        # 2. onclick 또는 href의 javascript: 에서 notiSn / ID 추출
        #    패턴 예: goView('12345'), fn_detail(12345), location.href='...notiSn=12345'
        for text in (onclick, href):
            # URL 내 notiSn 파라미터
            m = re.search(r"notiSn[='\"\s,]+(\d+)", text)
            if m:
                return f"{NRF_BASE_URL}/biz/info/notice/view?menuNo=1&notiSn={m.group(1)}"
            # 함수 호출 첫 번째 숫자 인자
            m = re.search(r"\(\s*['\"]?(\d{4,})['\"]?", text)
            if m:
                return f"{NRF_BASE_URL}/biz/info/notice/view?menuNo=1&notiSn={m.group(1)}"

        # 3. 추출 실패 → 목록 URL (상세 수집 불가)
        self.log_warning(f"URL 추출 실패 (href={href!r}, onclick={onclick!r})")
        return NRF_NOTICE_URL

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
