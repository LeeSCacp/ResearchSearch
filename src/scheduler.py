"""스크래핑 + 알림 스케줄러.

APScheduler를 사용하여 주기적으로 스크래핑을 실행하고,
신규 공고 알림 및 D-day 리마인더를 발송한다.
"""

import asyncio
import logging
from datetime import datetime, date

from sqlalchemy.orm import Session

from src.config import load_config, today_kst, KST
from src.models.announcement import (
    Announcement, NotificationLog, DigestLog, init_db, get_session,
)
from src.scrapers.base import AnnouncementData
from src.scrapers.nrf import NRFScraper
from src.scrapers.ntis import NTISScraper
from src.scrapers.iris import IRISScraper
from src.filters.engine import FilterEngine
from src.notifiers.email import EmailNotifier, REMINDER_THRESHOLDS
from src.notifiers.telegram import TelegramNotifier
from src.notifiers.calendar import CalendarNotifier

logger = logging.getLogger(__name__)


# ======================================================================
# 신규 공고 스크래핑 사이클
# ======================================================================

async def run_scraping_cycle():
    """1회 스크래핑 사이클: 수집 → 저장 → 필터 → 신규 공고 알림."""
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    try:
        # 1. 스크래핑
        all_items = await _scrape_all(config)
        logger.info(f"총 {len(all_items)}건 수집됨")

        # 2. 신규 공고만 저장
        new_announcements = _save_new_items(session, all_items)
        logger.info(f"신규 {len(new_announcements)}건 저장됨")

        if not new_announcements:
            logger.info("신규 공고 없음, 알림 생략")
            return

        # 3. 상세 정보 수집 (신규 공고만)
        await _enrich_new_items(session, all_items, new_announcements)

        # 4. 필터링
        filter_cfg = config.get("filters", {})
        filter_engine = FilterEngine(
            keywords=filter_cfg.get("keywords", []),
            categories=filter_cfg.get("categories", []),
            exclude_keywords=filter_cfg.get("exclude_keywords", []),
            conditional_keywords=filter_cfg.get("conditional_keywords", []),
        )
        dto_items = [
            AnnouncementData(
                title=a.title, url=a.url, source=a.source,
                category=a.category or "", deadline=a.deadline,
                posted_date=a.posted_date, description=a.description or "",
            )
            for a in new_announcements
        ]
        filtered_dtos = filter_engine.filter_items(dto_items)
        filtered_urls = {d.url for d in filtered_dtos}
        filtered = [a for a in new_announcements if a.url in filtered_urls]

        # 4. 필터 결과 로그만 남긴다 — 발송은 일일 다이제스트(09:00 KST)가 전담
        #    (2026-07-18 B: 즉시 발송 제거, 하루 최대 1통 통합)
        logger.info(
            f"필터 통과: {len(filtered)}건 / 전체 신규: {len(new_announcements)}건 "
            f"— 다이제스트 대기 (is_notified=False 유지)"
        )
        for a in filtered:
            logger.info(f"  → 다이제스트 대상: [{a.source}] {a.title[:50]}")

    except Exception as e:
        logger.error(f"스크래핑 사이클 오류: {e}")
        session.rollback()
    finally:
        session.close()


# ======================================================================
# 일일 다이제스트 사이클 (2026-07-18 B) — 하루 최대 1통, 09:00 KST 이후
# ======================================================================

# 신규 섹션에 포함할 공고의 최대 나이(일) — 오래 묵은 미발송 공고가
# 키워드 변경 등으로 갑자기 "신규"로 되살아나는 것을 방지
_DIGEST_NEW_MAX_AGE_DAYS = 14


async def run_daily_digest():
    """일일 다이제스트: 신규 공고 + 마감 리마인더를 1통으로 통합 발송.

    발송 규칙:
      - KST 09시 이전 사이클(새벽 3시 등)은 건너뜀
      - KST 날짜당 최대 1통 (DigestLog로 보장)
      - 보낼 내용이 없으면 침묵 (그날은 count=0으로 기록)
      - 발송 실패 시 기록하지 않음 → 같은 날 다음 사이클(15시)이 재시도
    """
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    try:
        now_kst = datetime.now(KST)
        today = now_kst.date()

        if now_kst.hour < 9:
            logger.info(f"다이제스트: KST {now_kst.hour}시 — 09시 이전, 건너뜀")
            return
        if session.query(DigestLog).filter_by(digest_date=today).first():
            logger.info("다이제스트: 오늘자 발송(또는 침묵) 기록 있음 — 건너뜀")
            return

        filter_cfg = config.get("filters", {})
        filter_engine = FilterEngine(
            keywords=filter_cfg.get("keywords", []),
            categories=filter_cfg.get("categories", []),
            exclude_keywords=filter_cfg.get("exclude_keywords", []),
            conditional_keywords=filter_cfg.get("conditional_keywords", []),
        )

        def _dto(a: Announcement) -> AnnouncementData:
            return AnnouncementData(
                title=a.title, url=a.url, source=a.source,
                category=a.category or "", deadline=a.deadline,
                posted_date=a.posted_date, description=a.description or "",
            )

        # ── 1. 신규 섹션: 미발송 + 마감 안 지남 + 최근 등록 + 필터 통과 ──
        new_candidates = (
            session.query(Announcement)
            .filter(Announcement.is_notified == False)
            .all()
        )
        new_items: list[tuple[Announcement, list[str]]] = []
        for ann in new_candidates:
            if ann.deadline and ann.deadline < today:
                continue   # 이미 마감
            if ann.created_at and (datetime.now() - ann.created_at).days > _DIGEST_NEW_MAX_AGE_DAYS:
                continue   # 오래 묵은 미발송 공고
            dto = _dto(ann)
            if filter_engine.matches(dto):
                new_items.append((ann, filter_engine.match_reasons(dto)))
        new_ids = {ann.id for ann, _ in new_items}

        # ── 2. 리마인더 섹션: 필터 통과 + 단계 도달 + 미발송 단계만 ──────
        reminder_items: list[tuple[Announcement, str, int, list[str]]] = []
        active = (
            session.query(Announcement)
            .filter(Announcement.deadline.isnot(None))
            .filter(Announcement.deadline >= today)
            .all()
        )
        for ann in active:
            if ann.id in new_ids:
                continue   # 오늘 신규로 소개되는 공고는 리마인더 중복 제외
            dto = _dto(ann)
            if not filter_engine.matches(dto):
                continue
            days_left = (ann.deadline - today).days
            for event_type, threshold, _label in REMINDER_THRESHOLDS:
                if days_left > threshold:
                    continue
                already = (
                    session.query(NotificationLog)
                    .filter_by(announcement_id=ann.id,
                               event_type=event_type, success=True)
                    .first()
                )
                if not already:
                    reminder_items.append(
                        (ann, event_type, days_left, filter_engine.match_reasons(dto))
                    )
                break   # 가장 긴급한 미발송 단계 하나만

        # 마감 가까운 순 정렬
        reminder_items.sort(key=lambda x: x[2])

        # ── 3. 발송 또는 침묵 ────────────────────────────────────────────
        if not new_items and not reminder_items:
            session.add(DigestLog(digest_date=today, item_count=0, success=True))
            session.commit()
            logger.info("다이제스트: 보낼 내용 없음 — 오늘은 침묵 (기록만)")
            return

        email_cfg = config.get("notifications", {}).get("email", {})
        notifier = EmailNotifier(email_cfg)
        ok, err = notifier.send_digest(new_items, reminder_items, today)

        if not ok:
            logger.error(f"다이제스트 발송 실패 — 다음 사이클 재시도: {err}")
            for ann, _ in new_items:
                _log_notification(session, ann.id, "email", "new", False, err)
            for ann, event_type, _, _ in reminder_items:
                _log_notification(session, ann.id, "email", event_type, False, err)
            return

        # ── 4. 성공 기록 ─────────────────────────────────────────────────
        for ann, _ in new_items:
            ann.is_notified = True
            _log_notification(session, ann.id, "email", "new", True)
        for ann, event_type, _, _ in reminder_items:
            _log_notification(session, ann.id, "email", event_type, True)
        session.add(DigestLog(
            digest_date=today,
            item_count=len(new_items) + len(reminder_items),
            success=True,
        ))
        session.commit()
        logger.info(
            f"다이제스트 발송 완료: 신규 {len(new_items)} + 리마인더 {len(reminder_items)}"
        )

        # ── 5. Google Calendar (켜져 있을 때만, 신규 공고만) ─────────────
        cal_cfg = config.get("notifications", {}).get("google_calendar", {})
        await _add_calendar_events(session, cal_cfg, [ann for ann, _ in new_items])

    except Exception as e:
        logger.error(f"다이제스트 사이클 오류: {e}")
        session.rollback()
    finally:
        session.close()


# ======================================================================
# D-day 리마인더 사이클 (레거시 — 다이제스트로 대체됨, 테스트용 유지)
# ======================================================================

async def run_reminder_cycle():
    """D-day 리마인더 사이클: D-7 / D-3 / D-1 마감 임박 공고 알림.

    매일 1회 실행. 각 임계값에 대해 미발송 공고만 골라 알림 발송.
    """
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    try:
        today = today_kst()
        notif_cfg = config.get("notifications", {})

        # 마감일이 있고 아직 마감되지 않은 공고 전체 조회
        candidates = (
            session.query(Announcement)
            .filter(Announcement.deadline.isnot(None))
            .filter(Announcement.deadline >= today)
            .all()
        )

        # 신규 공고 알림과 동일한 필터 적용 — 무관 분야 공고는 리마인더도 보내지 않음
        filter_cfg = config.get("filters", {})
        filter_engine = FilterEngine(
            keywords=filter_cfg.get("keywords", []),
            categories=filter_cfg.get("categories", []),
            exclude_keywords=filter_cfg.get("exclude_keywords", []),
            conditional_keywords=filter_cfg.get("conditional_keywords", []),
        )
        active_announcements = []
        for ann in candidates:
            dto = AnnouncementData(
                title=ann.title, url=ann.url, source=ann.source,
                category=ann.category or "", deadline=ann.deadline,
                posted_date=ann.posted_date, description=ann.description or "",
            )
            if filter_engine.matches(dto):
                active_announcements.append(ann)

        logger.info(
            f"리마인더 대상 후보: {len(active_announcements)}건 "
            f"(마감 임박 전체 {len(candidates)}건 중 필터 통과)"
        )

        # 각 임계값별로 미발송 대상 추출
        # [(Announcement, event_type, days_left), ...]
        pending: list[tuple[Announcement, str, int]] = []

        for ann in active_announcements:
            days_left = (ann.deadline - today).days

            for event_type, threshold, _ in REMINDER_THRESHOLDS:
                if days_left > threshold:
                    continue  # 아직 이 임계값에 해당 안 됨

                # 이미 이 event_type으로 성공 발송했는지 확인
                already_sent = (
                    session.query(NotificationLog)
                    .filter_by(
                        announcement_id=ann.id,
                        event_type=event_type,
                        success=True,
                    )
                    .first()
                )
                if already_sent:
                    continue

                pending.append((ann, event_type, days_left))
                break  # 한 공고에 여러 임계값이 해당될 때 가장 긴급한 것만 처리

        if not pending:
            logger.info("리마인더 발송 대상 없음")
            return

        # 긴급도별 로그 출력
        for ann, et, dl in pending:
            logger.info(f"리마인더 대상: [{et}] D-{dl} | {ann.title[:40]}")

        # 발송
        await _send_reminders(session, notif_cfg, pending)

    except Exception as e:
        logger.error(f"리마인더 사이클 오류: {e}")
        session.rollback()
    finally:
        session.close()


# ======================================================================
# 내부 함수
# ======================================================================

async def _scrape_all(config: dict) -> list[AnnouncementData]:
    sites = config.get("scraping", {}).get("sites", {})
    scrapers = []
    if sites.get("nrf",  True): scrapers.append(NRFScraper())
    if sites.get("ntis", True): scrapers.append(NTISScraper())
    if sites.get("iris", True): scrapers.append(IRISScraper())

    results = await asyncio.gather(
        *[s.scrape() for s in scrapers],
        return_exceptions=True,
    )
    all_items: list[AnnouncementData] = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"스크래퍼 오류: {result}")
        else:
            all_items.extend(result)
    return all_items


async def _enrich_new_items(
    session: Session,
    all_items: list[AnnouncementData],
    new_announcements: list[Announcement],
):
    """신규 공고에 대해 상세 페이지 수집 후 DB 업데이트."""
    from src.scrapers.nrf import NRFScraper
    from src.scrapers.ntis import NTISScraper
    from src.scrapers.iris import IRISScraper

    # URL → DTO 매핑 (상세 스크래퍼 호출용)
    url_to_dto = {item.url: item for item in all_items}
    # 소스별 스크래퍼 인스턴스
    scrapers = {
        "nrf":  NRFScraper(),
        "ntis": NTISScraper(),
        "iris": IRISScraper(),
    }

    enriched = 0
    for ann in new_announcements:
        dto = url_to_dto.get(ann.url)
        if not dto:
            continue
        scraper = scrapers.get(ann.source)
        if not scraper:
            continue

        try:
            enriched_dto = await scraper.scrape_detail(dto)
            # DB 업데이트
            if enriched_dto.description:
                ann.description = enriched_dto.description
            if enriched_dto.budget:
                ann.budget = enriched_dto.budget
            if enriched_dto.attachments:
                ann.attachments = enriched_dto.attachments
            if enriched_dto.deadline and not ann.deadline:
                ann.deadline = enriched_dto.deadline
            if enriched_dto.category and not ann.category:
                ann.category = enriched_dto.category
            ann.detail_fetched = True
            enriched += 1
        except Exception as e:
            logger.warning(f"상세 수집 실패 [{ann.title[:30]}]: {e}")

    if enriched:
        session.commit()
        logger.info(f"상세 정보 수집 완료: {enriched}건")


def _save_new_items(session: Session, items: list[AnnouncementData]) -> list[Announcement]:
    new_announcements: list[Announcement] = []
    for item in items:
        if session.query(Announcement).filter_by(url=item.url).first():
            continue
        ann = Announcement(
            title=item.title, url=item.url, source=item.source,
            category=item.category, deadline=item.deadline,
            posted_date=item.posted_date, description=item.description,
            is_notified=False,
        )
        session.add(ann)
        new_announcements.append(ann)
    session.commit()
    return new_announcements


def _log_notification(session: Session, announcement_id: int,
                       channel: str, event_type: str,
                       success: bool, error_message: str = ""):
    """알림 발송 결과를 NotificationLog에 기록."""
    log = NotificationLog(
        announcement_id=announcement_id,
        channel=channel,
        event_type=event_type,
        sent_at=datetime.now(),
        success=success,
        error_message=error_message if not success else None,
    )
    session.add(log)
    session.commit()


async def _send_new_notifications(session: Session, notif_cfg: dict,
                                   announcements: list[Announcement]):
    """신규 공고 알림 발송 + 로그 기록."""
    # 이메일
    email_cfg = notif_cfg.get("email", {})
    if email_cfg.get("enabled"):
        notifier = EmailNotifier(email_cfg)
        ok = notifier.send(announcements)
        for ann in announcements:
            _log_notification(session, ann.id, "email", "new", ok,
                              "" if ok else "발송 실패")
        if ok:
            for ann in announcements:
                ann.is_notified = True
            session.commit()

    # 텔레그램
    tg_cfg = notif_cfg.get("telegram", {})
    if tg_cfg.get("enabled"):
        tg = TelegramNotifier(tg_cfg)
        ok = await tg.send(announcements)
        for ann in announcements:
            _log_notification(session, ann.id, "telegram", "new", ok,
                              "" if ok else "발송 실패")


async def _send_reminders(session: Session, notif_cfg: dict,
                           pending: list[tuple[Announcement, str, int]]):
    """D-day 리마인더 발송 + 로그 기록."""
    # 이메일
    email_cfg = notif_cfg.get("email", {})
    if email_cfg.get("enabled"):
        notifier = EmailNotifier(email_cfg)
        results = notifier.send_reminder(pending)
        for ann, event_type, _ in pending:
            ok, err = results.get(ann.id, (False, "결과 없음"))
            _log_notification(session, ann.id, "email", event_type, ok, err)

    # 텔레그램 (추후 확장)
    # tg_cfg = notif_cfg.get("telegram", {})
    # if tg_cfg.get("enabled"): ...


async def _add_calendar_events(session: Session, cal_cfg: dict,
                                announcements: list[Announcement]):
    """신규 공고 마감일을 Google Calendar에 종일 이벤트로 등록."""
    if not cal_cfg.get("enabled"):
        return

    notifier = CalendarNotifier(cal_cfg)
    # 마감일이 있는 공고만 추려서 등록
    has_deadline = [a for a in announcements if a.deadline]
    if not has_deadline:
        logger.info("[Calendar] 마감일이 있는 신규 공고 없음 — 캘린더 등록 생략")
        return

    results = notifier.add_deadline_events(has_deadline)
    for ann in has_deadline:
        ok, err = results.get(ann.id, (False, "결과 없음"))
        _log_notification(session, ann.id, "calendar", "new", ok, err)
