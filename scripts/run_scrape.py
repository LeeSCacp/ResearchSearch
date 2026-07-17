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
    from src.scheduler import run_scraping_cycle, run_daily_digest
    from src.export import export_announcements, export_synonyms, restore_announcements

    # 캐시 유실 대비: 커밋된 JSON에서 운영 DB 복원 (중복 알림 방지)
    logger.info("=== 운영 DB 복원 확인 ===")
    try:
        restore_announcements()
    except Exception as e:
        logger.error(f"운영 DB 복원 실패 (무시 진행): {e}")

    logger.info("=== 스크래핑 사이클 시작 ===")
    health_alerts = await run_scraping_cycle() or []

    # 일일 다이제스트: 09:00 KST 이후 첫 사이클에서 하루 1통 발송
    # (매 사이클 호출해도 DigestLog + 시각 판정으로 중복 발송 없음)
    logger.info("=== 일일 다이제스트 확인 ===")
    digest_status = await run_daily_digest() or {}

    # 침묵 고장 감지: 경보가 있으면 플래그 파일 기록
    # → 워크플로 마지막 단계가 이 파일을 보고 실패 처리 (GitHub 실패 알림 발송)
    # 데이터 커밋 이후에 실패시키기 위해 여기서 exit하지 않는다.
    alerts = list(health_alerts)
    if digest_status.get("send_failed"):
        alerts.append("다이제스트 이메일 발송 실패 (같은 날 다음 사이클 재시도 예정)")
    flag = Path("data/health_fail.flag")
    if alerts:
        flag.parent.mkdir(exist_ok=True)
        flag.write_text("\n".join(alerts), encoding="utf-8")
        for a in alerts:
            logger.error(f"[경보] {a}")
    elif flag.exists():
        flag.unlink()   # 회복 시 플래그 제거

    logger.info("=== JSON 내보내기 시작 ===")
    count = export_announcements("docs/data/announcements.json")
    export_synonyms("docs/data/synonyms.json")

    # Phase 16: 운영 DB → historical 동기화 + 분석 갱신
    # NTIS/IRIS는 5년치 직접 수집 불가 → 매 사이클마다 점진 누적
    # 순서: 부트스트랩(커밋된 파일 → DB 복원) → 동기화 → 내보내기 → 분석
    # 부트스트랩 덕분에 Actions 캐시 DB에도 NRF 5년치가 복원되어
    # analytics.json이 0건으로 덮어써지는 문제와 캐시 유실 위험을 함께 방지.
    logger.info("=== historical 동기화 시작 ===")
    try:
        from scripts.sync_to_historical import (
            bootstrap_from_export, sync, export_historical,
        )
        restored = bootstrap_from_export()
        stats = sync()
        export_historical()
        logger.info(
            f"동기화 완료 — 부트스트랩 {restored}건, 신규 {stats['added']}건 "
            f"(출처별: {stats.get('by_source', {})})"
        )

        if restored > 0 or stats["added"] > 0:
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
