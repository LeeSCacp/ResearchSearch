"""D-day 리마인더 수동 테스트 스크립트.

사용법:
    python scripts/test_reminder.py           # 현재 리마인더 대상 조회만
    python scripts/test_reminder.py --send    # 실제 리마인더 메일 발송
    python scripts/test_reminder.py --force   # 임계값 무시, 마감일 있는 공고 전체 발송
"""

import sys
import os
import asyncio

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from datetime import date, datetime
from src.config import load_config
from src.models.announcement import Announcement, NotificationLog, init_db, get_session
from src.notifiers.email import EmailNotifier, REMINDER_THRESHOLDS


def main():
    send   = "--send"  in sys.argv
    force  = "--force" in sys.argv

    print("=" * 55)
    print("[D-day 리마인더 테스트]")
    print("=" * 55)

    config  = load_config()
    engine  = init_db(config["database"]["path"])
    session = get_session(engine)
    today   = date.today()

    print(f"\n오늘 날짜: {today}")
    print(f"임계값: D-7 / D-3 / D-1\n")

    # 마감일 있는 전체 공고 조회
    all_ann = (
        session.query(Announcement)
        .filter(Announcement.deadline.isnot(None))
        .order_by(Announcement.deadline)
        .all()
    )

    print(f"마감일 있는 공고: {len(all_ann)}건")
    print("-" * 55)

    pending = []
    for ann in all_ann:
        days_left = (ann.deadline - today).days
        status    = "마감됨" if days_left < 0 else f"D-{days_left}"

        if force and days_left >= 0:
            pending.append((ann, "d7", days_left))
            print(f"  [{status}] {ann.title[:45]}")
            continue

        # 임계값 확인
        triggered = None
        for event_type, threshold, label in REMINDER_THRESHOLDS:
            if days_left < 0:
                break
            if days_left <= threshold:
                already = session.query(NotificationLog).filter_by(
                    announcement_id=ann.id,
                    event_type=event_type,
                    success=True,
                ).first()
                triggered = (event_type, threshold, label, already)
                break

        if triggered:
            et, th, label, already = triggered
            sent_str = "발송완료" if already else "미발송"
            mark = "[발송 예정]" if not already else "[건너뜀]  "
            print(f"  {mark} {label} ({status}) | {sent_str} | {ann.title[:40]}")
            if not already:
                pending.append((ann, et, days_left))
        else:
            if days_left >= 0:
                print(f"  [해당없음]  D-{days_left} | {ann.title[:40]}")

    print("-" * 55)
    print(f"\n발송 대상: {len(pending)}건")

    if not pending:
        print("현재 리마인더 발송 대상이 없습니다.")
        session.close()
        return

    if not send and not force:
        print("\n실제 발송하려면: python scripts/test_reminder.py --send")
        session.close()
        return

    # 실제 발송
    email_cfg = config["notifications"]["email"]
    if not email_cfg.get("password"):
        print("\n[!] EMAIL_PASSWORD가 설정되지 않았습니다.")
        session.close()
        return

    notifier = EmailNotifier(email_cfg)
    print("\n[리마인더 메일 발송 중...]")
    results = notifier.send_reminder(pending)

    for ann, event_type, days_left in pending:
        ok, err = results.get(ann.id, (False, "결과 없음"))
        status_str = "OK" if ok else f"FAIL: {err}"
        print(f"  [{status_str}] D-{days_left} | {ann.title[:40]}")

        # 발송 결과를 NotificationLog에 기록 (중복 방지용)
        log = NotificationLog(
            announcement_id=ann.id,
            channel="email",
            event_type=event_type,
            sent_at=datetime.now(),
            success=ok,
            error_message=err if not ok else None,
        )
        session.add(log)

    session.commit()
    print("\n발송이 완료되었습니다. 받은편지함을 확인해 주세요.")
    print("(재실행 시 동일 공고는 '[건너뜀]'으로 표시됩니다)")
    print("=" * 55)
    session.close()


if __name__ == "__main__":
    main()
