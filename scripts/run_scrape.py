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

    logger.info(f"=== 완료: {count}건 내보냄 ===")


if __name__ == "__main__":
    asyncio.run(main())
