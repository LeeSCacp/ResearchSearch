"""이메일 알림 모듈 (SMTP 기반)."""

import smtplib
import logging
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from src.models.announcement import Announcement

logger = logging.getLogger(__name__)

# D-day 리마인더 임계값 정의
REMINDER_THRESHOLDS = [
    ("d7", 7,  "D-7 마감 임박"),
    ("d3", 3,  "D-3 마감 임박"),
    ("d1", 1,  "D-1 내일 마감"),
]


class EmailNotifier:
    def __init__(self, config: dict):
        self.enabled    = config.get("enabled", False)
        self.smtp_host  = config.get("smtp_host", "smtp.gmail.com")
        self.smtp_port  = config.get("smtp_port", 587)
        self.sender     = config.get("sender", "")
        self.password   = config.get("password", "")
        self.recipients = config.get("recipients", [])

    # ------------------------------------------------------------------
    # 연결 테스트
    # ------------------------------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        """SMTP 연결 및 로그인 테스트 (실제 메일 발송 없음)."""
        if not self.sender or not self.password:
            return False, "EMAIL_SENDER 또는 EMAIL_PASSWORD가 설정되지 않았습니다."
        if not self.recipients:
            return False, "수신자(recipients)가 설정되지 않았습니다."
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(self.sender, self.password)
            return True, f"[OK] 연결 성공! ({self.sender} -> {self.smtp_host}:{self.smtp_port})"
        except smtplib.SMTPAuthenticationError:
            return False, "[FAIL] 인증 실패: Gmail 앱 비밀번호를 확인해 주세요."
        except smtplib.SMTPConnectError as e:
            return False, f"[FAIL] 서버 연결 실패: {e}"
        except TimeoutError:
            return False, "[FAIL] 연결 시간 초과."
        except Exception as e:
            return False, f"[FAIL] 오류: {e}"

    def send_test(self) -> tuple[bool, str]:
        """테스트 이메일 발송."""
        msg = self._make_message("[ResearchProjAssist] 이메일 연결 테스트",
                                 self._build_test_html())
        return self._send(msg)

    # ------------------------------------------------------------------
    # 신규 공고 알림
    # ------------------------------------------------------------------

    def send(self, announcements: list[Announcement]) -> bool:
        """신규 공고 알림 이메일 발송."""
        if not self.enabled:
            logger.info("이메일 알림 비활성화 상태.")
            return False
        if not announcements:
            return True
        if not self._is_configured():
            logger.warning("이메일 설정 불완전.")
            return False

        subject = f"[연구과제 알림] 새 공고 {len(announcements)}건"
        msg = self._make_message(subject, self._build_new_html(announcements))
        ok, err = self._send(msg)
        if ok:
            logger.info(f"신규 공고 이메일 발송 완료: {len(announcements)}건")
        else:
            logger.error(f"신규 공고 이메일 발송 실패: {err}")
        return ok

    # ------------------------------------------------------------------
    # D-day 리마인더 알림
    # ------------------------------------------------------------------

    def send_reminder(
        self,
        reminders: list[tuple[Announcement, str, int]],
    ) -> dict[int, tuple[bool, str]]:
        """D-day 리마인더 이메일 발송.

        Args:
            reminders: [(Announcement, event_type, days_left), ...]
                       event_type: 'd7' | 'd3' | 'd1'

        Returns:
            {announcement.id: (success, error_message)}
        """
        if not self.enabled or not self._is_configured():
            return {ann.id: (False, "이메일 비활성화 또는 설정 불완전") for ann, _, _ in reminders}
        if not reminders:
            return {}

        # 긴급도 순 정렬 (d1 -> d3 -> d7)
        order = {"d1": 0, "d3": 1, "d7": 2}
        reminders_sorted = sorted(reminders, key=lambda x: order.get(x[1], 9))

        subject = self._reminder_subject(reminders_sorted)
        html    = self._build_reminder_html(reminders_sorted)
        msg     = self._make_message(subject, html)

        ok, err = self._send(msg)
        result = {}
        for ann, _, _ in reminders:
            result[ann.id] = (ok, err if not ok else "")
            if ok:
                logger.info(f"리마인더 발송 완료: [{ann.source}] {ann.title[:30]}")
            else:
                logger.error(f"리마인더 발송 실패: {err}")
        return result

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _is_configured(self) -> bool:
        return bool(self.sender and self.password and self.recipients)

    def _make_message(self, subject: str, html: str) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self.sender
        msg["To"]      = ", ".join(self.recipients)
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg

    def _send(self, msg: MIMEMultipart) -> tuple[bool, str]:
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(self.sender, self.password)
                srv.sendmail(self.sender, self.recipients, msg.as_string())
            return True, ""
        except smtplib.SMTPAuthenticationError:
            return False, "[FAIL] 인증 실패: Gmail 앱 비밀번호를 확인해 주세요."
        except Exception as e:
            return False, f"[FAIL] 발송 실패: {e}"

    @staticmethod
    def _reminder_subject(reminders: list[tuple]) -> str:
        min_days = min(days for _, _, days in reminders)
        count    = len(reminders)
        if min_days <= 1:
            return f"[내일 마감] 연구과제 {count}건 마감 임박"
        elif min_days <= 3:
            return f"[D-{min_days} 마감] 연구과제 {count}건 마감 임박"
        else:
            return f"[D-{min_days} 마감] 연구과제 {count}건 마감 예정"

    # ------------------------------------------------------------------
    # HTML 템플릿
    # ------------------------------------------------------------------

    _STYLE = """
        body { font-family: Arial, '맑은 고딕', sans-serif; max-width: 720px;
               margin: 0 auto; color: #333; }
        .header { padding: 20px 24px; border-radius: 8px 8px 0 0; }
        .header h2 { color: white; margin: 0; font-size: 1.1rem; }
        .header p  { color: rgba(255,255,255,0.85); margin: 4px 0 0; font-size: 0.85rem; }
        .body { padding: 0; border: 1px solid #ddd; border-top: none;
                border-radius: 0 0 8px 8px; }
        table { width: 100%; border-collapse: collapse; }
        th { padding: 10px 12px; background: #f8f9fa; font-size: 0.78rem;
             color: #666; text-align: left; border-bottom: 2px solid #eee; }
        td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 0.875rem; }
        .source { display: inline-block; padding: 2px 7px; border-radius: 4px;
                  font-size: 0.72rem; font-weight: 700; color: white; }
        .src-nrf  { background: #1d4ed8; }
        .src-ntis { background: #7c3aed; }
        .src-iris { background: #ea580c; }
        .dday { display: inline-block; padding: 3px 9px; border-radius: 12px;
                font-size: 0.78rem; font-weight: 700; white-space: nowrap; }
        .dday-1 { background: #fee2e2; color: #dc2626; }
        .dday-3 { background: #ffedd5; color: #ea580c; }
        .dday-7 { background: #fef9c3; color: #a16207; }
        a { color: #2d6a9f; text-decoration: none; }
        .footer { padding: 14px 16px; background: #f8f9fa;
                  border-top: 1px solid #eee; border-radius: 0 0 8px 8px; }
        .footer p { margin: 0; color: #999; font-size: 0.78rem; }
    """

    @classmethod
    def _build_test_html(cls) -> str:
        return f"""<html><head><style>{cls._STYLE}</style></head><body>
        <div class="header" style="background:#4A90D9;">
            <h2>ResearchProjAssist</h2>
            <p>연구과제 자동 알림 시스템</p>
        </div>
        <div class="body" style="padding:24px;">
            <h3 style="color:#333;">이메일 연결 테스트 성공</h3>
            <p style="color:#555;margin-top:8px;">
                이메일 알림 설정이 완료되었습니다.<br>
                앞으로 새 연구과제 공고와 마감 임박 리마인더가 이 주소로 발송됩니다.
            </p>
        </div>
        <div class="footer"><p>본 메일은 ResearchProjAssist 자동 알림 시스템에서 발송되었습니다.</p></div>
        </body></html>"""

    @classmethod
    def _build_new_html(cls, announcements: list[Announcement]) -> str:
        today = date.today()
        source_names = {"nrf": "NRF", "ntis": "NTIS", "iris": "IRIS"}
        rows = ""
        for ann in announcements:
            src   = source_names.get(ann.source, ann.source)
            badge = cls._deadline_badge(ann.deadline, today)
            rows += f"""<tr>
                <td><span class="source src-{ann.source}">{src}</span></td>
                <td><a href="{ann.url}">{ann.title}</a></td>
                <td style="color:#666;font-size:0.82rem;">{ann.category or '-'}</td>
                <td>{badge}</td>
            </tr>"""

        return f"""<html><head><style>{cls._STYLE}</style></head><body>
        <div class="header" style="background:#1d4ed8;">
            <h2>새 연구과제 공고 알림</h2>
            <p>총 {len(announcements)}건의 새 공고가 등록되었습니다.</p>
        </div>
        <div class="body">
            <table>
                <thead><tr>
                    <th>출처</th><th>제목</th><th>분야</th><th>마감일</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        <div class="footer">
            <p>대시보드: <a href="http://localhost:8000">ResearchProjAssist</a> &nbsp;|&nbsp;
               본 메일은 자동 알림 시스템에서 발송되었습니다.</p>
        </div>
        </body></html>"""

    @classmethod
    def _build_reminder_html(cls, reminders: list[tuple]) -> str:
        today = date.today()
        source_names = {"nrf": "NRF", "ntis": "NTIS", "iris": "IRIS"}

        # 긴급도에 따른 헤더 색상
        min_days = min(days for _, _, days in reminders)
        if min_days <= 1:
            header_color = "#dc2626"
            header_label = "내일 마감 — 지금 확인하세요"
        elif min_days <= 3:
            header_color = "#ea580c"
            header_label = f"D-{min_days} 마감 임박"
        else:
            header_color = "#a16207"
            header_label = f"D-{min_days} 마감 예정"

        rows = ""
        for ann, event_type, days_left in reminders:
            src   = source_names.get(ann.source, ann.source)
            dday_class = f"dday-{days_left if days_left <= 7 else 7}"
            if days_left <= 1:
                dday_class = "dday-1"
            elif days_left <= 3:
                dday_class = "dday-3"
            else:
                dday_class = "dday-7"

            dday_label = "내일 마감" if days_left <= 1 else f"D-{days_left}"
            deadline_str = ann.deadline.strftime("%Y-%m-%d") if ann.deadline else "-"

            rows += f"""<tr>
                <td><span class="source src-{ann.source}">{src}</span></td>
                <td><a href="{ann.url}">{ann.title}</a></td>
                <td style="color:#666;font-size:0.82rem;">{ann.category or '-'}</td>
                <td>{deadline_str}</td>
                <td><span class="dday {dday_class}">{dday_label}</span></td>
            </tr>"""

        return f"""<html><head><style>{cls._STYLE}</style></head><body>
        <div class="header" style="background:{header_color};">
            <h2>마감 임박 리마인더 — {header_label}</h2>
            <p>아래 {len(reminders)}건의 공고 마감이 임박했습니다.</p>
        </div>
        <div class="body">
            <table>
                <thead><tr>
                    <th>출처</th><th>제목</th><th>분야</th><th>마감일</th><th>남은 기간</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        <div class="footer">
            <p>대시보드: <a href="http://localhost:8000">ResearchProjAssist</a> &nbsp;|&nbsp;
               본 메일은 자동 알림 시스템에서 발송되었습니다.</p>
        </div>
        </body></html>"""

    @staticmethod
    def _deadline_badge(deadline, today: date) -> str:
        if not deadline:
            return '<span style="color:#ccc;">-</span>'
        days = (deadline - today).days
        ds   = deadline.strftime("%Y-%m-%d")
        if days < 0:
            return f'<span style="color:#9ca3af;text-decoration:line-through;">{ds}</span>'
        elif days <= 1:
            return f'<span class="dday dday-1">{ds} (내일 마감)</span>'
        elif days <= 3:
            return f'<span class="dday dday-3">{ds} (D-{days})</span>'
        elif days <= 7:
            return f'<span class="dday dday-7">{ds} (D-{days})</span>'
        else:
            return f'<span style="color:#2d7a2d;">{ds} (D-{days})</span>'
