import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # .env 환경변수로 민감 정보 오버라이드
    email_cfg = config.get("notifications", {}).get("email", {})
    if os.getenv("EMAIL_PASSWORD"):
        email_cfg["password"] = os.getenv("EMAIL_PASSWORD")
    if os.getenv("EMAIL_SENDER"):
        email_cfg["sender"] = os.getenv("EMAIL_SENDER")

    tg_cfg = config.get("notifications", {}).get("telegram", {})
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        tg_cfg["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        tg_cfg["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")

    # DB 경로를 절대 경로로 변환
    db_path = config.get("database", {}).get("path", "data/announcements.db")
    if not os.path.isabs(db_path):
        config["database"]["path"] = str(BASE_DIR / db_path)

    return config
