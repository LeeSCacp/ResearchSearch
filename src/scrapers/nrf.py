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
            await page.evaluate("() => { var btn = document.querySelector('.searchBizQueryBtn'); if (btn) btn.click(); }")
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

            links = await page.query_selector_all("a")
            for link in links:
                try:
                    ann = await self._parse_link(link)
                    if ann:
                        results.append(ann)
                except Exception as e:
                    self.log_error(f"항목 파싱 실패: {e}")

            await browser.close()
        return results

    async def _parse_link(self, link) -> AnnouncementData | None:
        href  = await link.get_attribute("href") or ""
        title = (await link.inner_text()).strip()

        if len(title) < 15:
            return None
        skip = ["로그인", "회원가입", "마이페이지", "HOME", "검색", "FAQ"]
        if any(k in title for k in skip):
            return None

        url = NRF_NOTICE_URL
        if href and href.startswith("http"):
            url = href
        elif href and not href.startswith("javascript") and not href.startswith("#"):
            url = NRF_BASE_URL + href

        parent = await link.evaluate_handle(
            'el => el.closest("li") || el.closest("tr") || el.parentElement'
        )
        parent_text = (await parent.as_element().inner_text()).strip()
        dates = re.findall(r"(\d{4}[-./]\d{2}[-./]\d{2})", parent_text)

        posted_date = self._parse_date(dates[0]) if dates else None
        deadline    = self._parse_date(dates[1]) if len(dates) > 1 else None

        return AnnouncementData(
            title=title, url=url, source="nrf",
            posted_date=posted_date, deadline=deadline,
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
