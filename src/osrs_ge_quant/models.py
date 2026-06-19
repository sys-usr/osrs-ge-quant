# src/osrs_ge_quant/models.py
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from .db import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    rsn: Mapped[str] = mapped_column(String, unique=True)
    starting_gp: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String, default="core_liquidity")

    trades = relationship("Trade", back_populates="account")
    balances = relationship("AccountBalance", back_populates="account")


class AccountBalance(Base):
    __tablename__ = "account_balances"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    gp: Mapped[int] = mapped_column(Integer)

    account = relationship("Account", back_populates="balances")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)  # OSRS item id
    name: Mapped[str] = mapped_column(String, index=True)
    examine: Mapped[Optional[str]] = mapped_column(String)
    limit: Mapped[Optional[int]] = mapped_column(Integer)
    members: Mapped[Optional[bool]] = mapped_column(Boolean)
    tradeable: Mapped[bool] = mapped_column(Boolean, default=True)

    prices = relationship("PricePoint", back_populates="item")


class PricePoint(Base):
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    avg_high: Mapped[Optional[float]] = mapped_column(Float)
    avg_low: Mapped[Optional[float]] = mapped_column(Float)
    high_vol: Mapped[Optional[int]] = mapped_column(Integer)
    low_vol: Mapped[Optional[int]] = mapped_column(Integer)
    timestep: Mapped[str] = mapped_column(String, default="24h")

    item = relationship("Item", back_populates="prices")
    __table_args__ = (
        UniqueConstraint("item_id", "ts", "timestep", name="uq_item_ts_step"),
        Index("idx_prices_item_ts_step", "item_id", "ts", "timestep"),
    )


class Recommendation(Base):
    """
    Recommended trade/opportunity (flip or processing), with taken/skip info.
    """
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    side: Mapped[Optional[str]] = mapped_column(String)  # "buy"/"sell" or None
    qty: Mapped[Optional[int]] = mapped_column(Integer)
    price_each: Mapped[Optional[int]] = mapped_column(Integer)
    expected_profit_gp: Mapped[Optional[float]] = mapped_column(Float)
    expected_return_pct: Mapped[Optional[float]] = mapped_column(Float)
    signal_type: Mapped[str] = mapped_column(String)  # "pure_flip", "processing", "news"
    reason: Mapped[Optional[str]] = mapped_column(String)

    taken_trade_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("trades.id"), nullable=True
    )
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, index=True, default=datetime.utcnow
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    item_name: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # buy/sell
    qty: Mapped[int] = mapped_column(Integer)
    price_each: Mapped[int] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(String)

    account = relationship("Account", back_populates="trades")
    recommendation = relationship(
        "Recommendation", backref="taken_trade", uselist=False
    )


class NewsPost(Base):
    __tablename__ = "news_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    date: Mapped[datetime] = mapped_column(DateTime)
    category: Mapped[Optional[str]] = mapped_column(String)
    summary: Mapped[Optional[str]] = mapped_column(String)
    raw_text: Mapped[Optional[str]] = mapped_column(String)

    __table_args__ = (
        Index("idx_news_date_cat", "date", "category"),
    )


class NewsImpact(Base):
    __tablename__ = "news_impacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    news_post_id: Mapped[int] = mapped_column(ForeignKey("news_posts.id"))
    item_name_keywords: Mapped[str] = mapped_column(String)  # comma-separated
    direction: Mapped[str] = mapped_column(String)  # up/down
    confidence: Mapped[float] = mapped_column(Float)
    expected_move_pct: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(String)

    news_post = relationship("NewsPost")


class DQNExperience(Base):
    __tablename__ = "dqn_experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String)  # Serialized state vector (JSON string)
    action: Mapped[int] = mapped_column(Integer)
    reward: Mapped[float] = mapped_column(Float)
    next_state: Mapped[str] = mapped_column(String)  # Serialized next state vector (JSON string)
    done: Mapped[bool] = mapped_column(Boolean)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    account = relationship("Account")
