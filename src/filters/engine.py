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
                 keywords: list[str] = None, categories: list[str] = None):
        """
        Args:
            synonyms_path: 동의어 사전 YAML 경로
            keywords: 스케줄러용 기본 키워드 (config에서 주입)
            categories: 스케줄러용 기본 카테고리 (config에서 주입)
        """
        self._synonym_map = _load_synonym_map(synonyms_path)
        self._default_keywords = [kw.strip() for kw in (keywords or []) if kw.strip()]
        self._default_categories = [c.strip() for c in (categories or []) if c.strip()]

    def expand(self, keyword: str) -> list[str]:
        """키워드를 동의어 포함 전체 목록으로 확장.

        사전에 없으면 원본 키워드만 반환.
        """
        kw = keyword.strip().lower()
        return self._synonym_map.get(kw, [kw])

    def _searchable_text(self, item) -> str:
        """공고 객체(ORM 또는 DTO)에서 검색 대상 텍스트 추출."""
        title = getattr(item, "title", "") or ""
        desc = getattr(item, "description", "") or ""
        cat = getattr(item, "category", "") or ""
        return f"{title} {desc} {cat}".lower()

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
                categories: list[str] = None) -> bool:
        """공고가 필터 조건에 부합하는지 확인.

        keywords/categories 미전달 시 생성자에서 받은 기본값 사용.
        둘 다 비어있으면 True (전체 통과).
        """
        kws = keywords if keywords is not None else self._default_keywords
        cats = categories if categories is not None else self._default_categories

        if not kws and not cats:
            return True

        text = self._searchable_text(item)

        # 키워드 OR 매칭 (동의어 포함)
        for kw in kws:
            expanded = self.expand(kw.lower())
            if any(t in text for t in expanded):
                return True

        # 카테고리 OR 매칭
        item_cat = (getattr(item, "category", "") or "").lower()
        for cat in cats:
            if cat.lower() in item_cat:
                return True

        return False

    def filter_items(self, items: list[AnnouncementData],
                     keywords: list[str] = None,
                     categories: list[str] = None) -> list[AnnouncementData]:
        """AnnouncementData 리스트를 필터링하여 반환 (스케줄러용)."""
        return [item for item in items if self.matches(item, keywords, categories)]
