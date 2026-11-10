"""SQLAlchemy-модели и атомарные транзакции для SQLite/PostgreSQL."""
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (BigInteger, DateTime, ForeignKey, Integer, JSON,
                        String, UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from engine import START_BALANCE, fresh_stats


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = 'players'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=START_BALANCE, nullable=False)
    spins: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, default=fresh_stats, nullable=False)
    claimed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    auto_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    installed_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    guarantees: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    luck_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Roll(Base):
    __tablename__ = 'rolls'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(String(96), ForeignKey('players.id'), index=True)
    tag: Mapped[str] = mapped_column(String(32))
    rarity: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(BigInteger)
    multiplier: Mapped[int] = mapped_column(BigInteger)
    bonuses: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Item(Base):
    __tablename__ = 'items'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(96), ForeignKey('players.id'), index=True)
    tag: Mapped[str] = mapped_column(String(32))
    rarity: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(BigInteger)
    multiplier: Mapped[int] = mapped_column(BigInteger)
    bonuses: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default='inventory')
    asking_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequestLog(Base):
    __tablename__ = 'request_logs'
    __table_args__ = (UniqueConstraint('player_id', 'request_id', name='unique_spin_request'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String(96), ForeignKey('players.id'))
    request_id: Mapped[str] = mapped_column(String(80))
    body: Mapped[dict] = mapped_column(JSON)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


URL = os.environ.get('DATABASE_URL', '')
if URL.startswith('postgres://'):
    URL = 'postgresql+psycopg://' + URL[len('postgres://'):]
elif URL.startswith('postgresql://'):
    URL = 'postgresql+psycopg://' + URL[len('postgresql://'):]
if not URL:
    URL = 'sqlite:///' + str(Path(__file__).resolve().parent / 'yuzy_dev.sqlite3')

kwargs = {'pool_pre_ping': True}
if URL.startswith('sqlite'):
    kwargs['connect_args'] = {'check_same_thread': False, 'timeout': 30}
engine = create_engine(URL, **kwargs)

if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def configure_sqlite(dbapi_connection, record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.close()

Base.metadata.create_all(engine)


@contextmanager
def transaction():
    """SQLite: сериализуем запись BEGIN IMMEDIATE; PostgreSQL: транзакция + row locks."""
    with engine.begin() as conn:
        if engine.dialect.name == 'sqlite':
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        with Session(bind=conn, expire_on_commit=False) as db:
            yield db
            db.flush()


def get_player(db: Session, player_id: str, lock: bool = False) -> Player:
    query = db.query(Player).filter(Player.id == player_id)
    player = query.with_for_update().one_or_none() if lock else query.one_or_none()
    if player is None:
        player = Player(id=player_id, balance=START_BALANCE, spins=0,
                        stats=fresh_stats(), claimed={}, guarantees={})
        db.add(player)
        db.flush()
    return player
