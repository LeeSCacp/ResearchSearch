"""4가지 패턴 분석 + Top-K 사업 실행 + JSON 출력 (v2 — 고도화).

출력:
  - docs/data/analytics.json
  - 콘솔 요약 (라벨별 시즌성·반복사업·트렌드·마감·Top사업)
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
from src.analytics.historical import run_full_analysis


KR_MONTH = ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"]
STRENGTH_KR = {"strong": "강함", "moderate": "보통", "weak": "약함"}
CONFIDENCE_KR = {"high": "높음", "medium": "보통", "low": "낮음"}


def print_summary(results: dict):
    for label, data in results.items():
        print(f"\n━━━━━━ [{data['label_kr']}] ━━━━━━")

        # 1. 시즌성
        s = data['seasonality']
        if s['total'] == 0:
            print("  (데이터 없음)")
            continue
        strength_str = STRENGTH_KR.get(s.get('seasonality_strength'), '-')
        chi_str = f", χ²={s.get('chi_square')}" if s.get('chi_square') is not None else ""
        print(f"  📅 시즌성 (총 {s['total']}건):")
        print(f"     월별: {s['monthly']}")
        print(f"     정점: {KR_MONTH[s['peak_month']-1]} ({s['peak_count']}건), "
              f"저점: {KR_MONTH[s['low_month']-1]} ({s['low_count']}건)")
        print(f"     강도: {strength_str}{chi_str} / "
              f"정점월±1 비율: {s.get('peak_window_pct')}%")

        # 2. 반복 사업 (상위 5개)
        clusters = data['recurring']
        if clusters:
            print(f"  🔄 반복 사업 클러스터 {len(clusters)}개 (상위 5):")
            for c in clusters[:5]:
                conf = CONFIDENCE_KR.get(c.get('confidence'), '-')
                next_str = c['next_predicted'] or '예측불가'
                win = ""
                if c.get('next_window_low') and c.get('next_window_high'):
                    win = f" [{c['next_window_low']} ~ {c['next_window_high']}]"
                print(f"     · {c['occurrences']}회 [{conf}] | {c['title_sample'][:50]}")
                print(f"       주기 {c['avg_interval_days']}±{c.get('stdev_interval_days','?')}일")
                print(f"       다음: {next_str}{win}")
        else:
            print(f"  🔄 반복 사업: 없음")

        # 3. 트렌드
        t = data['trend']
        if t['total'] > 0:
            trend_str = ' / '.join(f"{y}:{c}" for y, c in zip(t['years'], t['yearly_counts']))
            note = " (현재년 진행중)" if t.get('current_year_incomplete') else ""
            yoy = t.get('avg_yearly_change')
            slope = t.get('regression_slope')
            print(f"  📈 트렌드{note}: {trend_str}")
            print(f"     연평균 변화: {yoy}건 / 회귀 기울기: {slope}")

        # 4. 마감
        d = data['deadline']
        if d.get('total', 0) > 0:
            print(f"  ⏳ 마감 기간 (총 {d['total']}건):")
            print(f"     평균 {d['mean']}일 / 중앙값 {d['median']}일 "
                  f"/ IQR {d['q1']}~{d['q3']}일 ({d['iqr']}일)")
            print(f"     7일 이내 {d['under_7_pct']}% / 14일 이내 {d['under_14_pct']}%")

        # 5. Top 사업
        tops = data['top_types']
        if tops:
            print(f"  🏆 Top 사업 (상위 5):")
            for tp in tops[:5]:
                print(f"     · {tp['count']:>3}회 | {tp['name']}")


def save_json(results: dict, path: str = "docs/data/analytics.json"):
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
        logger.info("4가지 패턴 분석 + Top-K 시작 (5개 모집단)")
        results = run_full_analysis(session)
        print_summary(results)
        save_json(results)
    finally:
        session.close()


if __name__ == "__main__":
    main()
