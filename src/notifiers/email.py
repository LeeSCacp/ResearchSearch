"""이메일 알림 모듈 (SMTP 기반)."""

import smtplib
import logging
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from src.config import today_kst
from src.models.announcement import Announcement

logger = logging.getLogger(__name__)

# D-day 리마인더 임계값 정의 (2026-07-18 확장: 신규 확인 1회 + 5단계)
# 반드시 긴급한 순(오름차순)으로 정렬 — 스케줄러가 첫 매칭 단계를 선택한다.
REMINDER_THRESHOLDS = [
    ("d1",  1,  "내일 마감"),
    ("d3",  3,  "D-3 마감 임박"),
    ("d7",  7,  "D-7 마감 임박"),
    ("d14", 14, "D-14 마감 2주 전"),
    ("d30", 30, "D-30 마감 한 달 전"),
]


class EmailNotifier:
    def __init__(self, config: dict):
        self.enabled       = config.get("enabled", False)
        self.smtp_host     = config.get("smtp_host", "smtp.gmail.com")
        self.smtp_port     = config.get("smtp_port", 587)
        self.sender        = config.get("sender", "")
        self.password      = config.get("password", "")
        self.recipients    = config.get("recipients", [])
        self.dashboard_url = config.get(
            "dashboard_url", "https://leescacp.github.io/ResearchSearch/"
        )

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
        msg = self._make_message(subject, self._build_new_html(announcements))  # instance method
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
        html    = self._build_reminder_html(reminders_sorted)  # instance method
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
    # 일일 다이제스트 (신규 + 리마인더 통합, 하루 1통) — 카드형 레이아웃
    # ------------------------------------------------------------------

    _SRC_COLORS = {"nrf": "#1d4ed8", "ntis": "#7c3aed", "iris": "#ea580c"}
    _SRC_NAMES  = {"nrf": "NRF", "ntis": "NTIS", "iris": "IRIS"}

    def send_digest(
        self,
        new_items: list[tuple[Announcement, list[str]]],
        reminder_items: list[tuple[Announcement, str, int, list[str]]],
        today,
    ) -> tuple[bool, str]:
        """일일 다이제스트 1통 발송.

        Args:
            new_items:      [(Announcement, 매칭근거), ...]
            reminder_items: [(Announcement, event_type, days_left, 매칭근거), ...]
            today:          KST 기준 오늘 (제목 표기용)
        """
        if not self.enabled:
            return False, "이메일 비활성화"
        if not self._is_configured():
            return False, "이메일 설정 불완전"
        if not new_items and not reminder_items:
            return True, ""   # 보낼 내용 없음 — 침묵

        parts = []
        if new_items:      parts.append(f"신규 {len(new_items)}건")
        if reminder_items: parts.append(f"마감임박 {len(reminder_items)}건")
        weekday = "월화수목금토일"[today.weekday()]
        subject = (f"[연구과제 알림] {' · '.join(parts)}"
                   f" — {today.month}월 {today.day}일 ({weekday})")

        html = self._build_digest_html(new_items, reminder_items, today)
        msg  = self._make_message(subject, html)
        ok, err = self._send(msg)
        if ok:
            logger.info(f"다이제스트 발송 완료: 신규 {len(new_items)} + 리마인더 {len(reminder_items)}")
        else:
            logger.error(f"다이제스트 발송 실패: {err}")
        return ok, err

    def _digest_card(self, ann: Announcement, reasons: list[str],
                     days_left: int | None, stage_label: str = "") -> str:
        """공고 1건 → 모바일 1열 카드 HTML (이메일 클라이언트 호환 인라인 스타일)."""
        src_color = self._SRC_COLORS.get(ann.source, "#6b7280")
        src_name  = self._SRC_NAMES.get(ann.source, (ann.source or "?").upper())

        # D-day 배지 (남은 일수별 색상)
        dday_html = ""
        if days_left is not None:
            if   days_left <= 3:  bg, fg = "#fee2e2", "#dc2626"
            elif days_left <= 7:  bg, fg = "#ffedd5", "#ea580c"
            elif days_left <= 14: bg, fg = "#fef9c3", "#a16207"
            else:                 bg, fg = "#e0f2fe", "#075985"
            label = "오늘 마감" if days_left == 0 else f"D-{days_left}"
            dday_html = (f'<span style="display:inline-block;padding:2px 10px;'
                         f'border-radius:12px;font-size:12px;font-weight:700;'
                         f'background:{bg};color:{fg};">{label}</span>')

        stage_html = ""
        if stage_label:
            stage_html = (f'<span style="display:inline-block;padding:2px 8px;'
                          f'border-radius:4px;font-size:11px;background:#f3f4f6;'
                          f'color:#4b5563;">{stage_label}</span>')

        # 매칭 근거 배지
        reason_html = ""
        if reasons:
            chips = "".join(
                f'<span style="display:inline-block;padding:1px 8px;margin:1px 2px 1px 0;'
                f'border-radius:10px;font-size:11px;background:#eef2ff;color:#3730a3;">'
                f'{r}</span>'
                for r in reasons[:4]
            )
            reason_html = (f'<div style="margin-top:6px;font-size:11px;color:#9ca3af;">'
                           f'매칭 {chips}</div>')

        # 접수 기간
        period = ""
        if ann.posted_date or ann.deadline:
            s = ann.posted_date.strftime("%m.%d") if ann.posted_date else "?"
            e = ann.deadline.strftime("%m.%d")    if ann.deadline    else "?"
            period = f"접수 {s} ~ {e}"

        meta_rows = []
        if ann.category:
            meta_rows.append(ann.category[:60])
        if period:
            meta_rows.append(period)
        if ann.budget:
            meta_rows.append(f"예산 {ann.budget[:40]}")
        meta_html = " &nbsp;·&nbsp; ".join(meta_rows)

        return f"""
        <div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;
                    margin-bottom:10px;background:#ffffff;">
          <div style="margin-bottom:8px;">
            <span style="display:inline-block;padding:2px 8px;border-radius:4px;
                         font-size:11px;font-weight:700;color:#fff;
                         background:{src_color};">{src_name}</span>
            &nbsp;{dday_html}&nbsp;{stage_html}
          </div>
          <a href="{ann.url}" style="color:#111827;text-decoration:none;
             font-size:15px;font-weight:600;line-height:1.45;">{ann.title}</a>
          <div style="margin-top:6px;font-size:12px;color:#6b7280;">{meta_html}</div>
          {reason_html}
          <div style="margin-top:10px;">
            <a href="{ann.url}" style="display:inline-block;padding:6px 14px;
               border-radius:6px;background:#2d6a9f;color:#ffffff;
               font-size:12px;text-decoration:none;">공고 보기 →</a>
          </div>
        </div>"""

    def _build_digest_html(self, new_items, reminder_items, today) -> str:
        weekday = "월화수목금토일"[today.weekday()]

        sections = []
        if new_items:
            cards = "".join(
                self._digest_card(ann, reasons,
                                  (ann.deadline - today).days if ann.deadline else None)
                for ann, reasons in new_items
            )
            sections.append(f"""
            <div style="margin:18px 0 8px;font-size:14px;font-weight:700;color:#111827;">
              🆕 새로 확인된 공고 <span style="color:#2d6a9f;">{len(new_items)}건</span>
            </div>{cards}""")

        if reminder_items:
            stage_names = {et: label for et, _, label in REMINDER_THRESHOLDS}
            cards = "".join(
                self._digest_card(ann, reasons, days_left,
                                  stage_label=stage_names.get(event, event))
                for ann, event, days_left, reasons in reminder_items
            )
            sections.append(f"""
            <div style="margin:18px 0 8px;font-size:14px;font-weight:700;color:#111827;">
              ⏰ 마감 임박 리마인더 <span style="color:#dc2626;">{len(reminder_items)}건</span>
            </div>{cards}""")

        body = "".join(sections)
        return f"""<html><body style="margin:0;padding:0;background:#f5f7fa;">
        <div style="max-width:600px;margin:0 auto;padding:16px;
                    font-family:Arial,'맑은 고딕',sans-serif;">
          <div style="background:linear-gradient(135deg,#1e3a5f,#2d6a9f);
                      border-radius:10px 10px 0 0;padding:18px 20px;">
            <div style="color:#ffffff;font-size:16px;font-weight:700;">
              오늘의 연구과제 다이제스트</div>
            <div style="color:rgba(255,255,255,0.8);font-size:12px;margin-top:3px;">
              {today.year}년 {today.month}월 {today.day}일 ({weekday}) · 매일 아침 9시 발송</div>
          </div>
          <div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;
                      border-radius:0 0 10px 10px;padding:8px 14px 14px;">
            {body}
            <div style="margin-top:14px;padding-top:12px;border-top:1px solid #e5e7eb;
                        font-size:11px;color:#9ca3af;text-align:center;">
              <a href="{self.dashboard_url}" style="color:#2d6a9f;text-decoration:none;">
                전체 공고 대시보드</a> &nbsp;|&nbsp;
              <a href="{self.dashboard_url}analytics.html" style="color:#2d6a9f;text-decoration:none;">
                패턴 분석·준비 캘린더</a><br>
              ResearchProjAssist 자동 알림 · 관련 없는 공고가 왔다면 필터 조정이 필요합니다
            </div>
          </div>
        </div>
        </body></html>"""

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

    def _build_new_html(self, announcements: list[Announcement]) -> str:
        today = today_kst()
        source_names = {"nrf": "NRF", "ntis": "NTIS", "iris": "IRIS"}
        rows = ""
        for ann in announcements:
            src   = source_names.get(ann.source, ann.source)
            badge = self._deadline_badge(ann.deadline, today)
            rows += f"""<tr>
                <td><span class="source src-{ann.source}">{src}</span></td>
                <td><a href="{ann.url}">{ann.title}</a></td>
                <td style="color:#666;font-size:0.82rem;">{ann.category or '-'}</td>
                <td>{badge}</td>
            </tr>"""

        return f"""<html><head><style>{self._STYLE}</style></head><body>
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
            <p>대시보드: <a href="{self.dashboard_url}">ResearchProjAssist</a> &nbsp;|&nbsp;
               본 메일은 자동 알림 시스템에서 발송되었습니다.</p>
        </div>
        </body></html>"""

    def _build_reminder_html(self, reminders: list[tuple]) -> str:
        today = today_kst()
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

        return f"""<html><head><style>{self._STYLE}</style></head><body>
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
            <p>대시보드: <a href="{self.dashboard_url}">ResearchProjAssist</a> &nbsp;|&nbsp;
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
