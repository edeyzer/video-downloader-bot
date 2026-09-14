"""
SQLAlchemy 2.0 Async Models
"""

from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Telegram user_id as primary key

    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="uz")

    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Daily limits tracking
    daily_downloads: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_download_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Premium expiry (null = lifetime or not premium)
    premium_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    downloads: Mapped[List["DownloadLog"]] = relationship(
        "DownloadLog", back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[List["Subscription"]] = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username} premium={self.is_premium}>"


class Subscription(Base):
    """
    Premium subscription history / active subscriptions.
    One user can have multiple subscription records (history).
    """
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    plan: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "monthly", "yearly", "lifetime"
    amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # in tiyin / cents
    currency: Mapped[str] = mapped_column(String(10), default="UZS")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    payment_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "telegram", "click", "payme"
    payment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="subscriptions")

    __table_args__ = (
        Index("ix_subscriptions_user_active", "user_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} user_id={self.user_id} plan={self.plan}>"


class DownloadLog(Base):
    """
    Every download attempt is logged for analytics, limits and caching stats.
    """
    __tablename__ = "download_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)  # youtube, instagram, tiktok...
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # video, audio, carousel...

    # Quality & format
    quality: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 720p, 1080p, 4k, mp3
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # bytes

    # Telegram file_id for caching
    file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    file_unique_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending", index=True
    )  # pending, processing, success, failed, cached
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Performance
    download_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    was_cached: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="downloads")

    __table_args__ = (
        Index("ix_download_logs_user_created", "user_id", "created_at"),
        Index("ix_download_logs_url_hash", "url"),  # for cache lookups (can add hash later)
    )

    def __repr__(self) -> str:
        return f"<DownloadLog id={self.id} user_id={self.user_id} status={self.status}>"