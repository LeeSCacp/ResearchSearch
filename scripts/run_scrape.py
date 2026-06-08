"""GitHub Actions 진입점: 스크래핑 → 알림 → JSON 내보내기.

실행 방법:
  python scripts/run_scrape.py

GitHub Actions 워크플로(scrape.yml)에서 호출된다.
로컬에서도 동일하게 실행 가능 (테스트/수동 갱신용).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (Actions runner에서 실행 시 필요)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from src.scheduler import run_scraping_cycle, run_reminder_cycle
    from src.export import export_announcements, export_synonyms

    logger.info("=== 스크래핑 사이클 시작 ===")
    await run_scraping_cycle()

    logger.info("=== D-day 리마인더 사이클 시작 ===")
    await run_reminder_cycle()

    logger.info("=== JSON 내보내기 시작 ===")
    count = export_announcements("docs/data/announcements.json")
    export_synonyms("docs/data/synonyms.json")

    # Phase 16: 운영 DB → historical 동기화 + 분석 갱신
    # NTIS/IRIS는 5년치 직접 수집 불가 → 매 사이클마다 점진 누적
    logger.info("=== historical 동기화 시작 ===")
    try:
        from scripts.sync_to_historical import sync
        stats = sync()
        logger.info(
            f"동기화 완료 — 신규 {stats['added']}건 "
            f"(출처별: {stats.get('by_source', {})})"
        )

        if stats["added"] > 0:
            logger.info("=== 신규 데이터 반영 — 분석 재실행 ===")
            from src.analytics.historical import run_full_analysis
            from src.config import load_config
            from src.models.announcement import init_db, get_session
            import json
            from datetime import datetime, timezone
            from pathlib import Path as _Path

            config = load_config()
            engine = init_db(config["database"]["path"])
            session = get_session(engine)
            try:
                results = run_full_analysis(session)
                out = {
                    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    "labels": results,
                }
                p = _Path("docs/data/analytics.json")
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
                logger.info("analytics.json 갱신 완료")
            finally:
                session.close()
    except Exception as e:
        logger.error(f"historical 동기화/분석 실패 (무시 진행): {e}")

    logger.info(f"=== 완료: {count}건 내보냄 ===")


if __name__ == "__main__":
    asyncio.run(main())
