"""알림 모듈 단위 테스트 (실제 발송 없이 구조 검증)."""

import pytest
from datetime import date, datetime

from src.models.announcement import Announcement
from src.notifiers.email import EmailNotifier
from src.notifiers.telegram import TelegramNotifier


def _make_announcement(**kwargs) -> Announcement:
    defaults = {
        "id": 1,
        "title": "테스트 공고",
        "url": "https://example.com/1",
        "source": "nrf",
        "category": "공학",
        "deadline": date(2026, 12, 31),
        "posted_date": date(2026, 4, 1),
        "description": "테스트 설명",
        "is_notified": False,
    }
    defaults.update(kwargs)
    ann = Announcement(**defaults)
    return ann


class TestEmailNotifier:
    def test_disabled_returns_false(self):
        notifier = EmailNotifier({"enabled": False})
        result = notifier.send([_make_announcement()])
        assert result is False

    def test_empty_list_returns_true(self):
        notifier = EmailNotifier({"enabled": True, "sender": "a", "password": "b", "recipients": ["c"]})
        result = notifier.send([])
        assert result is True

    def test_incomplete_config_returns_false(self):
        notifier = EmailNotifier({"enabled": True, "sender": "", "password": "", "recipients": []})
        result = notifier.send([_make_announcement()])
        assert result is False

    def test_html_build(self):
        ann = _make_announcement()
        html = EmailNotifier._build_new_html([ann])
        assert "테스트 공고" in html
        assert "NRF" in html
        assert "2026-12-31" in html


class TestTelegramNotifier:
    def test_disabled_returns_false(self):
        import asyncio
        notifier = TelegramNotifier({"enabled": False})
        result = asyncio.run(notifier.send([_make_announcement()]))
        assert result is False

    def test_message_build(self):
        ann = _make_announcement()
        message = TelegramNotifier._build_message([ann])
        assert "테스트 공고" in message
        assert "한국연구재단" in message
        assert "2026-12-31" in message
