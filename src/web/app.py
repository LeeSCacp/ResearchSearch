"""FastAPI 웹 대시보드 라우터."""

from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config import load_config, today_kst
from src.models.announcement import Announcement, NotificationLog, init_db, get_session
from src.filters.engine import FilterEngine

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    app = FastAPI(title="연구과제 알림 시스템", version="1.0.0")

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        source: str = Query(default="", description="출처 필터"),
        keyword: str = Query(default="", description="키워드 검색"),
        page: int = Query(default=1, ge=1),
    ):
        config = load_config()
        db_engine = init_db(config["database"]["path"])
        session = get_session(db_engine)

        try:
            # 1. DB에서 출처 필터만 적용해 전체 조회 (키워드는 Python에서 처리)
            query = session.query(Announcement).order_by(Announcement.created_at.desc())
            if source:
                query = query.filter(Announcement.source == source)
            all_announcements = query.all()

            # 2. 키워드가 있으면 FilterEngine으로 동의어 확장 + 스코어링
            filter_engine = FilterEngine()
            keywords = [kw.strip() for kw in keyword.split() if kw.strip()]
            search_results = filter_engine.search(all_announcements, keywords)

            # 3. 확장된 동의어 목록 (검색창 힌트용)
            expanded_hints: list[str] = []
            if keywords:
                for kw in keywords:
                    expanded = filter_engine.expand(kw)
                    # 원본 키워드 제외한 동의어만 힌트로 표시
                    synonyms = [t for t in expanded if t.lower() != kw.lower()]
                    expanded_hints.extend(synonyms[:4])  # 너무 많으면 잘라냄

            # 4. 리마인더 발송 현황 (announcement_id → 발송된 event_type 목록)
            all_ann_ids = [r.announcement.id for r in search_results]
            reminder_logs = {}
            if all_ann_ids:
                logs = (
                    session.query(NotificationLog)
                    .filter(
                        NotificationLog.announcement_id.in_(all_ann_ids),
                        NotificationLog.success == True,
                    )
                    .all()
                )
                for log in logs:
                    reminder_logs.setdefault(log.announcement_id, []).append(log.event_type)

            # 5. 페이지네이션 (Python 레벨)
            total = len(search_results)
            per_page = 20
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            paged_results = search_results[(page - 1) * per_page : page * per_page]

            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={
                    "results": paged_results,
                    "reminder_logs": reminder_logs,            # {ann_id: [event_types]}
                    "source": source,
                    "keyword": keyword,
                    "keywords": keywords,
                    "expanded_hints": list(dict.fromkeys(expanded_hints)),
                    "page": page,
                    "total_pages": total_pages,
                    "total": total,
                    "today": today_kst(),
                    "has_keyword": bool(keywords),
                },
            )
        finally:
            session.close()

    @app.get("/api/announcements")
    async def api_announcements(
        source: str = Query(default=""),
        keyword: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=20, ge=1, le=100),
    ):
        config = load_config()
        db_engine = init_db(config["database"]["path"])
        session = get_session(db_engine)

        try:
            query = session.query(Announcement).order_by(Announcement.created_at.desc())
            if source:
                query = query.filter(Announcement.source == source)
            all_announcements = query.all()

            filter_engine = FilterEngine()
            keywords = [kw.strip() for kw in keyword.split() if kw.strip()]
            search_results = filter_engine.search(all_announcements, keywords)

            total = len(search_results)
            paged = search_results[(page - 1) * per_page : page * per_page]

            return {
                "total": total,
                "page": page,
                "per_page": per_page,
                "keywords": keywords,
                "items": [
                    {
                        **r.announcement.to_dict(),
                        "score": r.score,
                        "matched_keywords": r.matched_keywords,
                        "matched_terms": r.matched_terms,
                    }
                    for r in paged
                ],
            }
        finally:
            session.close()

    @app.get("/api/synonyms")
    async def api_synonyms(keyword: str = Query(default="")):
        """키워드의 동의어 목록 반환 (자동완성 힌트용)."""
        if not keyword:
            return {"keyword": "", "synonyms": []}
        engine = FilterEngine()
        expanded = engine.expand(keyword.strip())
        synonyms = [t for t in expanded if t.lower() != keyword.strip().lower()]
        return {"keyword": keyword, "synonyms": synonyms}

    @app.post("/api/scrape")
    async def trigger_scrape():
        """수동으로 스크래핑 사이클을 실행한다."""
        from src.scheduler import run_scraping_cycle
        await run_scraping_cycle()
        return {"status": "ok", "message": "스크래핑 사이클 완료"}

    return app
