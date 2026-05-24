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


def get_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def init_db(db_path: str):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)   # 새 테이블(notification_logs)도 자동 생성
    return engine


def get_session(engine):
    Session = sessionmaker(bind=engine)
    return Session()
