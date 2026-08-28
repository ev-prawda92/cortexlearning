"""
Minimal persistence layer — just enough substrate for the learning code.

This is a trimmed extract of a larger agent-operations platform. Only the two
tables the analysis actually reads are here:

    Agent   an agent and its current config version
    Run     one execution, tagged with the config version that produced it

That second column is the whole trick. Most agent tooling records what a run
did; recording which *version of the configuration* produced it is what makes
"did my last change help?" answerable at all.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (create_engine, Column, String, Text, Integer, Float,
                        DateTime, JSON, Boolean, ForeignKey, Index)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(dt):
    """Return dt as timezone-aware UTC.

    SQLite does not preserve tzinfo on DateTime(timezone=True) columns, so a
    value written aware reads back naive; Postgres round-trips it correctly.
    Supporting both means every datetime subtraction goes through here.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True, default=gen_id)
    owner_id = Column(String(64), nullable=True, index=True)
    slug = Column(String(128), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")

    # Bumped on every approved config change. Denormalized from AgentVersion
    # so a run can record it without a join.
    version = Column(Integer, default=1)
    config = Column(JSON, default=dict)

    status = Column(String(32), default="stopped")
    is_deleted = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    runs = relationship("Run", back_populates="agent",
                        cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id = Column(String(64), primary_key=True, default=gen_id)
    agent_id = Column(String(64), ForeignKey("agents.id"),
                      nullable=False, index=True)

    # COMPLETED | ESCALATED | ERROR
    outcome = Column(String(32), default="")
    # The config version in force when this run executed.
    config_version = Column(Integer, default=1, index=True)

    model = Column(String(128), default="")
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)

    started_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    agent = relationship("Agent", back_populates="runs")

    __table_args__ = (Index("ix_runs_agent_version", "agent_id", "config_version"),)

    @property
    def latency_ms(self) -> float:
        if not self.started_at or not self.finished_at:
            return 0.0
        return max(0.0, (as_aware(self.finished_at)
                         - as_aware(self.started_at)).total_seconds() * 1000.0)


def make_session(url: str = "sqlite:///learning_demo.db"):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()
