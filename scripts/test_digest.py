"""일일 다이제스트 테스트.

1) 임시 DB에서 다이제스트 사이클 로직 검증 (발송 없이 캡처):
   - 신규/리마인더 분류, 단계 선택, 하루 1통 보장, 침묵 기록
2) --send: 샘플 데이터로 실제 다이제스트 메일 1통 발송 (레이아웃 확인용)

실행:
  python scripts/test_digest.py          # 로직 검증만
  python scripts/test_digest.py --send   # + 실제 미리보기 메일 발송
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")

from src.config import load_config, today_kst
from src.models.announcement import (
    Announcement, NotificationLog, DigestLog, init_db, get_session,
)


def _make_sample_announcements(today: date) -> list[Announcement]:
    """레이아웃·로직 검증용 샘플 공고 4종."""
    return [
        Announcement(
            title="2026년도 인문사회연구소지원사업 신규과제 공모(문제해결형)",
            url="https://test/nrf-1", source="nrf",
            category="인문사회분야 학술연구지원사업 > 집단연구군 > 인문사회연구소지원",
            posted_date=today - timedelta(days=1), deadline=today + timedelta(days=45),
            budget="연 2억원 이내 (최대 6년)", is_notified=False,
            created_at=datetime.now(),
        ),
        Announcement(
            title="인공지능 기반 심리상담 지원 플랫폼 기술개발",
            url="https://test/ntis-1", source="ntis",
            category="보건복지부",
            posted_date=today - timedelta(days=2), deadline=today + timedelta(days=20),
            is_notified=False, created_at=datetime.now(),
        ),
        Announcement(
            title="2026년도 치매극복연구개발사업 신규과제 공모",
            url="https://test/ntis-2", source="ntis",
            category="보건복지부 > 국립보건연구원",
            posted_date=today - timedelta(days=40), deadline=today + timedelta(days=6),
            budget="과제당 5억원 이내", is_notified=True,   # 이미 알림된 공고 → 리마인더 대상
            created_at=datetime.now() - timedelta(days=40),
        ),
        Announcement(
            title="사회과학연구지원사업(SSK) 중형단계 신규과제 공모",
            url="https://test/iris-1", source="iris",
            category="교육부 > 한국연구재단",
            posted_date=today - timedelta(days=35), deadline=today + timedelta(days=28),
            is_notified=True,   # d30 단계 대상
            created_at=datetime.now() - timedelta(days=35),
        ),
    ]


def test_digest_logic() -> bool:
    """임시 DB로 run_daily_digest 전체 흐름 검증 (실발송 없음)."""
    import tempfile, os
    import src.scheduler as sched
    from src.notifiers.email import EmailNotifier

    tmp_db = os.path.join(tempfile.mkdtemp(), "digest_test.db")
    real_load = sched.load_config
    base_cfg = real_load()
    test_cfg = {**base_cfg, "database": {"path": tmp_db}}
    sched.load_config = lambda: test_cfg

    # 시각 고정: KST 10시로 강제 (09시 게이트 통과) — 게이트 자체는 실환경에서 검증됨
    real_datetime = sched.datetime
    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz).replace(hour=10, minute=0)
    sched.datetime = _FakeDateTime

    captured: dict = {}
    real_send = EmailNotifier.send_digest
    def fake_send(self, new_items, reminder_items, today):
        captured["new"] = [(a.title, r) for a, r in new_items]
        captured["rem"] = [(a.title, ev, d) for a, ev, d, _ in reminder_items]
        captured["html"] = self._build_digest_html(new_items, reminder_items, today)
        return True, ""
    EmailNotifier.send_digest = fake_send

    ok = True
    try:
        engine = init_db(tmp_db)
        session = get_session(engine)
        today = today_kst()
        for a in _make_sample_announcements(today):
            session.add(a)
        session.commit()

        # ── 1차 실행: 발송 + 기록 ──
        asyncio.run(sched.run_daily_digest())

        new_titles = [t for t, _ in captured.get("new", [])]
        rem = captured.get("rem", [])
        checks = [
            ("신규 2건 분류", len(new_titles) == 2),
            ("신규에 인문사회연구소 포함", any("인문사회연구소" in t for t in new_titles)),
            ("신규에 심리×AI 포함", any("심리상담" in t for t in new_titles)),
            ("리마인더 2건 분류", len(rem) == 2),
            ("치매 D-6 → d7 단계", any(ev == "d7" and d == 6 for _, ev, d in rem)),
            ("SSK D-28 → d30 단계", any(ev == "d30" and d == 28 for _, ev, d in rem)),
        ]

        session.expire_all()
        notified = session.query(Announcement).filter_by(is_notified=True).count()
        dlog = session.query(DigestLog).filter_by(digest_date=today).first()
        nlogs = session.query(NotificationLog).filter_by(success=True).count()
        checks += [
            ("신규 2건 is_notified 전환 (총 4건)", notified == 4),
            ("DigestLog 기록 (count=4)", dlog is not None and dlog.item_count == 4),
            ("NotificationLog 4건 기록", nlogs == 4),
        ]

        # ── 2차 실행: 같은 날 재실행 → 발송 없어야 함 ──
        captured.clear()
        asyncio.run(sched.run_daily_digest())
        checks.append(("같은 날 재실행 시 미발송", "html" not in captured))

        for name, passed in checks:
            print(f'  {"OK " if passed else "FAIL"} {name}')
            ok = ok and passed
        session.close()
    finally:
        sched.load_config = real_load
        sched.datetime = real_datetime
        EmailNotifier.send_digest = real_send

    return ok


def send_preview():
    """샘플 데이터로 실제 미리보기 메일 발송."""
    from src.notifiers.email import EmailNotifier
    from src.filters.engine import FilterEngine
    from src.scrapers.base import AnnouncementData

    config = load_config()
    fc = config["filters"]
    engine = FilterEngine(keywords=fc["keywords"], categories=fc["categories"],
                          exclude_keywords=fc["exclude_keywords"],
                          conditional_keywords=fc.get("conditional_keywords", []))
    today = today_kst()
    anns = _make_sample_announcements(today)

    def reasons(a):
        return engine.match_reasons(AnnouncementData(
            title=a.title, url=a.url, source=a.source, category=a.category or "",
            deadline=a.deadline, posted_date=a.posted_date, description=""))

    new_items = [(a, reasons(a)) for a in anns[:2]]
    reminder_items = [
        (anns[2], "d7", 6, reasons(anns[2])),
        (anns[3], "d30", 28, reasons(anns[3])),
    ]

    notifier = EmailNotifier(config["notifications"]["email"])
    ok, err = notifier.send_digest(new_items, reminder_items, today)
    print(f'미리보기 메일 발송: {"성공" if ok else "실패 — " + err}')


if __name__ == "__main__":
    print("=== 다이제스트 로직 검증 ===")
    passed = test_digest_logic()
    print(f"\n결과: {'전체 통과' if passed else '실패 있음'}")
    if "--send" in sys.argv:
        print("\n=== 미리보기 메일 발송 ===")
        send_preview()
