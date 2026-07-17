"""로컬 개발 전용 — FastAPI 서버 + APScheduler 통합.

GitHub Pages 배포 환경에서는 사용하지 않는다.
  - 배포용 진입점: scripts/run_scrape.py  (GitHub Actions에서 실행)
  - 로컬 대시보드:  python src/main.py  → http://localhost:8000
  - 로컬 의존성:    pip install -r requirements-dev.txt
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import load_config
from src.models.announcement import init_db
from src.scheduler import run_scraping_cycle, run_daily_digest
from src.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app):
    config   = load_config()
    init_db(config["database"]["path"])

    interval = config.get("scraping", {}).get("interval_hours", 6)

    # 신규 공고 수집: N시간 간격
    scheduler.add_job(
        run_scraping_cycle,
        "interval",
        hours=interval,
        id="scraping_cycle",
        replace_existing=True,
    )

    # 일일 다이제스트(신규+리마인더 통합): 매일 오전 9시
    scheduler.add_job(
        run_daily_digest,
        "cron",
        hour=9,
        minute=0,
        id="daily_digest",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(f"스케줄러 시작 — 스크래핑: {interval}시간 간격 / 다이제스트: 매일 09:00")

    # 서버 기동 직후 1회 안전 실행
    async def _safe_initial_scrape():
        try:
            await run_scraping_cycle()
        except Exception as e:
            logger.warning(f"초기 스크래핑 실패 (무시됨): {e}")

    asyncio.create_task(_safe_initial_scrape())

    yield

    scheduler.shutdown()
    logger.info("스케줄러 종료")


app = create_app()
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    # reload=True는 Windows에서 OSError 발생 → 제거
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)
