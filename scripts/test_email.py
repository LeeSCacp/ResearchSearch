"""이메일 연결 및 발송 테스트 스크립트.

사용법:
    python scripts/test_email.py          # 연결 테스트만
    python scripts/test_email.py --send   # 실제 테스트 메일 발송
"""

import sys
import os

# Windows 콘솔 UTF-8 출력
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.notifiers.email import EmailNotifier


def main():
    send_test_mail = "--send" in sys.argv

    print("=" * 50)
    print("[이메일 설정 테스트]")
    print("=" * 50)

    # 설정 로드
    config = load_config()
    email_cfg = config["notifications"]["email"]

    print("\n[현재 설정]")
    print(f"  SMTP 서버  : {email_cfg['smtp_host']}:{email_cfg['smtp_port']}")
    print(f"  발신자     : {email_cfg['sender'] or '미설정'}")
    pw_status = "설정됨" if email_cfg.get("password") else "미설정 (EMAIL_PASSWORD 환경변수 필요)"
    print(f"  비밀번호   : {pw_status}")
    print(f"  수신자     : {email_cfg['recipients'] or '미설정'}")
    enabled_str = "활성화" if email_cfg["enabled"] else "비활성화 (config.yaml에서 enabled: true 설정 필요)"
    print(f"  알림 상태  : {enabled_str}")

    if not email_cfg.get("password"):
        print("\n" + "=" * 50)
        print("[!] EMAIL_PASSWORD 환경변수가 설정되지 않았습니다.")
        print()
        print("[Gmail 앱 비밀번호 발급 방법]")
        print("  1. https://myaccount.google.com 접속")
        print("  2. 보안 -> 2단계 인증 활성화 (미설정 시)")
        print("  3. 보안 -> 앱 비밀번호 -> 앱 이름 입력 -> 생성")
        print("  4. 발급된 16자리 코드를 .env 파일에 입력:")
        print("     EMAIL_PASSWORD=xxxx xxxx xxxx xxxx")
        print("=" * 50)
        return

    notifier = EmailNotifier(email_cfg)

    # 1단계: SMTP 연결 테스트
    print("\n[SMTP 연결 테스트 중...]")
    ok, msg = notifier.test_connection()
    print(f"  {msg}")

    if not ok:
        print()
        print("[문제 해결 방법]")
        print("  - Gmail 2단계 인증이 활성화되어 있는지 확인")
        print("  - 앱 비밀번호 16자리를 정확히 입력했는지 확인")
        print("  - 방화벽에서 포트 587이 열려 있는지 확인")
        return

    # 2단계: 실제 메일 발송 (--send 옵션 시)
    if send_test_mail:
        print("\n[테스트 메일 발송 중...]")
        ok, msg = notifier.send_test()
        print(f"  {msg}")
        if ok:
            print("\n  받은편지함을 확인해 주세요! (스팸함에 있을 수 있습니다)")
    else:
        print()
        print("[안내] 실제 테스트 메일을 받으려면:")
        print("  python scripts/test_email.py --send")

    print("\n" + "=" * 50)


if __name__ == "__main__":
    main()
