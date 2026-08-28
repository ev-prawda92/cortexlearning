"""Tests for the version-comparison logic.

The refusal cases matter more than the detection cases — a tool that reports
noise as a finding is worse than no tool, so those get the most coverage.
"""
import random
from datetime import timedelta

import pytest

from models import Base, Agent, Run, utcnow, make_session
from learning import (version_performance, fleet_regressions,
                      MIN_RUNS_PER_VERSION, MEANINGFUL_DELTA)


@pytest.fixture
def db():
    session = make_session("sqlite:///:memory:")
    yield session
    session.close()


def add_agent(db, agent_id="a1", version=1, owner="u1"):
    db.add(Agent(id=agent_id, owner_id=owner, slug=agent_id,
                 name=f"Agent {agent_id}", version=version))
    db.commit()


def add_runs(db, agent_id, version, n, success_rate,
             escalation_rate=0.0, start=None, seed=None):
    """Add n runs at a given version with a deterministic outcome mix."""
    if seed is not None:
        random.seed(seed)
    start = start or (utcnow() - timedelta(days=1))
    for i in range(n):
        r = random.random()
        if r < success_rate:
            outcome = "COMPLETED"
        elif r < success_rate + escalation_rate:
            outcome = "ESCALATED"
        else:
            outcome = "ERROR"
        t = start + timedelta(minutes=i)
        db.add(Run(agent_id=agent_id, config_version=version, outcome=outcome,
                   total_tokens=1500, started_at=t,
                   finished_at=t + timedelta(milliseconds=800)))
    db.commit()


# ── refusals ──────────────────────────────────────────────────────────

def test_no_runs_reports_nothing(db):
    add_agent(db)
    result = version_performance(db, "a1")
    assert result["versions"] == []
    assert result["verdict"] is None


def test_single_version_refuses_to_compare(db):
    add_agent(db)
    add_runs(db, "a1", version=1, n=40, success_rate=1.0)
    verdict = version_performance(db, "a1")["verdict"]
    assert verdict["direction"] == "insufficient_data"
    assert "nothing to compare" in verdict["summary"]


def test_thin_version_refuses_even_with_a_huge_gap(db):
    """A 100%-to-0% collapse across 2 runs is still not a finding."""
    add_agent(db)
    add_runs(db, "a1", version=1, n=30, success_rate=1.0)
    add_runs(db, "a1", version=2, n=2, success_rate=0.0,
             start=utcnow() - timedelta(hours=1))
    verdict = version_performance(db, "a1")["verdict"]
    assert verdict["direction"] == "insufficient_data"
    assert f"need {MIN_RUNS_PER_VERSION}" in verdict["summary"]


def test_small_gap_is_reported_as_no_clear_change(db):
    add_agent(db)
    add_runs(db, "a1", version=1, n=20, success_rate=0.80, seed=1)
    add_runs(db, "a1", version=2, n=20, success_rate=0.75, seed=2,
             start=utcnow() - timedelta(hours=1))
    verdict = version_performance(db, "a1")["verdict"]
    assert verdict["direction"] == "no_clear_change"
    assert abs(verdict["delta"]) < MEANINGFUL_DELTA


# ── detections ────────────────────────────────────────────────────────

def test_detects_a_real_regression(db):
    add_agent(db, version=2)
    add_runs(db, "a1", version=1, n=25, success_rate=0.95, seed=3)
    add_runs(db, "a1", version=2, n=25, success_rate=0.45, escalation_rate=0.4,
             seed=4, start=utcnow() - timedelta(hours=1))
    verdict = version_performance(db, "a1")["verdict"]
    assert verdict["direction"] == "worse"
    assert verdict["delta"] < -MEANINGFUL_DELTA
    assert "Escalations went from" in verdict["summary"]


def test_detects_a_real_improvement(db):
    add_agent(db, version=2)
    add_runs(db, "a1", version=1, n=25, success_rate=0.50, seed=5)
    add_runs(db, "a1", version=2, n=25, success_rate=0.95, seed=6,
             start=utcnow() - timedelta(hours=1))
    verdict = version_performance(db, "a1")["verdict"]
    assert verdict["direction"] == "better"
    assert verdict["delta"] > MEANINGFUL_DELTA


# ── mechanics ─────────────────────────────────────────────────────────

def test_versions_are_newest_first_and_counted_correctly(db):
    add_agent(db, version=3)
    add_runs(db, "a1", version=1, n=6, success_rate=1.0)
    add_runs(db, "a1", version=2, n=7, success_rate=1.0)
    add_runs(db, "a1", version=3, n=8, success_rate=1.0)
    versions = version_performance(db, "a1")["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert [v["runs"] for v in versions] == [8, 7, 6]


def test_outcomes_are_classified_separately(db):
    add_agent(db)
    for outcome in ["COMPLETED"] * 6 + ["ESCALATED"] * 3 + ["ERROR"] * 1:
        db.add(Run(agent_id="a1", config_version=1, outcome=outcome,
                   started_at=utcnow(), finished_at=utcnow()))
    db.commit()
    v = version_performance(db, "a1")["versions"][0]
    assert (v["completed"], v["escalated"], v["error"]) == (6, 3, 1)
    assert v["success_rate"] == 0.6


def test_thin_versions_are_flagged_but_still_reported(db):
    add_agent(db)
    add_runs(db, "a1", version=1, n=2, success_rate=1.0)
    v = version_performance(db, "a1")["versions"][0]
    assert v["runs"] == 2
    assert v["enough_runs"] is False      # reported, but not conclusion-worthy


def test_fleet_surfaces_only_regressions(db):
    add_agent(db, "regressed", version=2)
    add_runs(db, "regressed", 1, 20, 0.95, seed=7)
    add_runs(db, "regressed", 2, 20, 0.40, seed=8, start=utcnow() - timedelta(hours=1))

    add_agent(db, "improved", version=2)
    add_runs(db, "improved", 1, 20, 0.40, seed=9)
    add_runs(db, "improved", 2, 20, 0.95, seed=10, start=utcnow() - timedelta(hours=1))

    add_agent(db, "quiet", version=1)
    add_runs(db, "quiet", 1, 20, 0.90, seed=11)

    names = [r["agent_id"] for r in fleet_regressions(db, owner_id="u1")]
    assert names == ["regressed"]
