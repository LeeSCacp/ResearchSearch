"""Google Calendar 알림 모듈.

서비스 계정(Service Account) 방식으로 인증하며,
신규 공고의 마감일을 캘린더 종일 이벤트로 등록한다.

인증 우선순위:
  1. 환경변수 GOOGLE_CALENDAR_CREDENTIALS (JSON 문자열) — GitHub Actions
  2. 프로젝트 루트의 researchsearch-*.json 파일 — 로컬 개발
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from src.models.announcement import Announcement

logger = logging.getLogger(__name__)

# Google Calendar 이벤트 색상 ID (토마토=빨강, 마감 긴박감 표현)
_COLOR_TOMATO = "11"


class CalendarNotifier:
    def __init__(self, config: dict):
        self.enabled     = config.get("enabled", False)
        self.calendar_id = config.get("calendar_id", "") or os.getenv("GOOGLE_CALENDAR_ID", "")
        self._service    = None  # 지연 초기화

    # ------------------------------------------------------------------
    # 신규 공고 → 캘린더 이벤트 등록
    # ------------------------------------------------------------------

    def add_deadline_events(
        self,
        announcements: list[Announcement],
    ) -> dict[int, tuple[bool, str]]:
        """마감일이 있는 신규 공고를 Google Calendar에 종일 이벤트로 등록.

        Returns:
            {announcement.id: (success, error_message)}
        """
        if not self.enabled:
            return {}
        if not self.calendar_id:
            logger.warning("[Calendar] calendar_id 미설정 — 환경변수 GOOGLE_CALENDAR_ID를 확인하세요.")
            return {ann.id: (False, "calendar_id 미설정") for ann in announcements}

        results: dict[int, tuple[bool, str]] = {}

        try:
            service = self._get_service()
        except Exception as e:
            logger.error(f"[Calendar] 서비스 초기화 실패: {e}")
            return {ann.id: (False, str(e)) for ann in announcements}

        for ann in announcements:
            if not ann.deadline:
                logger.debug(f"[Calendar] 마감일 없음, 건너뜀: {ann.title[:40]}")
                results[ann.id] = (False, "마감일 없음")
                continue

            try:
                deadline_str = ann.deadline.isoformat()          # YYYY-MM-DD
                end_str      = (ann.deadline + timedelta(days=1)).isoformat()  # 종일 이벤트 종료일

                source_label = ann.source.upper()
                event = {
                    "summary": f"[{source_label}] {ann.title}",
                    "description": (
                        f"출처: {source_label}\n"
                        f"공고 링크: {ann.url}\n\n"
                        f"{(ann.description or '').strip()}"
                    ),
                    "start": {"date": deadline_str},
                    "end":   {"date": end_str},
                    "colorId": _COLOR_TOMATO,
                    "reminders": {
                        "useDefault": False,
                        "overrides": [
                            {"method": "email",  "minutes": 60 * 24 * 7},  # D-7
                            {"method": "email",  "minutes": 60 * 24 * 3},  # D-3
                            {"method": "popup",  "minutes": 60 * 24 * 1},  # D-1
                        ],
                    },
                }

                service.events().insert(
                    calendarId=self.calendar_id, body=event
                ).execute()

                results[ann.id] = (True, "")
                logger.info(f"[Calendar] 등록 완료: [{source_label}] D-day={deadline_str} | {ann.title[:40]}")

            except Exception as e:
                results[ann.id] = (False, str(e))
                logger.error(f"[Calendar] 등록 실패 [{ann.title[:30]}]: {e}")

        return results

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _get_service(self):
        """Google Calendar API 서비스 객체 반환 (지연 초기화)."""
        if self._service:
            return self._service

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as e:
            raise ImportError(
                "google-api-python-client, google-auth 패키지가 필요합니다: "
                "pip install google-api-python-client google-auth"
            ) from e

        SCOPES = ["https://www.googleapis.com/auth/calendar"]

        # 1순위: 환경변수 (GitHub Actions)
        creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS")
        if creds_json:
            try:
                creds_info  = json.loads(creds_json)
                credentials = service_account.Credentials.from_service_account_info(
                    creds_info, scopes=SCOPES
                )
                logger.debug("[Calendar] 환경변수에서 서비스 계정 인증 완료")
            except json.JSONDecodeError as e:
                raise ValueError(f"GOOGLE_CALENDAR_CREDENTIALS JSON 파싱 실패: {e}") from e
        else:
            # 2순위: 프로젝트 루트 JSON 파일 (로컬 개발)
            root      = Path(__file__).resolve().parent.parent.parent
            key_files = (
                list(root.glob("researchsearch-*.json"))
                + list(root.glob("*-service-account*.json"))
                + list(root.glob("service_account*.json"))
            )
            if not key_files:
                raise FileNotFoundError(
                    "서비스 계정 키 파일을 찾을 수 없습니다. "
                    "프로젝트 루트에 researchsearch-*.json 파일이 있는지 확인하세요."
                )
            key_path    = str(key_files[0])
            credentials = service_account.Credentials.from_service_account_file(
                key_path, scopes=SCOPES
            )
            logger.debug(f"[Calendar] 파일에서 서비스 계정 인증: {key_path}")

        self._service = build("calendar", "v3", credentials=credentials)
        return self._service
