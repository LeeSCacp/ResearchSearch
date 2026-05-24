"""필터 엔진 단위 테스트."""

import pytest
from datetime import date

from src.scrapers.base import AnnouncementData
from src.filters.engine import FilterEngine


def _make_item(title="테스트 공고", category="공학", description="") -> AnnouncementData:
    return AnnouncementData(
        title=title,
        url="https://example.com/1",
        source="nrf",
        category=category,
        deadline=date(2026, 12, 31),
        posted_date=date(2026, 4, 1),
        description=description,
    )


class TestFilterEngine:
    def test_empty_filter_passes_all(self):
        engine = FilterEngine(keywords=[], categories=[])
        item = _make_item()
        assert engine.matches(item) is True

    def test_keyword_match_in_title(self):
        engine = FilterEngine(keywords=["AI"], categories=[])
        item = _make_item(title="AI 기반 연구과제 공모")
        assert engine.matches(item) is True

    def test_keyword_no_match(self):
        engine = FilterEngine(keywords=["바이오"], categories=[])
        item = _make_item(title="AI 기반 연구과제 공모", description="인공지능 관련")
        assert engine.matches(item) is False

    def test_keyword_match_in_description(self):
        engine = FilterEngine(keywords=["인공지능"], categories=[])
        item = _make_item(title="데이터 분석", description="인공지능 기술 활용")
        assert engine.matches(item) is True

    def test_keyword_case_insensitive(self):
        engine = FilterEngine(keywords=["ai"], categories=[])
        item = _make_item(title="AI 기반 연구")
        assert engine.matches(item) is True

    def test_category_match(self):
        engine = FilterEngine(keywords=[], categories=["공학"])
        item = _make_item(category="전자공학")
        assert engine.matches(item) is True

    def test_category_no_match(self):
        engine = FilterEngine(keywords=[], categories=["자연과학"])
        item = _make_item(category="공학")
        assert engine.matches(item) is False

    def test_keyword_or_category(self):
        engine = FilterEngine(keywords=["AI"], categories=["자연과학"])
        # 키워드 매칭 (카테고리는 불일치)
        item = _make_item(title="AI 연구", category="공학")
        assert engine.matches(item) is True

    def test_filter_items_returns_subset(self):
        engine = FilterEngine(keywords=["AI"], categories=[])
        items = [
            _make_item(title="AI 연구"),
            _make_item(title="바이오 연구"),
            _make_item(title="AI 데이터 분석"),
        ]
        # url 중복 방지를 위해 각각 다른 url 부여
        items[0].url = "https://example.com/1"
        items[1].url = "https://example.com/2"
        items[2].url = "https://example.com/3"

        filtered = engine.filter_items(items)
        assert len(filtered) == 2
        assert all("AI" in item.title for item in filtered)
