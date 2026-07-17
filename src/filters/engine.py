"""키워드 동의어 확장 + 관련도 스코어링 필터 엔진.

동작 방식:
  1. 입력 키워드를 동의어 사전(synonyms.yaml)으로 확장
  2. 공고별 매칭 점수 계산 (몇 개의 키워드가 매칭됐는지)
  3. 점수 높은 순 → 직접 매칭 우선 → 마감일 순으로 정렬
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from src.scrapers.base import AnnouncementData

logger = logging.getLogger(__name__)

SYNONYMS_PATH = Path(__file__).resolve().parent / "synonyms.yaml"


# ------------------------------------------------------------------
# 동의어 사전 로드
# ------------------------------------------------------------------

def _load_synonym_map(path: Path = SYNONYMS_PATH) -> dict[str, list[str]]:
    """동의어 YAML 로드 → {term_lower: [group_terms_lower]} 매핑 반환.

    같은 그룹에 속한 term끼리 서로를 확장 동의어로 갖는다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning(f"동의어 사전 파일 없음: {path}")
        return {}

    term_map: dict[str, list[str]] = {}
    for group in data.get("groups", []):
        terms = [str(t).lower() for t in group.get("terms", [])]
        for term in terms:
            term_map[term] = terms  # 자신 포함 전체 그룹
    return term_map


# ------------------------------------------------------------------
# 검색 결과 DTO
# ------------------------------------------------------------------

@dataclass
class SearchResult:
    """공고 검색 결과 + 관련도 메타 정보."""

    announcement: object                          # Announcement ORM 또는 AnnouncementData
    score: int = 0                                # 매칭된 원본 키워드 수
    direct_score: int = 0                         # 동의어 없이 직접 매칭된 키워드 수
    matched_keywords: list[str] = field(default_factory=list)   # 매칭된 원본 키워드 목록
    matched_terms: list[str] = field(default_factory=list)      # 실제로 텍스트에서 발견된 단어

    @property
    def relevance_label(self) -> str:
        """UI 표시용 관련도 레이블."""
        if self.score == 0:
            return ""
        parts = []
        for kw in self.matched_keywords:
            kw_lower = kw.lower()
            if kw_lower in [t.lower() for t in self.matched_terms]:
                parts.append(kw)           # 직접 매칭
            else:
                parts.append(f"~{kw}")     # 동의어 매칭
        return ", ".join(parts)


# ------------------------------------------------------------------
# 필터 엔진
# ------------------------------------------------------------------

class FilterEngine:
    """동의어 확장 기반 검색 + 관련도 스코어링 엔진."""

    def __init__(self, synonyms_path: Path = SYNONYMS_PATH,
                 keywords: list[str] = None, categories: list[str] = None,
                 exclude_keywords: list[str] = None,
                 conditional_keywords: list[str] = None):
        """
        Args:
            synonyms_path: 동의어 사전 YAML 경로
            keywords: 스케줄러용 기본 키워드 (config에서 주입)
            categories: 스케줄러용 기본 카테고리 (config에서 주입)
            exclude_keywords: 제외 키워드 — 매칭 시 포함 조건 무관하게 차단
            conditional_keywords: 조건부 키워드 — 단독 통과 불가,
                핵심 키워드와 동시 등장 시에만 통과 (예: 인공지능)
        """
        self._synonym_map = _load_synonym_map(synonyms_path)
        self._default_keywords = [kw.strip() for kw in (keywords or []) if kw.strip()]
        self._default_categories = [c.strip() for c in (categories or []) if c.strip()]
        self._default_exclude_keywords = [kw.strip() for kw in (exclude_keywords or []) if kw.strip()]
        self._default_conditional_keywords = [kw.strip() for kw in (conditional_keywords or []) if kw.strip()]

    def expand(self, keyword: str) -> list[str]:
        """키워드를 동의어 포함 전체 목록으로 확장.

        사전에 없으면 원본 키워드만 반환.
        """
        kw = keyword.strip().lower()
        return self._synonym_map.get(kw, [kw])

    def _searchable_text(self, item) -> str:
        """공고 객체(ORM 또는 DTO)에서 검색 대상 텍스트 추출 (제목+본문+분야)."""
        title = getattr(item, "title", "") or ""
        desc = getattr(item, "description", "") or ""
        cat = getattr(item, "category", "") or ""
        return f"{title} {desc} {cat}".lower()

    def _primary_text(self, item) -> str:
        """알림 통과 판정용 텍스트 (제목+분야만 — 본문 제외).

        본문은 세부 과제 목록 등에서 무관 키워드가 스치듯 등장해
        오탐의 주 원인이었으므로 통과 판정에서 제외한다.
        """
        title = getattr(item, "title", "") or ""
        cat = getattr(item, "category", "") or ""
        return f"{title} {cat}".lower()

    # ------------------------------------------------------------------
    # 메인 검색 메서드 (웹 대시보드용)
    # ------------------------------------------------------------------

    def search(
        self,
        announcements: list,
        keywords: list[str],
    ) -> list[SearchResult]:
        """키워드로 공고를 검색하고 관련도 순으로 정렬된 결과 반환.

        - 키워드 없음: 전체 반환 (score=0, 원래 순서 유지)
        - 키워드 있음: 1개 이상 매칭된 것만 포함, 관련도 순 정렬
        - 정렬 기준: score DESC → direct_score DESC → 마감일 ASC
        """
        keywords = [kw.strip() for kw in keywords if kw.strip()]

        if not keywords:
            return [SearchResult(announcement=a) for a in announcements]

        results: list[SearchResult] = []

        for ann in announcements:
            text = self._searchable_text(ann)
            score = 0
            direct_score = 0
            matched_keywords: list[str] = []
            matched_terms: list[str] = []

            for kw in keywords:
                kw_lower = kw.lower()
                expanded = self.expand(kw_lower)
                kw_matched = False

                for term in expanded:
                    if term in text:
                        if not kw_matched:
                            score += 1
                            matched_keywords.append(kw)
                            kw_matched = True
                        if term not in matched_terms:
                            matched_terms.append(term)
                        # 직접 매칭 카운트 (동의어 아닌 원본 키워드)
                        if term == kw_lower:
                            direct_score += 1

            if score > 0:
                results.append(SearchResult(
                    announcement=ann,
                    score=score,
                    direct_score=direct_score,
                    matched_keywords=matched_keywords,
                    matched_terms=matched_terms,
                ))

        # 정렬: score DESC → direct_score DESC → 마감일 ASC (None은 마지막)
        def sort_key(r: SearchResult) -> tuple:
            dl = getattr(r.announcement, "deadline", None)
            if dl is None:
                dl_val = date(9999, 12, 31)
            elif hasattr(dl, "date"):
                dl_val = dl.date()
            else:
                dl_val = dl
            return (-r.score, -r.direct_score, dl_val)

        results.sort(key=sort_key)
        return results

    # ------------------------------------------------------------------
    # 스케줄러용 메서드 (AnnouncementData 기반)
    # ------------------------------------------------------------------

    def matches(self, item: AnnouncementData,
                keywords: list[str] = None,
                categories: list[str] = None,
                exclude_keywords: list[str] = None,
                conditional_keywords: list[str] = None) -> bool:
        """공고가 알림 필터 조건에 부합하는지 확인 (정밀 매칭).

        평가 순서 — 통과 판정은 제목+분야(primary)만 사용, 본문은 보조:
          1. 제외 키워드: 제목·분야 매칭 시 즉시 False (차단)
          2. 핵심 키워드: 제목·분야 매칭 시 True
          3. 카테고리: category 필드 매칭 시 True
          4. 조건부 키워드(인공지능 등): 제목·분야에 있고,
             핵심 키워드가 본문 포함 어디든 함께 있으면 True
             (심리학×AI 융합만 통과 — 순수 ICT 과제 차단)

        본문(description) 단독 매칭은 통과 근거가 되지 않는다.
        포함 조건이 모두 비어있으면 True (전체 통과).
        """
        kws    = keywords             if keywords             is not None else self._default_keywords
        cats   = categories           if categories           is not None else self._default_categories
        excls  = exclude_keywords     if exclude_keywords     is not None else self._default_exclude_keywords
        conds  = conditional_keywords if conditional_keywords is not None else self._default_conditional_keywords

        primary = self._primary_text(item)      # 제목 + 분야
        full    = self._searchable_text(item)   # 제목 + 본문 + 분야 (조건부 판정 보조)

        # ── 1. 제외 키워드 (포함 조건보다 먼저 평가) ──────────────────────
        # 동의어 확장 없이 정확한 부분 문자열 매칭 (확장 시 일반 단어가 과잉 차단됨)
        for excl in excls:
            if excl.lower() in primary:
                logger.debug(
                    f"제외 키워드 매칭 [{excl}]: {getattr(item, 'title', '')[:40]}"
                )
                return False

        # ── 2. 포함 조건이 모두 비어있으면 전체 통과 ─────────────────────
        if not kws and not cats and not conds:
            return True

        # ── 3. 핵심 키워드 — 제목·분야 매칭 (동의어 확장 없음) ───────────
        for kw in kws:
            if kw.lower() in primary:
                return True

        # ── 4. 카테고리 매칭 ─────────────────────────────────────────────
        item_cat = (getattr(item, "category", "") or "").lower()
        for cat in cats:
            if cat.lower() in item_cat:
                return True

        # ── 5. 조건부 키워드 — 제목·분야에 있고 + 핵심 키워드 동반 시 ────
        for ck in conds:
            if ck.lower() in primary:
                if any(kw.lower() in full for kw in kws):
                    return True
                break   # 조건부 키워드는 있으나 핵심 키워드 없음 → 통과 불가

        return False

    def match_reasons(self, item: AnnouncementData) -> list[str]:
        """이 공고가 필터를 통과한 근거 키워드 목록 (다이제스트 메일 배지용).

        matches()와 동일한 규칙으로 평가하되, 매칭된 근거를 문자열로 수집한다.
        통과하지 못하는 공고면 빈 리스트.
        """
        primary = self._primary_text(item)
        full    = self._searchable_text(item)

        for excl in self._default_exclude_keywords:
            if excl.lower() in primary:
                return []

        reasons: list[str] = []

        for kw in self._default_keywords:
            if kw.lower() in primary:
                reasons.append(kw)

        item_cat = (getattr(item, "category", "") or "").lower()
        for cat in self._default_categories:
            if cat.lower() in item_cat and cat not in reasons:
                reasons.append(cat)

        if not reasons:
            # 조건부 키워드 경로: "인공지능×심리" 형태로 표기
            for ck in self._default_conditional_keywords:
                if ck.lower() in primary:
                    partners = [kw for kw in self._default_keywords if kw.lower() in full]
                    if partners:
                        reasons.append(f"{ck}×{partners[0]}")
                    break

        return reasons

    def filter_items(self, items: list[AnnouncementData],
                     keywords: list[str] = None,
                     categories: list[str] = None,
                     exclude_keywords: list[str] = None,
                     conditional_keywords: list[str] = None) -> list[AnnouncementData]:
        """AnnouncementData 리스트를 필터링하여 반환 (스케줄러용)."""
        return [item for item in items
                if self.matches(item, keywords, categories,
                                exclude_keywords, conditional_keywords)]
