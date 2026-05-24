"""SQLite DB → GitHub Pages 용 JSON 내보내기.

GitHub Actions 워크플로에서 스크래핑 직후 호출한다.
생성된 JSON 파일은 docs/data/ 에 저장되며 GitHub Pages가 서빙한다.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import yaml

from src.config import load_config
from src.models.announcement import Announcement, NotificationLog, init_db, get_session

logger = logging.getLogger(__name__)


def export_announcements(
    output_path: str = "docs/data/announcements.json",
) -> int:
    """DB의 모든 공고를 JSON으로 내보낸다. 내보낸 건수를 반환한다."""
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    try:
        # 마감 안 된 공고 우선(ASC), 마감일 없는 것은 뒤로, 이후 등록 최신순
        announcements = (
            session.query(Announcement)
            .order_by(
                Announcement.deadline.asc().nulls_last(),
                Announcement.created_at.desc(),
            )
            .all()
        )

        # 알림 이력 매핑 {announcement_id: [event_types]}
        all_ids = [a.id for a in announcements]
        log_map: dict[int, list[str]] = {}
        if all_ids:
            for log in (
                session.query(NotificationLog)
                .filter(
                    NotificationLog.announcement_id.in_(all_ids),
                    NotificationLog.success.is_(True),
                )
                .all()
            ):
                log_map.setdefault(log.announcement_id, []).append(log.event_type)

        data = {
            "updated_at": date.today().isoformat(),
            "total": len(announcements),
            "items": [
                {**a.to_dict(), "notification_logs": log_map.get(a.id, [])}
                for a in announcements
            ],
        }

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"[export] {len(announcements)}건 → {output_path}")
        return len(announcements)

    finally:
        session.close()


def export_synonyms(
    output_path: str = "docs/data/synonyms.json",
) -> None:
    """synonyms.yaml → JSON 변환 (정적 프론트엔드 검색용).

    출력 형태: { "term_lower": ["동의어1", "동의어2", ...], ... }
    어떤 단어로 검색해도 같은 그룹의 모든 단어가 포함되도록 역방향 인덱스 구성.
    """
    yaml_path = Path(__file__).resolve().parent / "filters" / "synonyms.yaml"
    with open(yaml_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    term_map: dict[str, list[str]] = {}
    for group in raw.get("groups", []):
        terms = [t.lower() for t in group.get("terms", [])]
        for t in terms:
            term_map[t] = terms  # 자신 포함 전체 그룹

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(term_map, f, ensure_ascii=False, indent=2)

    logger.info(f"[export] 동의어 사전 {len(term_map)}개 항목 → {output_path}")
