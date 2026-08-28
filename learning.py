"""
Did the last config change help?

Agent platforms are good at telling you what happened — traces, token spend,
latency percentiles. They are mostly silent on the question an operator
actually asks after shipping a change: was that a good idea?

Answering it needs three things, and the third is where most tooling stops:

  1. Configuration changes produce discrete, numbered versions.
  2. Every run records the version that produced it.
  3. Something compares outcomes across those versions and says so plainly.

This module is (3). It writes nothing and adds no instrumentation — it reads
run history that already exists and reports rates.

The design constraint that shaped it: at the volumes a real agent produces in
its first weeks, most differences are noise. A tool that calls a three-point
swing across four runs a "regression" gets ignored inside a month, and then
the honest signal it eventually finds gets ignored too. So the interesting
part of this file is not the arithmetic, it's the cases where it declines to
answer.
"""

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from models import Agent, Run, as_aware, utcnow

# Below this many runs on a version, report the numbers but draw no conclusion.
MIN_RUNS_PER_VERSION = 5

# Completion-rate gap that counts as a real difference rather than noise.
# Deliberately blunt: with double-digit run counts a proper interval estimate
# (Wilson lower bound) is better, and is the natural next step once volumes
# support it. A fixed threshold that is honest about being a threshold beats a
# calculated-looking score whose confidence nobody has validated.
MEANINGFUL_DELTA = 0.15


def version_performance(db: Session, agent_id: str) -> dict:
    """Outcome rates per config version for one agent, newest version first.

    Returns per-version numbers plus a verdict comparing the current version
    to the one before it. The verdict stays silent unless both versions have
    enough runs and the gap is large enough to mean something.
    """
    runs = (db.query(Run)
            .filter(Run.agent_id == agent_id)
            .order_by(Run.started_at).all())

    if not runs:
        return {"agent_id": agent_id, "versions": [], "verdict": None,
                "note": "no runs recorded for this agent yet"}

    buckets: Dict[int, List[Run]] = {}
    for r in runs:
        buckets.setdefault(r.config_version or 1, []).append(r)

    versions = []
    for v in sorted(buckets, reverse=True):
        group = buckets[v]
        n = len(group)
        completed = sum(1 for r in group if (r.outcome or "").upper() == "COMPLETED")
        escalated = sum(1 for r in group if (r.outcome or "").upper() == "ESCALATED")
        errored = sum(1 for r in group if (r.outcome or "").upper() == "ERROR")
        latencies = [r.latency_ms for r in group]

        versions.append({
            "version": v,
            "runs": n,
            "completed": completed,
            "escalated": escalated,
            "error": errored,
            "success_rate": completed / n,
            "escalation_rate": escalated / n,
            "error_rate": errored / n,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "avg_tokens": sum(r.total_tokens or 0 for r in group) / n,
            "enough_runs": n >= MIN_RUNS_PER_VERSION,
            "first_run": _iso(group[0].started_at),
            "last_run": _iso(group[-1].started_at),
        })

    return {"agent_id": agent_id, "versions": versions,
            "verdict": _verdict(versions)}


def _verdict(versions: List[dict]) -> Optional[dict]:
    """Compare the current version against the one before it.

    Four outcomes, and two of them are refusals. That ratio is intentional.
    """
    if len(versions) < 2:
        return {"direction": "insufficient_data",
                "summary": "Only one config version has run so far — nothing "
                           "to compare against yet."}

    current, previous = versions[0], versions[1]

    if not (current["enough_runs"] and previous["enough_runs"]):
        thin = current if not current["enough_runs"] else previous
        plural = "" if thin["runs"] == 1 else "s"
        return {"direction": "insufficient_data",
                "current": current["version"], "previous": previous["version"],
                "summary": f"v{thin['version']} has only {thin['runs']} run{plural} — "
                           f"need {MIN_RUNS_PER_VERSION} before comparing."}

    delta = current["success_rate"] - previous["success_rate"]
    pct_now = round(current["success_rate"] * 100)
    pct_before = round(previous["success_rate"] * 100)

    if abs(delta) < MEANINGFUL_DELTA:
        direction = "no_clear_change"
        summary = (f"v{current['version']} completes {pct_now}% of runs, "
                   f"v{previous['version']} completed {pct_before}%. Too close "
                   f"to call at {current['runs']} and {previous['runs']} runs.")
    elif delta < 0:
        direction = "worse"
        summary = (f"v{current['version']} completes {pct_now}% of runs. "
                   f"v{previous['version']} completed {pct_before}%. Escalations "
                   f"went from {round(previous['escalation_rate'] * 100)}% to "
                   f"{round(current['escalation_rate'] * 100)}%.")
    else:
        direction = "better"
        summary = (f"v{current['version']} completes {pct_now}% of runs, up "
                   f"from {pct_before}% on v{previous['version']}.")

    return {"direction": direction, "current": current["version"],
            "previous": previous["version"], "delta": round(delta, 3),
            "summary": summary}


def fleet_regressions(db: Session, owner_id: str = None) -> List[dict]:
    """Agents whose newest config version is performing worse than the last.

    The one thing worth interrupting somebody about: a change they approved
    made an agent measurably worse. Everything else can wait to be asked.
    """
    q = db.query(Agent).filter(Agent.is_deleted == False)  # noqa: E712
    if owner_id:
        q = q.filter(Agent.owner_id == owner_id)

    out = []
    for agent in q.all():
        verdict = (version_performance(db, agent.id) or {}).get("verdict") or {}
        if verdict.get("direction") == "worse":
            out.append({"agent_id": agent.id,
                        "name": agent.name or agent.slug,
                        "summary": verdict["summary"],
                        "delta": verdict.get("delta"),
                        "current_version": verdict.get("current"),
                        "previous_version": verdict.get("previous")})

    out.sort(key=lambda x: x.get("delta") or 0)   # worst regression first
    return out


def _iso(dt):
    aware = as_aware(dt)
    return aware.isoformat() if aware else None
