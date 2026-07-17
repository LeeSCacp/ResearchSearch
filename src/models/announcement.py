from datetime import datetime, date

from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    Date, DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship


class Base(DeclarativeBase):
    pass


class Announcement(Base):
    __tablename__ = "announcements"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    title       = Column(Text, nullable=False)
    url         = Column(Text, unique=True, nullable=False)
    source      = Column(String(20), nullable=False)   # 'nrf', 'ntis', 'iris'
    category    = Column(String(100))
    deadline    = Column(Date)
    posted_date = Column(Date)
    description = Column(Text)                         # 사업 개요/목적
    budget      = Column(String(300))                  # 지원규모 (예: 과제당 3억원 이내)
    attachments = Column(Text)                         # 첨부파일 URL 목록 (줄바꿈 구분)
    detail_fetched = Column(Boolean, default=False)    # 상세 수집 완료 여부
    is_notified = Column(Boolean, default=False)       # 신규 공고 알림 여부 (레거시)
    created_at  = Column(DateTime, default=datetime.now)

    notification_logs = relationship("NotificationLog", back_populates="announcement",
                                     cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Announcement(id={self.id}, source={self.source}, title={self.title[:30]})>"

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "title":          self.title,
            "url":            self.url,
            "source":         self.source,
            "category":       self.category,
            "deadline":       self.deadline.isoformat() if self.deadline else None,
            "posted_date":    self.posted_date.isoformat() if self.posted_date else None,
            "description":    self.description,
            "budget":         self.budget,
            "attachments":    self.attachments,
            "detail_fetched": self.detail_fetched,
            "is_notified":    self.is_notified,
            "created_at":     self.created_at.isoformat() if self.created_at else None,
        }


class NotificationLog(Base):
    """발송된 알림 이력 테이블.

    신규 공고 알림과 D-day 리마인더를 채널·이벤트 유형별로 기록한다.
    중복 발송 방지 및 실패/재시도 추적에 사용된다.
    """
    __tablename__ = "notification_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    announcement_id = Column(Integer, ForeignKey("announcements.id"), nullable=False)
    channel         = Column(String(20), nullable=False)   # 'email', 'telegram'
    event_type      = Column(String(10), nullable=False)   # 'new', 'd7', 'd3', 'd1'
    sent_at         = Column(DateTime, default=datetime.now)
    success         = Column(Boolean, default=True)
    error_message   = Column(Text, nullable=True)

    announcement = relationship("Announcement", back_populates="notification_logs")

    def __repr__(self):
        return (f"<NotificationLog(ann_id={self.announcement_id}, "
                f"channel={self.channel}, event={self.event_type}, "
                f"success={self.success})>")


class Filter(Base):
    __tablename__ = "filters"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    keywords   = Column(Text)       # 쉼표 구분
    categories = Column(Text)       # 쉼표 구분
    is_active  = Column(Boolean, default=True)


class HistoricalAnnouncement(Base):
    """5년치 과거 공고 — 운영 DB(announcements)와 분리하여 패턴 분석 전용으로 사용.

    수집 출처: NRF (추후 NTIS/IRIS 확장 가능, source 컬럼으로 구분).
    수집 시점: 일회성 + 점진적 갱신.
    분석 용도: 시즌성, 다음 공고 예측, 분야별 트렌드, 마감일 패턴.
    """
    __tablename__ = "historical_announcements"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    title         = Column(Text, nullable=False)
    url           = Column(Text, unique=True, nullable=False)
    source        = Column(String(20), nullable=False, default="nrf")   # 'nrf'/'ntis'/'iris'
    category      = Column(Text)                       # NRF 분야 (예: "인문사회분야 > 개인연구군")
    notice_type   = Column(String(40))                 # 공고 유형 (접수마감/접수중/보고서제출/사업관리/기타 등)
    posted_date   = Column(Date)                       # 접수 시작일
    deadline      = Column(Date)                       # 접수 마감일
    year          = Column(Integer)                    # 검색 기준 연도 (수집 편의용 인덱스)

    # 분야 자동 라벨 (Phase D — 다중 라벨)
    label_psychology     = Column(Boolean, default=False)  # 심리학 전반
    label_aging          = Column(Boolean, default=False)  # 노화·치매·고령
    label_psy_ai         = Column(Boolean, default=False)  # 심리학 + AI 융합
    label_humanities     = Column(Boolean, default=False)  # 인문사회 전반

    collected_at  = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "url": self.url,
            "source": self.source, "category": self.category,
            "notice_type": self.notice_type,
            "posted_date": self.posted_date.isoformat() if self.posted_date else None,
            "deadline":    self.deadline.isoformat()    if self.deadline    else None,
            "year": self.year,
            "labels": {
                "psychology": self.label_psychology,
                "aging":      self.label_aging,
                "psy_ai":     self.label_psy_ai,
                "humanities": self.label_humanities,
            },
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
        }


class SourceHealth(Base):
    """스크래퍼 소스별 연속 실패 카운터 — 침묵 고장 감지용.

    일시적 네트워크 오류로 워크플로가 실패 처리되는 것을 막기 위해
    연속 2회(12시간) 이상 실패 시에만 경보 대상으로 삼는다.
    """
    __tablename__ = "source_health"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    source               = Column(String(20), nullable=False, unique=True)
    consecutive_failures = Column(Integer, default=0)
    last_ok_at           = Column(DateTime)
    last_error           = Column(Text)
    updated_at           = Column(DateTime, default=datetime.now)


class DigestLog(Base):
    """일일 다이제스트 발송 기록 — KST 날짜당 최대 1통 보장.

    item_count=0 기록은 "보낼 내용이 없어 침묵한 날"을 뜻한다
    (그날 이후 사이클에서 다이제스트를 다시 시도하지 않기 위해 기록).
    """
    __tablename__ = "digest_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    digest_date = Column(Date, nullable=False, unique=True)   # KST 기준 날짜
    sent_at     = Column(DateTime, default=datetime.now)
    item_count  = Column(Integer, default=0)                  # 0 = 침묵한 날
    success     = Column(Boolean, default=True)


class CollectionCheckpoint(Base):
    """수집 진행 상태 체크포인트 (중단 복구용)."""
    __tablename__ = "collection_checkpoints"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    source        = Column(String(20), nullable=False)   # 'nrf' 등
    year          = Column(Integer, nullable=False)
    page          = Column(Integer, nullable=False)
    completed     = Column(Boolean, default=False)
    items_count   = Column(Integer, default=0)
    last_attempt  = Column(DateTime, default=datetime.now)
    error_message = Column(Text)


def get_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def init_db(db_path: str):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)   # 새 테이블(notification_logs)도 자동 생성
    return engine


def get_session(engine):
    Session = sessionmaker(bind=engine)
    return Session()
