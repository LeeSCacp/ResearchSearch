"""공고 상세 정보 수집 테스트 스크립트.

사용법:
    python scripts/test_detail.py           # DB의 미수집 공고 상세 수집
    python scripts/test_detail.py --all     # 전체 공고 상세 재수집
    python scripts/test_detail.py --source ntis   # 특정 출처만
"""

import sys
import os
import asyncio

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.models.announcement import Announcement, init_db, get_session
from src.scrapers.base import AnnouncementData
from src.scrapers.ntis import NTISScraper
from src.scrapers.iris import IRISScraper
from src.scrapers.nrf import NRFScraper


async def main():
    fetch_all = "--all"    in sys.argv
    source    = None
    if "--source" in sys.argv:
        idx = sys.argv.index("--source")
        if idx + 1 < len(sys.argv):
            source = sys.argv[idx + 1]

    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    print("=" * 55)
    print("[공고 상세 정보 수집 테스트]")
    print("=" * 55)

    query = session.query(Announcement)
    if source:
        query = query.filter(Announcement.source == source)
    if not fetch_all:
        query = query.filter(Announcement.detail_fetched == False)

    targets = query.order_by(Announcement.created_at.desc()).all()
    print(f"\n대상 공고: {len(targets)}건 {'(전체 재수집)' if fetch_all else '(미수집 한정)'}")
    if source:
        print(f"출처 필터: {source}")

    if not targets:
        print("수집 대상이 없습니다.")
        session.close()
        return

    scrapers = {
        "ntis": NTISScraper(),
        "iris": IRISScraper(),
        "nrf":  NRFScraper(),
    }

    print("\n[수집 시작]\n")
    success = 0
    for ann in targets:
        scraper = scrapers.get(ann.source)
        if not scraper:
            continue

        dto = AnnouncementData(
            title=ann.title, url=ann.url, source=ann.source,
            category=ann.category or "", deadline=ann.deadline,
            posted_date=ann.posted_date, description=ann.description or "",
        )

        print(f"  [{ann.source.upper()}] {ann.title[:50]}")
        try:
            enriched = await scraper.scrape_detail(dto)

            # DB 업데이트
            changed = []
            if enriched.description and enriched.description != (ann.description or ""):
                ann.description = enriched.description
                changed.append(f"개요 {len(enriched.description)}자")
            if enriched.budget and enriched.budget != (ann.budget or ""):
                ann.budget = enriched.budget
                changed.append(f"예산: {enriched.budget[:30]}")
            if enriched.attachments and enriched.attachments != (ann.attachments or ""):
                ann.attachments = enriched.attachments
                cnt = len(enriched.attachments.split("\n"))
                changed.append(f"첨부{cnt}건")
            if enriched.deadline and not ann.deadline:
                ann.deadline = enriched.deadline
                changed.append(f"마감일: {enriched.deadline}")
            if enriched.category and not ann.category:
                ann.category = enriched.category
                changed.append(f"분야: {enriched.category[:20]}")

            ann.detail_fetched = True
            session.commit()
            success += 1

            if changed:
                print(f"    -> 수집: {', '.join(changed)}")
            else:
                print(f"    -> 상세 정보 없음 (페이지 구조 미일치)")

        except Exception as e:
            print(f"    -> 실패: {e}")

    print(f"\n{'='*55}")
    print(f"완료: {success}/{len(targets)}건 수집 성공")
    session.close()


if __name__ == "__main__":
    asyncio.run(main())
