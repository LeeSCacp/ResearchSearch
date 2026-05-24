"""텔레그램 봇 알림 모듈."""

import logging

import httpx

from src.models.announcement import Announcement

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, config: dict):
        self.enabled = config.get("enabled", False)
        self.bot_token = config.get("bot_token", "")
        self.chat_id = config.get("chat_id", "")

    async def send(self, announcements: list[Announcement]) -> bool:
        if not self.enabled:
            logger.info("텔레그램 알림이 비활성화 상태입니다.")
            return False

        if not announcements:
            return True

        if not self.bot_token or not self.chat_id:
            logger.warning("텔레그램 설정이 불완전합니다 (bot_token/chat_id 확인).")
            return False

        message = self._build_message(announcements)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    TELEGRAM_API_URL.format(token=self.bot_token),
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
            logger.info(f"텔레그램 발송 완료: {len(announcements)}건")
            return True
        except Exception as e:
            logger.error(f"텔레그램 발송 실패: {e}")
            return False

    @staticmethod
    def _build_message(announcements: list[Announcement]) -> str:
        source_names = {"nrf": "한국연구재단", "ntis": "NTIS", "iris": "IRIS"}

        lines = [f"📢 <b>새 연구과제 공고 {len(announcements)}건</b>\n"]

        for ann in announcements:
            source_label = source_names.get(ann.source, ann.source)
            deadline_str = ann.deadline.strftime("%Y-%m-%d") if ann.deadline else "미정"
            lines.append(
                f"• [{source_label}] <a href=\"{ann.url}\">{ann.title}</a>\n"
                f"  분야: {ann.category or '-'} | 마감: {deadline_str}"
            )

        return "\n\n".join(lines)
