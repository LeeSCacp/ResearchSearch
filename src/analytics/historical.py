"""NRF 과거 공고 패턴 분석 (v2 — 고도화).

분석 모집단:
  - "interest" : 4개 라벨(인문사회/심리학/노화/심리AI) 중 하나라도 해당하는 공고
                 ("전체"는 사용자 무관 공고를 포함하므로 제외)
  - "humanities" / "psychology" / "aging" / "psy_ai" : 단일 라벨

각 모집단에 다음 5가지 분석 수행:
  1. 시즌성 (월별 분포 + 정점·저점 + 카이제곱 검정 + 누적 분포)
  2. 반복 사업 클러스터 + 다음 공고 예측 (평균 ± 표준편차 신뢰구간)
  3. 5년 트렌드 (연도별 + 연평균 변화 + 단순 회귀)
  4. 마감 기간 분포 (평균/중앙값/IQR + 7일 이내 비율)
  5. (신규) Top-K 사업 — 라벨별 빈도 상위 사업
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import or_

from src.config import today_kst
from src.models.announcement import HistoricalAnnouncement


# ======================================================================
# 라벨 필터 정의 — "전체" 제거, "interest"(관심 분야 합집합) 추가
# ======================================================================

def _interest_filter(q):
    """관심 4개 라벨 중 하나라도 True인 공고만."""
    return q.filter(or_(
        HistoricalAnnouncement.label_humanities == True,
        HistoricalAnnouncement.label_psychology == True,
        HistoricalAnnouncement.label_aging == True,
        HistoricalAnnouncement.label_psy_ai == True,
    ))


LABEL_FILTERS = {
    "interest":   _interest_filter,
    "humanities": lambda q: q.filter(HistoricalAnnouncement.label_humanities == True),
    "psychology": lambda q: q.filter(HistoricalAnnouncement.label_psychology == True),
    "aging":      lambda q: q.filter(HistoricalAnnouncement.label_aging == True),
    "psy_ai":     lambda q: q.filter(HistoricalAnnouncement.label_psy_ai == True),
}

LABEL_NAMES_KR = {
    "interest":   "관심 분야 통합",
    "humanities": "인문사회 전반",
    "psychology": "심리학 전반",
    "aging":      "노화/치매/고령",
    "psy_ai":     "심리+AI 융합",
}

LABEL_ORDER = ["interest", "humanities", "psychology", "aging", "psy_ai"]


def _query(session, label: str):
    """라벨별 기본 쿼리. posted_date NOT NULL인 공고만 분석 대상."""
    base = session.query(HistoricalAnnouncement).filter(
        HistoricalAnnouncement.posted_date.isnot(None)
    )
    fn = LABEL_FILTERS.get(label, LABEL_FILTERS["interest"])
    return fn(base)


# ======================================================================
# 1. 시즌성 — 월별 분포 + 카이제곱 + 누적
# ======================================================================

def _chi_square_uniform(observed: list[int]) -> float | None:
    """관찰값이 균등분포와 다른 정도 — chi-square statistic.

    None 반환: 표본 < 12.
    값이 클수록 균등분포에서 더 벗어남 (즉 시즌성 강함).
    자유도 11에서 임계값: 19.68 (p<0.05), 24.72 (p<0.01).
    """
    total = sum(observed)
    if total < 12:
        return None
    expected = total / len(observed)
    return round(sum((o - expected) ** 2 / expected for o in observed), 2)


def analyze_seasonality(session, label: str) -> dict:
    items = _query(session, label).all()
    if not items:
        return {"total": 0, "monthly": [0]*12, "peak_month": None,
                "low_month": None, "avg_per_month": 0,
                "chi_square": None, "seasonality_strength": None,
                "cumulative_pct": [0]*12}

    monthly = [0] * 12
    for ann in items:
        if ann.posted_date:
            monthly[ann.posted_date.month - 1] += 1

    avg = sum(monthly) / 12
    peak_idx = monthly.index(max(monthly))
    low_idx  = monthly.index(min(monthly))

    # 카이제곱 (균등분포 대비 시즌성 강도)
    chi = _chi_square_uniform(monthly)
    strength = None
    if chi is not None:
        # 자유도 11 기준 강도 분류
        if chi > 24.72:   strength = "strong"     # p<0.01
        elif chi > 19.68: strength = "moderate"   # p<0.05
        else:             strength = "weak"

    # 누적 분포 — 1월부터 N월까지 누적 비율 (사전 신청 준비 시점 산정용)
    total = sum(monthly)
    cum = []
    s = 0
    for m in monthly:
        s += m
        cum.append(round(100 * s / total, 1))

    # 정점 인접 3개월 (정점 ±1)
    p = peak_idx
    peak_window = [(p - 1) % 12, p, (p + 1) % 12]
    peak_window_count = sum(monthly[i] for i in peak_window)
    peak_window_pct = round(100 * peak_window_count / total, 1)

    return {
        "total":         len(items),
        "monthly":       monthly,
        "peak_month":    peak_idx + 1,
        "peak_count":    monthly[peak_idx],
        "low_month":     low_idx + 1,
        "low_count":     monthly[low_idx],
        "avg_per_month": round(avg, 1),
        "chi_square":          chi,
        "seasonality_strength": strength,   # "strong"/"moderate"/"weak"/None
        "cumulative_pct":      cum,
        "peak_window_pct":     peak_window_pct,  # 정점월 ±1 누적 비율
    }


# ======================================================================
# 2. 반복 사업 식별 + 다음 공고 예측 (신뢰구간 포함)
# ======================================================================

_YEAR_PATTERN  = re.compile(r'(19|20)\d{2}\s*년도?')
_NUM_BRACKETS  = re.compile(r'[\(\[\{][^)\]\}]*[\)\]\}]')
_ROUND_PATTERN = re.compile(r'(\d+)\s*(차|호|회)\b')
_SUFFIX_NOISE  = [
    "신규과제", "신규지원", "공고", "공모", "재공모", "수정",
    "신청요강", "사전공고", "추가공고", "추가공모", "연장공고",
    "(연장공고)", "공모(접수기간)", "지원 대상과제",
    "공모(공고문)", "기획을 위한 기술수요조사",
]
_WS_RE = re.compile(r'\s+')


def normalize_title(title: str) -> str:
    t = title or ""
    t = _YEAR_PATTERN.sub('', t)
    t = _NUM_BRACKETS.sub('', t)
    t = _ROUND_PATTERN.sub('', t)
    for s in _SUFFIX_NOISE:
        t = t.replace(s, '')
    t = re.sub(r'[·.,;:!?_\-]+', ' ', t)
    t = _WS_RE.sub(' ', t).strip()
    return t.lower()[:80]


# 사업별 권장 사전 준비 기간 (마감 기간 외 추가로 필요한 일수)
_PREP_BUFFER_DAYS = 30


def _circular_month_stats(months: list[int]) -> tuple[int | None, float | None]:
    """공고 월의 정점 + 변동성(원형 통계 근사).

    NRF 사업이 보통 연 1~2회라 단순 통계로도 충분.
    반환: (정점 월, 월 단위 표준편차)
    """
    if not months:
        return None, None
    counter = Counter(months)
    peak = counter.most_common(1)[0][0]
    # 정점월 기준 원형 거리 (예: 11월~1월 같이 연말연시 사업 처리)
    distances = [min(abs(m - peak), 12 - abs(m - peak)) for m in months]
    if len(distances) >= 2:
        stdev = round(statistics.stdev(distances), 1)
    else:
        stdev = 0.0
    return peak, stdev


def _posting_consistency(month_stdev: float | None) -> str | None:
    """공고일 일관성 라벨. 월 단위 표준편차 기반."""
    if month_stdev is None:
        return None
    if month_stdev <= 0.5: return "exact"      # 거의 정확히 같은 월
    if month_stdev <= 1.5: return "stable"     # ±1~2개월 내
    if month_stdev <= 3.0: return "loose"      # 분기 단위 변동
    return "scattered"                          # 연중 분산


def _urgency(days_until: int | None) -> str | None:
    """권장 시작일까지 D-day 기반 urgency 라벨."""
    if days_until is None: return None
    if days_until < 0:     return "critical"   # 권장 시작일 이미 지남
    if days_until <= 30:   return "high"       # 30일 이내
    if days_until <= 90:   return "medium"     # 90일 이내
    return "low"


def cluster_recurring(session, label: str, min_occurrences: int = 2) -> list[dict]:
    """반복 사업 식별 + 지원 준비를 위한 종합 분석.

    Phase 15 강화 포인트:
      - 사업별 평균 마감 기간 (avg_deadline_days)
      - 권장 준비 시작일 (prep_start) = 다음 공고 예상 - 평균 마감기간 - 30일 버퍼
      - 권장 시작일까지 D-day (days_until_prep)
      - 행동 시급도 라벨 (urgency: critical/high/medium/low)
      - 공고일 일관성 (posting_consistency: exact/stable/loose/scattered)
    """
    today = today_kst()
    items = _query(session, label).all()
    groups: dict[tuple, list[HistoricalAnnouncement]] = defaultdict(list)

    for ann in items:
        norm = normalize_title(ann.title)
        if not norm or len(norm) < 4:
            continue
        cat_key = (ann.category or "")[:50]
        groups[(norm, cat_key)].append(ann)

    clusters = []
    for (norm, cat_key), group in groups.items():
        if len(group) < min_occurrences:
            continue
        sorted_grp = sorted(group, key=lambda a: a.posted_date)
        dates = [a.posted_date for a in sorted_grp]

        intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_interval = round(statistics.mean(intervals), 0) if intervals else None
        stdev_interval = round(statistics.stdev(intervals), 0) if len(intervals) >= 2 else None

        # 다음 예측 + 신뢰구간
        last_date = dates[-1]
        next_predicted = None
        next_window_low = None
        next_window_high = None
        likely_discontinued = False
        if avg_interval and avg_interval > 0:
            ai = int(avg_interval)
            anchor = last_date + timedelta(days=ai)
            # 이미 지난 시점이면 미래로 다음 사이클까지 보정
            while anchor < today - timedelta(days=max(7, ai // 4)):
                anchor = anchor + timedelta(days=ai)
            next_predicted = anchor
            # 마지막 공고가 2 사이클 이상 지났는데도 안 나왔다면 종료 사업 추정
            if (today - last_date).days > 2 * ai:
                likely_discontinued = True
            if stdev_interval:
                next_window_low  = next_predicted - timedelta(days=int(stdev_interval))
                next_window_high = next_predicted + timedelta(days=int(stdev_interval))

        # 예측 신뢰도 (CV)
        confidence = None
        if avg_interval and stdev_interval is not None and avg_interval > 0:
            cv = stdev_interval / avg_interval
            confidence = ("high" if cv < 0.15 else
                          "medium" if cv < 0.35 else
                          "low")

        # 정점 월 + 공고일 일관성 (월 단위 원형 통계)
        peak_month, month_stdev = _circular_month_stats([d.month for d in dates])
        consistency = _posting_consistency(month_stdev)

        # 사업별 평균 마감 기간 (해당 클러스터 내 공고들의 posted~deadline)
        # 180일(6개월) 초과는 "연중 안내" 공고로 보고 outlier 제외
        deadlines = [(a.posted_date, a.deadline) for a in sorted_grp
                     if a.posted_date and a.deadline]
        durations = [(dl - pd).days for pd, dl in deadlines if 0 <= (dl - pd).days <= 180]
        avg_deadline_days = round(statistics.mean(durations), 0) if durations else None

        # ── 권장 준비 시작일 ───────────────────────────────────────────
        # 신뢰도 "낮음"인 사업은 예측 자체가 부정확 → 정점월 기반 시즌 모니터링만
        prep_start = None
        days_until_prep = None
        prep_basis = None    # "predicted" | "seasonal"
        if next_predicted and confidence in ("high", "medium"):
            buffer = int(avg_deadline_days or 14) + _PREP_BUFFER_DAYS
            prep_start = next_predicted - timedelta(days=buffer)
            days_until_prep = (prep_start - today).days
            prep_basis = "predicted"
        elif peak_month:
            # 신뢰도 낮음: 정점월 기준 권장 — 정점월 2개월 전 시작
            current_year = today.year
            target_month = peak_month
            # 올해 정점월이 지났으면 내년 기준
            tentative = date(current_year, target_month, 15) - timedelta(days=60)
            if tentative < today:
                tentative = date(current_year + 1, target_month, 15) - timedelta(days=60)
            prep_start = tentative
            days_until_prep = (prep_start - today).days
            prep_basis = "seasonal"

        urgency = _urgency(days_until_prep)

        clusters.append({
            "title_sample":     sorted_grp[-1].title[:80],
            "title_normalized": norm,
            "category":         cat_key,
            "occurrences":      len(group),
            "first_posted":     dates[0].isoformat(),
            "last_posted":      dates[-1].isoformat(),
            "peak_month":       peak_month,
            "posting_consistency": consistency,
            "posting_month_stdev": month_stdev,
            "avg_interval_days":   avg_interval,
            "stdev_interval_days": stdev_interval,
            "avg_deadline_days":   avg_deadline_days,    # 사업별 평균 마감 기간
            "next_predicted":   next_predicted.isoformat() if next_predicted else None,
            "next_window_low":  next_window_low.isoformat()  if next_window_low  else None,
            "next_window_high": next_window_high.isoformat() if next_window_high else None,
            "confidence":       confidence,   # "high"/"medium"/"low"
            # Phase 15 신규 — 지원 준비 중심 필드
            "prep_start":       prep_start.isoformat() if prep_start else None,
            "days_until_prep":  days_until_prep,
            "prep_basis":       prep_basis,    # "predicted"/"seasonal"
            "urgency":          urgency,       # "critical"/"high"/"medium"/"low"
            "likely_discontinued": likely_discontinued,
        })

    clusters.sort(key=lambda c: c["occurrences"], reverse=True)
    return clusters


# ----------------------------------------------------------------------
# 2-a. "지금 액션 필요" 사업 추출 (Preparation Calendar 핵심)
# ----------------------------------------------------------------------

def action_required(clusters: list[dict], horizon_days: int = 90) -> list[dict]:
    """오늘 ±horizon_days 내 권장 시작일이 있는 반복 사업만 필터.

    정렬: critical → high → medium 순, 같은 urgency 안에서는 days_until_prep 오름차순.
    """
    URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    out = []
    for c in clusters:
        if c.get("likely_discontinued"):
            continue                       # 종료 추정 사업 제외
        d = c.get("days_until_prep")
        if d is None:
            continue
        if d > horizon_days:
            continue
        out.append(c)
    out.sort(key=lambda c: (URGENCY_ORDER.get(c.get("urgency", "low"), 3),
                             c.get("days_until_prep", 0)))
    return out


# ----------------------------------------------------------------------
# 2-b. 12개월 정점 캘린더 — 월별 정점 사업 매핑
# ----------------------------------------------------------------------

def monthly_calendar(clusters: list[dict], top_per_month: int = 8) -> list[list[dict]]:
    """각 월에 정점(peak_month)이 위치한 반복 사업 리스트 (occurrences 내림차순).

    반환: 길이 12 리스트, 각 항목은 해당 월 사업 목록.
    """
    by_month: dict[int, list[dict]] = {m: [] for m in range(1, 13)}
    for c in clusters:
        pm = c.get("peak_month")
        if pm:
            by_month[pm].append({
                "title_sample": c["title_sample"],
                "occurrences":  c["occurrences"],
                "consistency":  c.get("posting_consistency"),
                "category":     c.get("category", ""),
                "next_predicted": c.get("next_predicted"),
            })
    out = []
    for m in range(1, 13):
        items = sorted(by_month[m], key=lambda x: x["occurrences"], reverse=True)
        out.append(items[:top_per_month])
    return out


# ======================================================================
# 3. 5년 트렌드 + 단순 회귀 기울기
# ======================================================================

def _linear_slope(xs: list[int], ys: list[int]) -> float | None:
    """단순 선형 회귀 기울기 (최소제곱). 데이터 < 3이면 None."""
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    return round(num / den, 2)


def analyze_trend(session, label: str, year_min: int = 2021, year_max: int = 2026) -> dict:
    items = _query(session, label).all()
    yearly = Counter()
    for ann in items:
        if ann.posted_date:
            y = ann.posted_date.year
            if year_min <= y <= year_max:
                yearly[y] += 1

    years = list(range(year_min, year_max + 1))
    counts = [yearly[y] for y in years]

    # 완성된 연도만 회귀 (현재 진행 연도 제외)
    current_year = today_kst().year
    completed_years = [y for y in years if y < current_year]
    completed_counts = [yearly[y] for y in completed_years]

    avg_yoy = None
    slope = None
    if len(completed_counts) >= 2:
        yoy = [completed_counts[i+1] - completed_counts[i]
               for i in range(len(completed_counts) - 1)]
        avg_yoy = round(statistics.mean(yoy), 1)
    if len(completed_years) >= 3:
        slope = _linear_slope(completed_years, completed_counts)

    return {
        "years":           years,
        "yearly_counts":   counts,
        "total":           sum(counts),
        "avg_yearly_change":   avg_yoy,
        "regression_slope":    slope,    # 단순 회귀 기울기
        "current_year_incomplete": current_year in years,
    }


# ======================================================================
# 4. 마감 기간 분포 — 평균/중앙값/IQR + 7일 이내 비율
# ======================================================================

def _quartiles(sorted_data: list[int]) -> tuple[int, int, int]:
    """Q1, Q2(중앙값), Q3 반환."""
    n = len(sorted_data)
    def at(p: float) -> int:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return sorted_data[idx]
    return at(0.25), at(0.5), at(0.75)


def analyze_deadline_pattern(session, label: str) -> dict:
    items = _query(session, label).filter(
        HistoricalAnnouncement.deadline.isnot(None)
    ).all()

    durations = []
    for ann in items:
        if ann.posted_date and ann.deadline:
            d = (ann.deadline - ann.posted_date).days
            if 0 <= d <= 365:
                durations.append(d)

    if not durations:
        return {"total": 0}

    sd = sorted(durations)
    q1, q2, q3 = _quartiles(sd)

    bin_size = 7
    max_d = max(durations)
    bin_count = (max_d // bin_size) + 1
    bins = [0] * bin_count
    for d in durations:
        bins[d // bin_size] += 1

    under_7  = sum(1 for d in durations if d <= 7)
    under_14 = sum(1 for d in durations if d <= 14)

    return {
        "total":  len(durations),
        "mean":   round(statistics.mean(durations), 1),
        "median": q2,
        "q1":     q1,
        "q3":     q3,
        "iqr":    q3 - q1,
        "stdev":  round(statistics.stdev(durations), 1) if len(durations) >= 2 else None,
        "min":    min(durations),
        "max":    max(durations),
        "bin_size_days":   bin_size,
        "bins":   bins,
        "under_7_pct":  round(100 * under_7  / len(durations), 1),
        "under_14_pct": round(100 * under_14 / len(durations), 1),
    }


# ======================================================================
# 5. Top-K 사업 (신규) — 빈도 상위 사업
# ======================================================================

def top_business_types(session, label: str, k: int = 10) -> list[dict]:
    """카테고리 기반 빈도 상위 사업."""
    items = _query(session, label).all()
    counter = Counter()
    for ann in items:
        if ann.category:
            # 가장 구체적인 사업명 (마지막 ">" 이후)
            parts = [p.strip() for p in ann.category.split(">")]
            last = parts[-1] if parts else ann.category
            counter[last[:60]] += 1
    return [{"name": name, "count": cnt} for name, cnt in counter.most_common(k)]


# ======================================================================
# 종합 — 5개 분석 × 5개 라벨 (interest + 4 라벨)
# ======================================================================

def run_full_analysis(session) -> dict:
    out = {}
    for label in LABEL_ORDER:
        clusters = cluster_recurring(session, label, min_occurrences=2)
        out[label] = {
            "label_kr":    LABEL_NAMES_KR[label],
            "seasonality": analyze_seasonality(session, label),
            "recurring":   clusters,
            "action_required": action_required(clusters, horizon_days=90),
            "calendar":    monthly_calendar(clusters, top_per_month=8),
            "trend":       analyze_trend(session, label),
            "deadline":    analyze_deadline_pattern(session, label),
            "top_types":   top_business_types(session, label, k=10),
        }
    return out
