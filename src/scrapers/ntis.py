"""NTIS(국가과학기술지식정보서비스) 공고 스크래퍼.

NTIS 통합공고 페이지(ntis.go.kr)에서 공고 목록을 수집한다.
공고 목록은 table.basic_list에 서버 렌더링되므로 httpx + BS4로 처리 가능.

테이블 구조 (2026-04 기준):
  td[0]: 체크박스 (빈값)
  td[1]: 순번
  td[2]: 현황 (접수중/접수예정/마감 등)
  td[3]: 공고명 + <a href="/rndgate/eg/un/ra/view.do?roRndUid=...">
  td[4]: 부처명
  td[5]: 접수일 (YYYY.MM.DD)
  td[6]: 마감일 (YYYY.MM.DD)
  td[7]: D-day
"""

import re
from datetime import datetime, date

import httpx
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, AnnouncementData

NTIS_LIST_URL   = "https://www.ntis.go.kr/rndgate/eg/un/ra/mng.do"
NTIS_BASE_URL   = "https://www.ntis.go.kr"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class NTISScraper(BaseScraper):
    source_name = "ntis"

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
        results = []
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            response = await client.get(NTIS_LIST_URL, headers=HEADERS)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            table = soup.find("table", class_="basic_list")
            if not table:
                self.log_error("basic_list 테이블을 찾을 수 없습니다")
                return results
            for row in table.find_all("tr")[1:]:
                try:
                    item = self._parse_row(row)
                    if item:
                        results.append(item)
                except Exception as e:
                    self.log_error(f"행 파싱 실패: {e}")
        return results

    def _parse_row(self, row) -> AnnouncementData | None:
        tds = row.find_all("td")
        if len(tds) < 7:
            return None

        link = tds[3].find("a")
        if not link:
            return None
        title = link.get_text(strip=True)
        if not title:
            return None

        href = link.get("href", "")
        if not href or href.startswith("#"):
            return None
        url = href if href.startswith("http") else NTIS_BASE_URL + href

        category    = tds[4].get_text(strip=True)
        posted_date = self._parse_date(tds[5].get_text(strip=True))
        deadline    = self._parse_date(tds[6].get_text(strip=True))

        return AnnouncementData(
            title=title, url=url, source="ntis",
            category=category, posted_date=posted_date, deadline=deadline,
        )

    # ------------------------------------------------------------------
    # 상세 수집
    # ------------------------------------------------------------------

    async def scrape_detail(self, item: AnnouncementData) -> AnnouncementData:
        """NTIS 상세 페이지에서 description, budget, attachments 수집."""
        try:
            async with httpx.AsyncClient(timeout=20, verify=False) as client:
                resp = await client.get(item.url, headers=HEADERS)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")

            item.description = self._extract_description(soup)
            item.budget       = self._extract_budget(soup)
            item.attachments  = self._extract_attachments(soup)

            # 마감일 보완 (목록에서 못 가져왔을 경우)
            if not item.deadline:
                item.deadline = self._extract_deadline(soup)

        except Exception as e:
            self.log_error(f"상세 수집 실패 [{item.title[:30]}]: {e}")

        return item

    @staticmethod
    def _clean_text(text: str) -> str:
        """HTML span 분리로 인한 불필요한 공백 제거 (예: '2 026' → '2026')."""
        text = re.sub(r'(?<=\d)\s+(?=\d)', '', text)   # 숫자 사이 공백
        text = re.sub(r' {2,}', ' ', text)               # 연속 공백 정리
        return text.strip()

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str:
        """NTIS 공고 본문(se-contents) 추출."""
        sc = soup.find("div", class_="se-contents")
        if sc:
            return NTISScraper._clean_text(sc.get_text(separator=" ", strip=True))[:1000]
        nc = soup.find("div", class_="notice_cont")
        if nc:
            return NTISScraper._clean_text(nc.get_text(separator=" ", strip=True))[:1000]
        return ""

    @staticmethod
    def _extract_budget(soup: BeautifulSoup) -> str:
        """NTIS notice_view에서 공고금액 추출."""
        nv = soup.find("div", class_="notice_view")
        if not nv:
            return ""
        text = nv.get_text(separator="|", strip=True)
        # 패턴: 공고금액 :|128.55|억원
        m = re.search(r"공고금액\s*:\s*\|([0-9.,]+)\|억원", text)
        if m:
            return m.group(1) + "억원"
        # 패턴: 공고금액 :|1,234백만원
        m2 = re.search(r"공고금액\s*:\s*\|([0-9,]+(?:\.[0-9]+)?)\|(백만원|만원|원)", text)
        if m2:
            return m2.group(1) + m2.group(2)
        return ""

    @staticmethod
    def _extract_deadline(soup: BeautifulSoup) -> date | None:
        """NTIS notice_view에서 마감일 추출 (보완용)."""
        nv = soup.find("div", class_="notice_view")
        if not nv:
            return None
        text = nv.get_text(separator="|", strip=True)
        # 패턴: 마감일 :|2026.05.12
        m = re.search(r"마감일\s*:\s*\|(\d{4}[./]\d{2}[./]\d{2})", text)
        if m:
            t = m.group(1).replace(".", "-").replace("/", "-")
            try:
                return datetime.strptime(t, "%Y-%m-%d").date()
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_attachments(soup: BeautifulSoup) -> str:
        """NTIS summary_file 영역에서 첨부파일 이름 목록 추출."""
        sf = soup.find("div", class_="summary_file")
        if not sf:
            return ""
        names = []
        for a in sf.find_all("a"):
            name = a.get_text(strip=True)
            if name:
                names.append(name)
        return "\n".join(names[:10])

    @staticmethod
    def _parse_date(text: str) -> date | None:
        text = text.strip().replace("/", "-").replace(".", "-")
        for fmt in ("%Y-%m-%d", "%y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None
