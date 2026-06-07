"""HistoricalAnnouncement 분야 자동 라벨링.

제목 + 카테고리만으로 4개 라벨을 다중 부여한다 (광범위 라벨링).
상세 페이지는 수집하지 않으므로 빠르고 비용 0.

라벨:
  - label_psychology  : 심리학 전반 (심리·인지·정신건강·상담·발달 등)
  - label_aging       : 노화·치매·고령·노년 관련
  - label_psy_ai      : 심리학 키워드 AND AI 키워드 동시
  - label_humanities  : 인문사회 분야 전반 (카테고리 기반 — 가장 광범위)

실행:
  python scripts/label_historical.py
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("label")

from src.config import load_config
from src.models.announcement import init_db, get_session, HistoricalAnnouncement


# ----------------------------------------------------------------------
# 라벨 규칙 (대소문자 무시, 부분 문자열 매칭)
# ----------------------------------------------------------------------

PSYCHOLOGY_KWS = [
    "심리", "인지", "정신건강", "정신의학", "상담", "발달",
    "정서", "행동과학", "행동심리", "인지과학", "신경과학", "뇌과학",
    "휴먼팩터", "사회과학",  # 사회과학은 광범위하지만 심리학 포함
]

AGING_KWS = [
    "노화", "치매", "고령", "노년", "노인", "초고령",
    "건강노화", "인지노화", "퇴행성", "알츠하이머",
]

AI_KWS = [
    "AI", "인공지능", "머신러닝", "딥러닝", "기계학습",
    "데이터사이언스", "빅데이터",
    # 자연어처리/컴퓨터비전 등은 너무 좁아 제외 (포괄적 AI만)
]

# 인문사회는 카테고리 기준 (제목으로는 잘 안 잡힘)
HUMANITIES_CAT_KWS = [
    "인문사회", "인문학", "사회과학", "인문",
]


def _text_for(ann: HistoricalAnnouncement) -> str:
    return f"{ann.title or ''} {ann.category or ''}".lower()


def _has_any(text: str, kws: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in kws)


def label_one(ann: HistoricalAnnouncement) -> dict[str, bool]:
    """단일 공고에 4개 라벨 부여."""
    text = _text_for(ann)
    cat  = (ann.category or "").lower()

    label_psy        = _has_any(text, PSYCHOLOGY_KWS)
    label_aging      = _has_any(text, AGING_KWS)
    # 인문사회는 카테고리 기반만 (광범위 모집단)
    label_humanities = _has_any(cat, HUMANITIES_CAT_KWS)
    # 심리+AI 융합: 심리학 OR 노화(인지노화 등) AND AI
    label_psy_ai     = (label_psy or label_aging) and _has_any(text, AI_KWS)

    return {
        "psychology": label_psy,
        "aging":      label_aging,
        "psy_ai":     label_psy_ai,
        "humanities": label_humanities,
    }


def relabel_all() -> dict:
    """전체 공고 재라벨링. 통계 반환."""
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    counts = {"psychology": 0, "aging": 0, "psy_ai": 0, "humanities": 0, "no_label": 0}
    total = 0

    try:
        for ann in session.query(HistoricalAnnouncement).all():
            labels = label_one(ann)
            ann.label_psychology = labels["psychology"]
            ann.label_aging      = labels["aging"]
            ann.label_psy_ai     = labels["psy_ai"]
            ann.label_humanities = labels["humanities"]

            total += 1
            for k, v in labels.items():
                if v:
                    counts[k] += 1
            if not any(labels.values()):
                counts["no_label"] += 1

        session.commit()
    finally:
        session.close()

    return {"total": total, **counts}


def show_samples_per_label(n: int = 5) -> None:
    """각 라벨별 샘플 N건 출력 — 라벨링 품질 검토용."""
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    try:
        for label_col, label_name in [
            (HistoricalAnnouncement.label_psychology, "심리학"),
            (HistoricalAnnouncement.label_aging,      "노화/치매/고령"),
            (HistoricalAnnouncement.label_psy_ai,     "심리+AI 융합"),
            (HistoricalAnnouncement.label_humanities, "인문사회 전반"),
        ]:
            print(f"\n=== [{label_name}] 샘플 {n}건 ===")
            samples = (
                session.query(HistoricalAnnouncement)
                .filter(label_col == True)
                .order_by(HistoricalAnnouncement.posted_date.desc())
                .limit(n)
                .all()
            )
            for s in samples:
                cat = (s.category or '(빈값)')[:70]
                print(f"  · [{s.year}] {s.title[:60]}")
                print(f"      분야: {cat}")
    finally:
        session.close()


def main():
    logger.info("라벨링 시작")
    stats = relabel_all()
    logger.info("라벨링 완료")
    print()
    print(f"━━━ 전체 {stats['total']}건 ━━━")
    print(f"  심리학 전반        : {stats['psychology']:>4}건")
    print(f"  노화/치매/고령     : {stats['aging']:>4}건")
    print(f"  심리+AI 융합       : {stats['psy_ai']:>4}건")
    print(f"  인문사회 전반      : {stats['humanities']:>4}건")
    print(f"  미분류             : {stats['no_label']:>4}건")
    show_samples_per_label(n=5)


if __name__ == "__main__":
    main()
