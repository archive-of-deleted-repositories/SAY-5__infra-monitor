"""SQLAlchemy 2 storage layer.

Time-series goes into ``metric_samples`` with a composite index on
``(host_id, metric_name, recorded_at)`` so the trend-chart range query is a
straight index scan. SQLite is fine at study scale (~10 hosts, ~5 metrics, 5s
cadence = ~864k rows/day); production swaps in InfluxDB or TimescaleDB without
touching the collector or alert layer.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # "linux" | "ec2"
    cloud_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    samples: Mapped[list[MetricSample]] = relationship(back_populates="host")


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (
        Index("ix_samples_host_metric_time", "host_id", "metric_name", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id"), index=True)
    metric_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    tags: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    host: Mapped[Host] = relationship(back_populates="samples")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    metric: Mapped[str] = mapped_column(String(64))
    condition: Mapped[str] = mapped_column(String(32))  # e.g. ">90"
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    actions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    events: Mapped[list[AlertEvent]] = relationship(back_populates="rule")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id"), index=True)
    metric_value: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rule: Mapped[AlertRule] = relationship(back_populates="events")


class RemediationAction(Base):
    __tablename__ = "remediation_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id"), index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    script_path: Mapped[str] = mapped_column(String(512))
    args_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_excerpt: Mapped[str] = mapped_column(String(4096), default="")
    stderr_excerpt: Mapped[str] = mapped_column(String(4096), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


@dataclass
class Storage:
    """Thin wrapper around an engine + session factory.

    Wrapped so tests can build an in-memory store with one line:
    ``Storage.in_memory()`` and the production code uses
    ``Storage.from_url(settings.database_url)`` — same API.
    """

    engine: Any
    SessionLocal: sessionmaker[Session]

    @classmethod
    def from_url(cls, url: str) -> Storage:
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        engine = create_engine(url, future=True, connect_args=connect_args)
        return cls(engine=engine, SessionLocal=sessionmaker(bind=engine, expire_on_commit=False))

    @classmethod
    def in_memory(cls) -> Storage:
        # Use StaticPool so every session shares the same in-memory connection;
        # otherwise each new connection gets a brand-new empty database.
        engine = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        store = cls(engine=engine, SessionLocal=sessionmaker(bind=engine, expire_on_commit=False))
        store.create_all()
        return store

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---- query helpers -----------------------------------------------------

    def upsert_host(self, name: str, kind: str, cloud_id: str | None = None) -> Host:
        with self.session() as s:
            host = s.execute(select(Host).where(Host.name == name)).scalar_one_or_none()
            if host is None:
                host = Host(name=name, kind=kind, cloud_id=cloud_id)
                s.add(host)
                s.flush()
            return host

    def insert_samples(self, samples: Iterable[MetricSample]) -> None:
        with self.session() as s:
            s.add_all(list(samples))

    def list_hosts(self) -> list[Host]:
        with self.session() as s:
            return list(s.execute(select(Host).order_by(Host.name)).scalars())

    def metrics(
        self,
        *,
        host_id: int,
        metric: str,
        since: datetime,
    ) -> list[MetricSample]:
        with self.session() as s:
            stmt = (
                select(MetricSample)
                .where(MetricSample.host_id == host_id)
                .where(MetricSample.metric_name == metric)
                .where(MetricSample.recorded_at >= since)
                .order_by(MetricSample.recorded_at.asc())
            )
            return list(s.execute(stmt).scalars())

    def recent_alerts(self, limit: int = 50) -> list[AlertEvent]:
        with self.session() as s:
            stmt = select(AlertEvent).order_by(AlertEvent.fired_at.desc()).limit(limit)
            return list(s.execute(stmt).scalars())

    def recent_remediations(self, limit: int = 50) -> list[RemediationAction]:
        with self.session() as s:
            stmt = (
                select(RemediationAction).order_by(RemediationAction.fired_at.desc()).limit(limit)
            )
            return list(s.execute(stmt).scalars())

    def prune_samples(self, *, older_than: datetime) -> int:
        with self.session() as s:
            stmt = select(MetricSample).where(MetricSample.recorded_at < older_than)
            rows = list(s.execute(stmt).scalars())
            for row in rows:
                s.delete(row)
            return len(rows)

    def prune_default_retention(self, days: int) -> int:
        return self.prune_samples(older_than=_now() - timedelta(days=days))
