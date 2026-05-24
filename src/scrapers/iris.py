"""IRIS(연구사업통합관리시스템) 공고 스크래퍼.

IRIS 사업공고 현황 페이지에서 공고 목록을 수집한다.
Playwright로 동적 로딩된 공고 리스트를 파싱.

공고 항목 구조 (2026-04 기준):
  - a[onclick*="f_bsnsAncmBtinSituListForm_view('공고ID','상태')"]
  - 부모 li 내부 텍스트:
    Line 0: 부처명 > 전문기관명
    Line 1: 공고 제목 (= a 태그 텍스트)
    Line 2: 공고번호
    Line 3: 공고일자 :YYYY-MM-DD
    Line 4: 접수상태
    Line 5: 연구분야
"""

import re
from datetime import datetime, date

from src.scrapers.base import BaseScraper, AnnouncementData

IRIS_LIST_URL   = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"
IRIS_BASE_URL   = "https://www.iris.go.kr"
IRIS_DETAIL_URL = IRIS_BASE_URL + "/contents/retrieveBsnsAncmBtinSituDetailView.do"


class IRISScraper(BaseScraper):
    source_name = "iris"

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
            page = await browser.new_page()
            await page.goto(IRIS_LIST_URL, wait_until="networkidle", timeout=60000)
            items = await page.query_selector_all(
                '[onclick*="f_bsnsAncmBtinSituListForm_view"]'
            )
            for item in items:
                try:
                    ann = await self._parse_item(item)
                    if ann:
                        results.append(ann)
                except Exception as e:
                    self.log_error(f"항목 파싱 실패: {e}")
            await browser.close()
        return results

    async def _parse_item(self, item) -> AnnouncementData | None:
        title = (await item.inner_text()).strip()
        if not title or len(title) < 3:
            return None

        onclick = await item.get_attribute("onclick") or ""
        match = re.search(r"f_bsnsAncmBtinSituListForm_view\('(\w+)'", onclick)
        if not match:
            return None

        bsns_id = match.group(1)
        url = f"{IRIS_DETAIL_URL}?bsnsSn={bsns_id}"

        parent = await item.evaluate_handle(
            'el => el.closest("li") || el.parentElement.parentElement'
        )
        parent_text = (await parent.as_element().inner_text()).strip()
        lines = [l.strip() for l in parent_text.split("\n") if l.strip()]

        category    = ""
        posted_date = None
        dept        = ""

        for line in lines:
            if "공고일자" in line or "공고일" in line:
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                if date_match:
                    posted_date = self._parse_date(date_match.group(1))
            elif "연구분야" in line or "분야" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    category = parts[-1].strip()
            elif ">" in line and not line.startswith("공고"):
                dept = line.strip()

        if not category and dept:
            category = dept

        return AnnouncementData(
            title=title, url=url, source="iris",
            category=category, posted_date=posted_date,
        )

    # ------------------------------------------------------------------
    # 상세 수집 (Playwright)
    # ------------------------------------------------------------------

    async def scrape_detail(self, item: AnnouncementData) -> AnnouncementData:
        """IRIS 상세 페이지에서 description, deadline, budget, attachments 수집.

        IRIS는 GET 방식으로 상세 URL에 직접 접근 시 404 또는 main.do로 리다이렉트된다.
        목록 페이지의 기존 폼(bsnsAncmBtinSituListForm)을 JS로 조작해
        ancmId를 설정하고 POST 제출하는 방식으로 상세 페이지에 진입한다.
        """
        # URL에서 ancmId 추출 (기존 URL에 bsnsSn 파라미터로 저장됨)
        m = re.search(r"bsnsSn=(\w+)", item.url)
        if not m:
            self.log_error(f"ancmId 추출 실패: {item.url}")
            return item
        ancm_id = m.group(1)

        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page    = await browser.new_page()

                # 1. 목록 페이지 로드 (폼 세션/쿠키 획득)
                await page.goto(IRIS_LIST_URL, wait_until="networkidle", timeout=60000)

                # 2. 기존 폼(bsnsAncmBtinSituListForm)의 ancmId를 설정하고 제출
                form_ok = await page.evaluate(f"""() => {{
                    const form = document.getElementById('bsnsAncmBtinSituListForm');
                    if (!form) return false;
                    const inp = form.querySelector('input[name="ancmId"]');
                    if (inp) inp.value = '{ancm_id}';
                    form.action = '/contents/retrieveBsnsAncmView.do';
                    form.submit();
                    return true;
                }}""")

                if not form_ok:
                    self.log_error(f"IRIS 폼 미발견: {item.title[:30]}")
                    await browser.close()
                    return item

                # 3. 상세 페이지 URL 전환 대기
                await page.wait_for_url("**/retrieveBsnsAncmView.do**", timeout=15000)
                await page.wait_for_timeout(1000)

                content   = await page.content()
                body_text = await page.inner_text("body")
                await browser.close()

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "lxml")

            item.description = self._extract_description(soup)
            item.deadline    = item.deadline or self._extract_deadline(soup)
            item.budget      = self._extract_budget(body_text)
            item.attachments = self._extract_attachments(body_text)

        except Exception as e:
            self.log_error(f"상세 수집 실패 [{item.title[:30]}]: {e}")

        return item

    @staticmethod
    def _extract_description(soup) -> str:
        """공고문 본문: div.se-contents (IRIS/NTIS 공통 에디터 영역)."""
        sc = soup.find("div", class_="se-contents")
        if sc:
            text = sc.get_text(separator=" ", strip=True)
            if len(text) > 10:
                return text[:1000]
        # fallback: tb_contents 전체
        tb = soup.find("div", class_="tb_contents")
        if tb:
            text = tb.get_text(separator=" ", strip=True)
            if len(text) > 10:
                return text[:1000]
        return ""

    @staticmethod
    def _extract_deadline(soup) -> date | None:
        """접수기간 필드에서 마감일 추출.

        IRIS 상세 페이지 구조:
          ul.list_dot > li.write > strong[접수기간] + span[YYYY-MM-DD ~ YYYY-MM-DD]
        마감일 = 기간의 마지막 날짜.
        """
        for li in soup.find_all("li", class_="write"):
            strong = li.find("strong")
            if strong and "접수기간" in strong.get_text():
                span = li.find("span")
                if span:
                    text = span.get_text(strip=True)
                    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
                    if dates:
                        try:
                            return datetime.strptime(dates[-1], "%Y-%m-%d").date()
                        except ValueError:
                            pass
        return None

    @staticmethod
    def _extract_budget(body_text: str) -> str:
        """본문에서 지원규모/사업비 추출 (IRIS 상세에는 별도 메타 필드 없음)."""
        keywords = ["지원규모", "사업비", "지원금액", "총사업비", "예산", "지원금"]
        for kw in keywords:
            idx = body_text.find(kw)
            if idx != -1:
                snippet = body_text[idx:idx + 100].strip()
                snippet = re.sub(r"\s+", " ", snippet)
                if any(c in snippet for c in ["원", "억", "천만"]):
                    return snippet[:200]
        return ""

    @staticmethod
    def _extract_attachments(body_text: str) -> str:
        """본문 텍스트에서 첨부파일 이름 추출.

        IRIS 첨부는 Innorix JS 시스템으로 href 추출 불가.
        본문에 '붙임N. 파일명.확장자 (크기)' 패턴으로 노출됨.
        """
        files = re.findall(
            r"붙임\d+[.．]?\s*.+?\.(?:pdf|hwp|docx|xlsx|pptx|zip)[^\n]*",
            body_text, re.IGNORECASE
        )
        if files:
            return "\n".join(f.strip() for f in files[:10])
        # fallback: 파일명.확장자 (크기KB) 패턴
        files2 = re.findall(
            r"\S+\.(?:pdf|hwp|docx|xlsx|pptx)\s*\([^)]+\)",
            body_text, re.IGNORECASE
        )
        return "\n".join(files2[:10])

    @staticmethod
    def _parse_date(text: str) -> date | None:
        text = text.strip().replace("/", "-").replace(".", "-")
        for fmt in ("%Y-%m-%d", "%y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None
