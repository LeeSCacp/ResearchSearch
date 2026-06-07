"""4가지 패턴 분석 실행 + JSON 출력.

출력:
  - docs/data/analytics.json  (시각화 대시보드용)
  - 콘솔 요약

실행:
  python scripts/analyze_historical.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("analyze")

from src.config import load_config
from src.models.announcement import init_db, get_session
from src.analytics.historical import (
    run_full_analysis, LABEL_NAMES_KR,
)


KR_MONTH = ["1월", "2월", "3월", "4월", "5월", "6월",
            "7월", "8월", "9월", "10월", "11월", "12월"]


def print_summary(results: dict):
    """콘솔용 보고서."""
    for label, data in results.items():
        print(f"\n━━━━━━ [{data['label_kr']}] ━━━━━━")

        # 1. 시즌성
        s = data['seasonality']
        if s['total'] == 0:
            print("  (데이터 없음)")
            continue
        print(f"  📅 시즌성 (총 {s['total']}건):")
        print(f"     월별: {s['monthly']}")
        print(f"     정점: {KR_MONTH[s['peak_month']-1]} ({s['peak_count']}건)")
        print(f"     저점: {KR_MONTH[s['low_month']-1]} ({s['low_count']}건)")
        print(f"     월평균: {s['avg_per_month']}건")

        # 2. 반복 사업 (상위 5개)
        clusters = data['recurring']
        if clusters:
            print(f"  🔄 반복 사업 클러스터 {len(clusters)}개 (상위 5):")
            for c in clusters[:5]:
                next_str = c['next_predicted'] or '예측불가'
                print(f"     · {c['occurrences']}회 | {c['title_sample'][:55]}")
                print(f"       주기 {c['avg_interval_days']}일 → 다음 예상: {next_str}")
        else:
            print(f"  🔄 반복 사업: 없음")

        # 3. 트렌드
        t = data['trend']
        if t['total'] > 0:
            trend_str = ' / '.join(f"{y}:{c}" for y, c in zip(t['years'], t['yearly_counts']))
            note = " (2026 진행중)" if t.get('current_year_incomplete') else ""
            yoy = t.get('avg_yearly_change')
            direction = ('증가' if yoy and yoy > 0 else
                         '감소' if yoy and yoy < 0 else '보합')
            print(f"  📈 트렌드{note}: {trend_str}")
            print(f"     연평균 변화: {yoy} ({direction})")

        # 4. 마감일 패턴
        d = data['deadline']
        if d['total'] > 0:
            print(f"  ⏳ 마감일 패턴 (총 {d['total']}건):")
            print(f"     평균 {d['mean']}일 / 중앙값 {d['median']}일 / "
                  f"표준편차 {d['stdev']}일")
            print(f"     범위: {d['min']}~{d['max']}일")


def save_json(results: dict, path: str = "docs/data/analytics.json"):
    """시각화 대시보드용 JSON 저장."""
    out_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "labels": results,
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"분석 결과 저장: {path}")


def main():
    config = load_config()
    engine = init_db(config["database"]["path"])
    session = get_session(engine)

    try:
        logger.info("4가지 패턴 분석 시작 (5개 모집단)")
        results = run_full_analysis(session)
        print_summary(results)
        save_json(results)
    finally:
        session.close()


if __name__ == "__main__":
    main()
