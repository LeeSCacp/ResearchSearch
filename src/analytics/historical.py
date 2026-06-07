"""NRF 과거 공고 4가지 패턴 분석.

분석 단위:
  - "전체" : 모든 1700+건 (마감일 패턴, 광범위 시즌성)
  - "인문사회" : label_humanities = True (심리학자가 신청 가능한 주요 모집단)
  - "심리학" : label_psychology = True
  - "노화" : label_aging = True
  - "심리AI" : label_psy_ai = True

함수 호출 시 라벨 필터를 지정하여 4가지 분석을 라벨별로 수행한다.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Iterable

from src.models.announcement import HistoricalAnnouncement


# ======================================================================
# 공통 유틸
# ======================================================================

# 라벨 필터 정의
LABEL_FILTERS = {
    "all":        lambda q: q,
    "humanities": lambda q: q.filter(HistoricalAnnouncement.label_humanities == True),
    "psychology": lambda q: q.filter(HistoricalAnnouncement.label_psychology == True),
    "aging":      lambda q: q.filter(HistoricalAnnouncement.label_aging == True),
    "psy_ai":     lambda q: q.filter(HistoricalAnnouncement.label_psy_ai == True),
}

LABEL_NAMES_KR = {
    "all":        "전체",
    "humanities": "인문사회 전반",
    "psychology": "심리학 전반",
    "aging":      "노화/치매/고령",
    "psy_ai":     "심리+AI 융합",
}


def _query(session, label: str):
    """라벨별 기본 쿼리. posted_date NOT NULL인 공고만 분석 대상."""
    base = session.query(HistoricalAnnouncement).filter(
        HistoricalAnnouncement.posted_date.isnot(None)
    )
    fn = LABEL_FILTERS.get(label, LABEL_FILTERS["all"])
    return fn(base)


# ======================================================================
# 1. 시즌성 — 월별 공고 분포
# ======================================================================

def analyze_seasonality(session, label: str) -> dict:
    """월별(1~12) 공고 수 집계. 평균·정점·저점 식별."""
    items = _query(session, label).all()
    if not items:
        return {"total": 0, "monthly": [0]*12, "peak_month": None,
                "low_month": None, "avg_per_month": 0}

    monthly = [0] * 12
    for ann in items:
        if ann.posted_date:
            monthly[ann.posted_date.month - 1] += 1

    avg = sum(monthly) / 12
    peak_idx = monthly.index(max(monthly))
    low_idx  = monthly.index(min(monthly))

    return {
        "total":         len(items),
        "monthly":       monthly,
        "peak_month":    peak_idx + 1,
        "peak_count":    monthly[peak_idx],
        "low_month":     low_idx + 1,
        "low_count":     monthly[low_idx],
        "avg_per_month": round(avg, 1),
    }


# ======================================================================
# 2. 반복 사업 식별 + 다음 공고 예측
# ======================================================================

# 제목 정규화 정규식
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
    """제목 정규화 — 연도·차수·접미사 제거하여 동일 사업 묶기."""
    t = title or ""
    t = _YEAR_PATTERN.sub('', t)                 # "2023년도" 제거
    t = _NUM_BRACKETS.sub('', t)                 # 괄호 안 메모 제거
    t = _ROUND_PATTERN.sub('', t)                # "1차", "2호" 제거
    for s in _SUFFIX_NOISE:
        t = t.replace(s, '')
    t = re.sub(r'[·.,;:!?_\-]+', ' ', t)         # 구두점 제거
    t = _WS_RE.sub(' ', t).strip()
    return t.lower()[:80]                         # 길이 제한


def cluster_recurring(session, label: str, min_occurrences: int = 2) -> list[dict]:
    """반복 사업(클러스터) 식별 — 정규화된 제목 + 카테고리로 그룹화."""
    items = _query(session, label).all()
    groups: dict[tuple, list[HistoricalAnnouncement]] = defaultdict(list)

    for ann in items:
        norm = normalize_title(ann.title)
        if not norm or len(norm) < 4:
            continue
        # 카테고리 일부도 키에 포함 (사업명이 비슷해도 분야 다르면 분리)
        cat_key = (ann.category or "")[:50]
        groups[(norm, cat_key)].append(ann)

    clusters = []
    for (norm, cat_key), group in groups.items():
        if len(group) < min_occurrences:
            continue
        sorted_grp = sorted(group, key=lambda a: a.posted_date)
        dates = [a.posted_date for a in sorted_grp]

        # 평균 주기 (일 단위)
        intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_interval = round(statistics.mean(intervals), 0) if intervals else None
        stdev_interval = round(statistics.stdev(intervals), 0) if len(intervals) >= 2 else None

        # 다음 예측 (마지막 공고일 + 평균 주기)
        last_date = dates[-1]
        next_predicted = (last_date + timedelta(days=int(avg_interval))) if avg_interval else None

        # 정점 월 (가장 자주 공고된 월)
        month_counter = Counter(d.month for d in dates)
        peak_month = month_counter.most_common(1)[0][0] if month_counter else None

        clusters.append({
            "title_sample":    sorted_grp[-1].title[:80],
            "title_normalized": norm,
            "category":        cat_key,
            "occurrences":     len(group),
            "first_posted":    dates[0].isoformat(),
            "last_posted":     dates[-1].isoformat(),
            "peak_month":      peak_month,
            "avg_interval_days":   avg_interval,
            "stdev_interval_days": stdev_interval,
            "next_predicted":  next_predicted.isoformat() if next_predicted else None,
        })

    clusters.sort(key=lambda c: c["occurrences"], reverse=True)
    return clusters


# ======================================================================
# 3. 5년 트렌드 — 연도별 공고 수
# ======================================================================

def analyze_trend(session, label: str, year_min: int = 2021, year_max: int = 2026) -> dict:
    """연도별 공고 수 + 증감 추세."""
    items = _query(session, label).all()
    yearly = Counter()
    for ann in items:
        if ann.posted_date:
            y = ann.posted_date.year
            if year_min <= y <= year_max:
                yearly[y] += 1

    years = list(range(year_min, year_max + 1))
    counts = [yearly[y] for y in years]

    # 단순 선형 추세 (연간 평균 변화량)
    if len(years) >= 2 and any(counts):
        # 2026년이 진행 중이라면 제외 (불완전)
        completed_years = [y for y in years if y < date.today().year]
        completed_counts = [yearly[y] for y in completed_years]
        if len(completed_counts) >= 2:
            yoy_changes = [completed_counts[i+1] - completed_counts[i]
                           for i in range(len(completed_counts) - 1)]
            avg_yoy = round(statistics.mean(yoy_changes), 1)
        else:
            avg_yoy = None
    else:
        avg_yoy = None

    return {
        "years":           years,
        "yearly_counts":   counts,
        "total":           sum(counts),
        "avg_yearly_change": avg_yoy,   # 양수 = 증가 추세
        "current_year_incomplete": date.today().year in years,
    }


# ======================================================================
# 4. 마감일 패턴 — 공고~마감 기간
# ======================================================================

def analyze_deadline_pattern(session, label: str) -> dict:
    """공고일~마감일 기간 분포. 평균·중앙값·표준편차."""
    items = _query(session, label).filter(
        HistoricalAnnouncement.deadline.isnot(None)
    ).all()

    durations = []
    for ann in items:
        if ann.posted_date and ann.deadline:
            d = (ann.deadline - ann.posted_date).days
            if 0 <= d <= 365:   # 1년 이상은 outlier로 간주
                durations.append(d)

    if not durations:
        return {"total": 0, "mean": None, "median": None, "stdev": None,
                "min": None, "max": None, "bins": []}

    # 히스토그램 bin (7일 단위)
    bin_size = 7
    max_d = max(durations)
    bin_count = (max_d // bin_size) + 1
    bins = [0] * bin_count
    for d in durations:
        bins[d // bin_size] += 1

    return {
        "total":  len(durations),
        "mean":   round(statistics.mean(durations), 1),
        "median": int(statistics.median(durations)),
        "stdev":  round(statistics.stdev(durations), 1) if len(durations) >= 2 else None,
        "min":    min(durations),
        "max":    max(durations),
        "bin_size_days": bin_size,
        "bins":   bins,    # [0~6일, 7~13일, 14~20일, ...]
    }


# ======================================================================
# 종합 분석 — 4개 라벨 × 4개 분석
# ======================================================================

def run_full_analysis(session) -> dict:
    """5개 모집단(all + 4 라벨) 각각에 4가지 분석을 수행."""
    out = {}
    for label in ["all", "humanities", "psychology", "aging", "psy_ai"]:
        out[label] = {
            "label_kr":      LABEL_NAMES_KR[label],
            "seasonality":   analyze_seasonality(session, label),
            "recurring":     cluster_recurring(session, label, min_occurrences=2),
            "trend":         analyze_trend(session, label),
            "deadline":      analyze_deadline_pattern(session, label),
        }
    return out
