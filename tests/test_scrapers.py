"""스크래퍼 기본 구조 테스트."""

import pytest
from datetime import date

from src.scrapers.base import BaseScraper, AnnouncementData
from src.scrapers.nrf import NRFScraper
from src.scrapers.ntis import NTISScraper
from src.scrapers.iris import IRISScraper


class TestAnnouncementData:
    def test_creation(self):
        item = AnnouncementData(
            title="테스트 공고",
            url="https://example.com/1",
            source="nrf",
            category="공학",
            deadline=date(2026, 12, 31),
            posted_date=date(2026, 4, 1),
            description="설명",
        )
        assert item.title == "테스트 공고"
        assert item.source == "nrf"
        assert item.deadline == date(2026, 12, 31)

    def test_defaults(self):
        item = AnnouncementData(title="공고", url="https://example.com", source="ntis")
        assert item.category == ""
        assert item.deadline is None
        assert item.description == ""


class TestScraperInstances:
    def test_nrf_scraper_has_source_name(self):
        scraper = NRFScraper()
        assert scraper.source_name == "nrf"

    def test_ntis_scraper_has_source_name(self):
        scraper = NTISScraper()
        assert scraper.source_name == "ntis"

    def test_iris_scraper_has_source_name(self):
        scraper = IRISScraper()
        assert scraper.source_name == "iris"


class TestNRFDateParser:
    def test_parse_yyyy_mm_dd(self):
        result = NRFScraper._parse_date("2026-04-08")
        assert result == date(2026, 4, 8)

    def test_parse_dot_separator(self):
        result = NRFScraper._parse_date("2026.04.08")
        assert result == date(2026, 4, 8)

    def test_parse_slash_separator(self):
        result = NRFScraper._parse_date("2026/04/08")
        assert result == date(2026, 4, 8)

    def test_parse_invalid(self):
        result = NRFScraper._parse_date("invalid")
        assert result is None

    def test_parse_empty(self):
        result = NRFScraper._parse_date("")
        assert result is None
