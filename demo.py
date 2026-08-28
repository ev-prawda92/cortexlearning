"""Runnable demonstration — seeds three agents and prints what the analysis says.

    python demo.py

No API keys, no services. It builds a throwaway SQLite database, writes
synthetic run history, and shows the four verdicts the module can reach.
"""
import random
from datetime import timedelta

from models import make_session, Agent, Run, utcnow
from learning import version_performance, fleet_regressions

# One scenario per verdict the module can reach.
SCENARIOS = [
    # id, name, (version, runs, success, escalation) per version, note
    ("prior-auth", "Prior Auth Agent",
     [(4, 18, 0.94, 0.06), (5, 14, 0.50, 0.43)],
     "someone loosened a threshold and it cost them"),
    ("triage", "Triage Agent",
     [(2, 16, 0.45, 0.45), (3, 16, 0.94, 0.06)],
     "a change that clearly worked"),
    ("billing", "Billing Agent",
     [(1, 20, 0.80, 0.15), (2, 20, 0.75, 0.20)],
     "a real but small difference — not enough to call"),
    ("notify", "Notify Agent",
     [(1, 9, 0.78, 0.22), (2, 2, 1.00, 0.00)],
     "a brand-new version with too little evidence to judge"),
]


def seed(db):
    random.seed(11)
    base = utcnow() - timedelta(days=6)
    for slug, name, versions, _ in SCENARIOS:
        db.add(Agent(id=slug, owner_id="u1", slug=slug, name=name,
                     version=versions[-1][0], status="running"))
        for offset, (v, n, ok, esc) in enumerate(versions):
            start = base + timedelta(days=offset * 3)
            for i in range(n):
                r = random.random()
                outcome = ("COMPLETED" if r < ok
                           else "ESCALATED" if r < ok + esc else "ERROR")
                t = start + timedelta(minutes=i * 20)
                db.add(Run(agent_id=slug, config_version=v, outcome=outcome,
                           model="claude-sonnet-5", total_tokens=1920,
                           started_at=t,
                           finished_at=t + timedelta(milliseconds=2400)))
    db.commit()


def main():
    db = make_session("sqlite:///:memory:")
    seed(db)

    print("=" * 72)
    print("PER-AGENT VERDICTS")
    print("=" * 72)
    for slug, name, _, note in SCENARIOS:
        result = version_performance(db, slug)
        verdict = result["verdict"]
        print(f"\n{name}  ({note})")
        for v in result["versions"]:
            flag = "" if v["enough_runs"] else "   [thin — not conclusion-worthy]"
            print(f"    v{v['version']}  {v['success_rate']*100:5.1f}% complete"
                  f"   {v['escalation_rate']*100:5.1f}% escalated"
                  f"   {v['runs']:3d} runs{flag}")
        print(f"    -> {verdict['direction'].upper()}")
        print(f"       {verdict['summary']}")

    print()
    print("=" * 72)
    print("FLEET VIEW — what is worth interrupting someone about")
    print("=" * 72)
    regressions = fleet_regressions(db, owner_id="u1")
    if not regressions:
        print("\n  Nothing regressed.")
    for r in regressions:
        print(f"\n  {r['name']}  (v{r['previous_version']} -> v{r['current_version']})")
        print(f"    {r['summary']}")
    print(f"\n  {len(regressions)} of {len(SCENARIOS)} agents flagged. "
          f"The rest are fine, or not yet provable — and saying which "
          f"is which is the point.")
    db.close()


if __name__ == "__main__":
    main()
