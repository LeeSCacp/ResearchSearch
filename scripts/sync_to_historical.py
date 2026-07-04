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


# ----------------------------------------------------------------------
# historical 내보내기 / 부트스트랩 (GitHub Actions 캐시 유실 대비)
# ----------------------------------------------------------------------
# 배경: 로컬에서 수집한 NRF 5년치(1,700여 건)는 DB가 gitignore라 Actions에
# 전달되지 않는다. Actions가 자기 캐시 DB 기준으로 분석을 돌리면
# analytics.json이 0건으로 덮어써진다. 또한 점진 누적분이 캐시에만 있으면
# 캐시 유실 시 몇 달치 데이터가 사라진다.
# 해결: historical 테이블 전체를 docs/data/historical.json으로 커밋하고,
# 실행 시작 시 이 파일에서 DB에 없는 행을 복원(bootstrap)한다.

import json
from datetime import datetime as _dt

HISTORICAL_EXPORT_PATH = "docs/data/historical.json"

_DATE_FIELDS = ("posted_date", "deadline")


def export_historical(path: str = HISTORICAL_EXPORT_PATH) -> int:
    """historical 테이블 전체를 JSON으로 내보낸다. 내보낸 행 수 반환."""
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)
    try:
        rows = []
        for h in session.query(HistoricalAnnouncement).all():
            rows.append({
                "title": h.title, "url": h.url, "source": h.source,
                "category": h.category, "notice_type": h.notice_type,
                "posted_date": h.posted_date.isoformat() if h.posted_date else None,
                "deadline":    h.deadline.isoformat()    if h.deadline    else None,
                "year": h.year,
                "label_psychology": bool(h.label_psychology),
                "label_aging":      bool(h.label_aging),
                "label_psy_ai":     bool(h.label_psy_ai),
                "label_humanities": bool(h.label_humanities),
            })
        rows.sort(key=lambda r: r["url"])   # 안정 정렬 → git diff 최소화
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        logger.info(f"historical 내보내기: {len(rows)}건 → {path}")
        return len(rows)
    finally:
        session.close()


def bootstrap_from_export(path: str = HISTORICAL_EXPORT_PATH) -> int:
    """내보내기 파일에서 DB에 없는 행을 복원한다. 복원한 행 수 반환."""
    p = Path(path)
    if not p.exists():
        logger.info(f"부트스트랩 파일 없음 — 건너뜀: {path}")
        return 0

    with open(p, encoding="utf-8") as f:
        rows = json.load(f)

    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)
    restored = 0
    try:
        existing_urls = {
            u for (u,) in session.query(HistoricalAnnouncement.url).all()
        }
        for r in rows:
            if not r.get("url") or r["url"] in existing_urls:
                continue
            kwargs = dict(r)
            for fld in _DATE_FIELDS:
                v = kwargs.get(fld)
                kwargs[fld] = _dt.strptime(v, "%Y-%m-%d").date() if v else None
            session.add(HistoricalAnnouncement(**kwargs))
            restored += 1
        session.commit()
        logger.info(f"부트스트랩: 파일 {len(rows)}건 중 {restored}건 복원")
        return restored
    finally:
        session.close()


def main():
    logger.info("운영 DB → historical 동기화 시작")
    restored = bootstrap_from_export()
    stats = sync()
    export_historical()
    logger.info(
        f"부트스트랩 {restored}건 / 확인 {stats['checked']}건 / "
        f"신규 {stats['added']}건 / 이미 있음 {stats['skipped']}건"
    )
    if stats["by_source"]:
        logger.info(f"신규 출처별: {stats['by_source']}")


if __name__ == "__main__":
    main()
