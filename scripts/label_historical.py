"""HistoricalAnnouncement 분야 자동 라벨링 (v2 — 보강 버전).

광범위 라벨링이지만 NRF 사업 카테고리 구조를 활용해 모집단을 확장한다.
"심리/노화"가 사업명에 직접 안 나오는 NRF 특성을 보완하기 위해:

  1. 키워드 매칭 (제목 + 카테고리)
  2. 카테고리 직접 매핑 (심리학자 잠재 신청 사업 그룹)
  3. 인문사회 분야 학술연구지원사업의 학술군 → 심리학 잠재 풀로 포함

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


# ======================================================================
# 라벨 규칙 — 키워드 + 카테고리 매핑
# ======================================================================

# 심리학 (제목·카테고리 어디든)
PSYCHOLOGY_KWS = [
    "심리", "인지", "정신건강", "정신의학", "상담", "발달",
    "정서", "행동과학", "행동심리", "인지과학", "신경과학", "뇌과학",
    "휴먼팩터", "의사결정", "주의", "기억", "지각", "학습",
]

# 심리학자 잠재 신청 사업 카테고리 (인문사회 학술군 + 사회과학 + 융합)
# 카테고리에 이 문자열이 포함되면 심리학 라벨 부여
PSYCHOLOGY_CAT_KWS = [
    "사회과학연구",          # SSK
    "사회과학",
    "한국사회과학연구",
    "학술연구교수",          # 인문사회 학술연구교수
    "인문사회연구소",
    "글로벌인문사회",
    "인문사회 융합",
    "융합인재",              # 인문사회 융합인재
    "학문후속세대",          # 후속세대지원 (심리학 박사·박사후 다수)
    "박사후국내연수",
    "박사후국외연수",
    "신진연구",              # 신진연구자
    "중견연구",              # 중견연구자
]

# 노화·치매·고령
AGING_KWS = [
    "노화", "치매", "고령", "노년", "노인", "초고령",
    "건강노화", "인지노화", "퇴행성", "알츠하이머",
    "노인복지", "노년기", "건강수명",
]
AGING_CAT_KWS = [
    "치매의료기술",
    "생체노화",
    "노화 리프로그래밍",
]

# 인문사회 전반 (가장 광범위, 카테고리 기반)
HUMANITIES_CAT_KWS = [
    "인문사회", "인문학", "사회과학", "인문",
]

# AI / 디지털 (심리+AI 융합 판정에 사용)
AI_KWS = [
    "AI", "인공지능", "머신러닝", "딥러닝", "기계학습",
    "데이터사이언스", "빅데이터", "디지털치료", "디지털 치료",
    "디지털헬스", "디지털 헬스", "디지털전환", "디지털 전환",
    "HCI", "휴먼-AI", "디지털혁신",
]


# ======================================================================
# 매칭 헬퍼
# ======================================================================

def _text_for(ann: HistoricalAnnouncement) -> str:
    return f"{ann.title or ''} {ann.category or ''}"


def _has_any(text: str, kws: list[str]) -> bool:
    tl = text.lower()
    return any(kw.lower() in tl for kw in kws)


def label_one(ann: HistoricalAnnouncement) -> dict[str, bool]:
    """단일 공고에 4개 라벨 부여."""
    text = _text_for(ann)
    cat  = (ann.category or "")

    # 심리학 = 키워드 OR 사업 카테고리 매핑
    label_psy = _has_any(text, PSYCHOLOGY_KWS) or _has_any(cat, PSYCHOLOGY_CAT_KWS)

    # 노화 = 키워드 OR 카테고리 매핑
    label_aging = _has_any(text, AGING_KWS) or _has_any(cat, AGING_CAT_KWS)

    # 인문사회 = 카테고리 기반 (가장 광범위)
    label_humanities = _has_any(cat, HUMANITIES_CAT_KWS)

    # 심리+AI 융합 = (심리 OR 노화) AND AI 키워드
    label_psy_ai = (label_psy or label_aging) and _has_any(text, AI_KWS)

    return {
        "psychology": label_psy,
        "aging":      label_aging,
        "psy_ai":     label_psy_ai,
        "humanities": label_humanities,
    }


# ======================================================================
# 실행
# ======================================================================

def relabel_all() -> dict:
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    counts = {"psychology": 0, "aging": 0, "psy_ai": 0, "humanities": 0,
              "any_label": 0, "no_label": 0}
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
            if any(labels.values()):
                counts["any_label"] += 1
            else:
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
                .limit(n).all()
            )
            for s in samples:
                cat = (s.category or '(빈값)')[:70]
                print(f"  · [{s.year}] {s.title[:60]}")
                print(f"      분야: {cat}")
    finally:
        session.close()


def main():
    logger.info("라벨링 v2 (보강) 시작")
    stats = relabel_all()
    logger.info("라벨링 완료")
    print()
    print(f"━━━ 전체 {stats['total']}건 ━━━")
    print(f"  심리학 (잠재 풀 포함) : {stats['psychology']:>4}건")
    print(f"  노화/치매/고령        : {stats['aging']:>4}건")
    print(f"  심리+AI 융합          : {stats['psy_ai']:>4}건")
    print(f"  인문사회 전반         : {stats['humanities']:>4}건")
    print(f"  ───────────────────")
    print(f"  관심 분야 (합집합)    : {stats['any_label']:>4}건  ← 분석 모집단")
    print(f"  무관 (분석 제외)      : {stats['no_label']:>4}건")
    show_samples_per_label(n=5)


if __name__ == "__main__":
    main()
