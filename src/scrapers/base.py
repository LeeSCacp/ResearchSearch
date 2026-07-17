from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
import logging

logger = logging.getLogger(__name__)


@dataclass
class AnnouncementData:
    """스크래퍼에서 수집한 공고 데이터를 담는 DTO."""
    title:       str
    url:         str
    source:      str
    category:    str       = ""
    deadline:    date | None = None
    posted_date: date | None = None
    description: str       = ""   # 사업 개요/목적
    budget:      str       = ""   # 지원규모 (예: "과제당 3억원 이내")
    attachments: str       = ""   # 첨부파일 URL 목록 (줄바꿈 구분)


class BaseScraper(ABC):
    """모든 스크래퍼의 추상 베이스 클래스."""

    source_name: str = ""

    def __init__(self):
        # 침묵 고장 감지용 — scrape() 실행 후 각 스크래퍼가 갱신한다.
        #   raw_count: 사이트에서 파싱한 원시 목록 개수 (필터링 전)
        #   ok: 구조가 정상으로 보이는지 (raw_count > 0 등)
        #   error: 실패 사유
        # "접수 중 공고 0건"은 정상일 수 있으므로 ok 판정은 raw_count 기준.
        self.health: dict = {"ok": True, "raw_count": None, "error": ""}

    def set_health(self, ok: bool, raw_count: int | None, error: str = ""):
        self.health = {"ok": ok, "raw_count": raw_count, "error": error[:300]}

    @abstractmethod
    async def scrape(self) -> list[AnnouncementData]:
        """사이트에서 공고 목록을 수집하여 반환한다."""
        ...

    async def scrape_detail(self, item: AnnouncementData) -> AnnouncementData:
        """상세 페이지를 방문하여 item의 필드를 보강한다.

        기본 구현은 item을 그대로 반환한다.
        각 스크래퍼에서 재정의하여 description, budget, attachments 등을 채운다.
        실패해도 원본 item을 반환하며 전체 흐름을 방해하지 않는다.
        """
        return item

    def log_info(self, msg: str):
        logger.info(f"[{self.source_name}] {msg}")

    def log_warning(self, msg: str):
        logger.warning(f"[{self.source_name}] {msg}")

    def log_error(self, msg: str):
        logger.error(f"[{self.source_name}] {msg}")
