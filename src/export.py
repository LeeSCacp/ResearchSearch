"""SQLite DB → GitHub Pages 용 JSON 내보내기.

GitHub Actions 워크플로에서 스크래핑 직후 호출한다.
생성된 JSON 파일은 docs/data/ 에 저장되며 GitHub Pages가 서빙한다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.config import load_config
from src.models.announcement import Announcement, NotificationLog, init_db, get_session
from src.scrapers.base import AnnouncementData
from src.filters.engine import FilterEngine

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

        # 알림 이력 매핑 {announcement_id: [{event, channel, success, error?}]}
        # 성공·실패 모두 포함 — 대시보드에서 발송 시도 여부를 확인할 수 있도록
        all_ids = [a.id for a in announcements]
        log_map: dict[int, list[dict]] = {}
        if all_ids:
            for log in (
                session.query(NotificationLog)
                .filter(NotificationLog.announcement_id.in_(all_ids))
                .order_by(NotificationLog.sent_at.asc())
                .all()
            ):
                entry: dict = {
                    "event":   log.event_type,
                    "channel": log.channel,
                    "success": log.success,
                }
                if not log.success and log.error_message:
                    entry["error"] = log.error_message
                log_map.setdefault(log.announcement_id, []).append(entry)

        # FilterEngine으로 각 공고의 관련성 계산 (프론트 '관련 공고만' 토글용)
        filter_cfg = config.get("filters", {})
        filter_engine = FilterEngine(
            keywords=filter_cfg.get("keywords", []),
            categories=filter_cfg.get("categories", []),
            exclude_keywords=filter_cfg.get("exclude_keywords", []),
        )

        def _is_relevant(ann: Announcement) -> bool:
            dto = AnnouncementData(
                title=ann.title or "", url=ann.url or "", source=ann.source or "",
                category=ann.category or "", deadline=ann.deadline,
                posted_date=ann.posted_date, description=ann.description or "",
            )
            return filter_engine.matches(dto)

        def _norm_title(title: str) -> str:
            """제목 정규화: 공백·특수문자 제거, 소문자 — 중복 감지용."""
            return re.sub(r'[\s\W]+', '', (title or "")).lower()

        # 출처별 중복 공고 병합 (예: 같은 공고가 NTIS + IRIS 동시 게재)
        seen_keys: dict[str, dict] = {}
        items_deduped: list[dict] = []
        for a in announcements:
            raw = {
                **a.to_dict(),
                "notification_logs": log_map.get(a.id, []),
                "is_relevant": _is_relevant(a),
            }
            key = _norm_title(a.title)
            if key and key in seen_keys:
                existing = seen_keys[key]
                # extra_sources 초기화 (첫 중복 발견 시)
                if "extra_sources" not in existing:
                    existing["extra_sources"] = [existing["source"]]
                if raw["source"] not in existing["extra_sources"]:
                    existing["extra_sources"].append(raw["source"])
                # 더 풍부한 데이터로 보완
                if not existing.get("description") and raw.get("description"):
                    existing["description"] = raw["description"]
                if not existing.get("budget") and raw.get("budget"):
                    existing["budget"] = raw["budget"]
            else:
                if key:
                    seen_keys[key] = raw
                items_deduped.append(raw)

        data = {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "total": len(items_deduped),
            "items": items_deduped,
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
