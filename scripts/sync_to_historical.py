"""운영 DB(announcements) → historical_announcements 동기화.

목적:
  NTIS/IRIS는 사이트에서 5년치 직접 수집이 불가능 (마감되면 자동 제거).
  대신 매 스크래핑 사이클마다 운영 DB에 들어온 신규 공고를
  historical 테이블로 자동 누적하여 장기간에 걸쳐 데이터셋을 구축한다.

  NRF도 동기화 대상에 포함하지만, 이미 5년치 수집되어 있어 중복은 자동 제외.

실행:
  python scripts/sync_to_historical.py            # 1회 실행
  (또는 scripts/run_scrape.py 내부에서 자동 호출)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("sync")

from src.config import load_config
from src.models.announcement import (
    init_db, get_session, Announcement, HistoricalAnnouncement,
)


def _label_one(ann: HistoricalAnnouncement) -> None:
    """label_historical.label_one을 재사용. 순환 import 회피용으로 동적 import."""
    from scripts.label_historical import label_one
    labels = label_one(ann)
    ann.label_psychology = labels["psychology"]
    ann.label_aging      = labels["aging"]
    ann.label_psy_ai     = labels["psy_ai"]
    ann.label_humanities = labels["humanities"]


def sync() -> dict:
    """운영 DB의 모든 공고 중 historical에 없는 것을 신규 추가."""
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    stats = {"checked": 0, "added": 0, "skipped": 0, "by_source": {}}

    try:
        ops = session.query(Announcement).all()
        existing_urls = {
            u for (u,) in session.query(HistoricalAnnouncement.url).all()
        }

        for op in ops:
            stats["checked"] += 1
            if op.url in existing_urls:
                stats["skipped"] += 1
                continue

            year = None
            if op.posted_date:
                year = op.posted_date.year

            hist = HistoricalAnnouncement(
                title       = (op.title or "")[:2000],
                url         = op.url,
                source      = op.source or "unknown",
                category    = (op.category or "")[:500],
                notice_type = "접수마감" if op.deadline else "",
                posted_date = op.posted_date,
                deadline    = op.deadline,
                year        = year,
            )
            _label_one(hist)
            session.add(hist)
            stats["added"] += 1
            stats["by_source"][op.source or "unknown"] = (
                stats["by_source"].get(op.source or "unknown", 0) + 1
            )

        session.commit()
    finally:
        session.close()

    return stats


def main():
    logger.info("운영 DB → historical 동기화 시작")
    stats = sync()
    logger.info(
        f"확인 {stats['checked']}건 / "
        f"신규 {stats['added']}건 / "
        f"이미 있음 {stats['skipped']}건"
    )
    if stats["by_source"]:
        logger.info(f"신규 출처별: {stats['by_source']}")


if __name__ == "__main__":
    main()
