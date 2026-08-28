# Did the last config change help?

Agent platforms are good at telling you *what happened* — traces, token spend,
latency percentiles. They are mostly silent on the question an operator asks
right after shipping a change: **was that a good idea?**

This is a small, self-contained extract of that answer, pulled out of a larger
agent-operations platform so the idea can be read on its own.

```
Prior Auth Agent
    v5   57.1% complete   42.9% escalated   14 runs
    v4   88.9% complete   11.1% escalated   18 runs
    -> WORSE
       v5 completes 57% of runs. v4 completed 89%.
       Escalations went from 11% to 43%.
```

No new instrumentation produced that. It reads run history that already exists.

---

## Run it

```bash
pip install -r requirements.txt
python demo.py      # four agents, four different verdicts
pytest -q           # 10 tests
```

No API keys, no services, no external calls. `demo.py` builds an in-memory
SQLite database and prints what the analysis says about synthetic history.

---

## The idea

Answering "did that change help?" needs three things. Most tooling has the
first two and stops.

1. **Config changes produce discrete, numbered versions.** A change that
   mutates a running agent in place is unmeasurable after the fact.
2. **Every run records the version that produced it.** One integer column.
   This is the whole trick, and it has to be there from the beginning —
   you cannot backfill which config was live three weeks ago.
3. **Something compares outcomes across versions and says so plainly.**

That third piece is `learning.py`. It writes nothing.

---

## The interesting part is where it refuses to answer

At the volumes a real agent produces in its first weeks, most differences are
noise. A tool that calls a three-point swing across four runs a *regression*
gets ignored inside a month — and then the honest signal it eventually finds
gets ignored too, because the operator has already learned not to trust it.

So there are four verdicts and two of them are refusals:

| Verdict | When |
|---|---|
| `worse` / `better` | Both versions have ≥5 runs **and** the gap exceeds 15 points |
| `no_clear_change` | Both versions have enough runs, but the gap is inside the noise floor |
| `insufficient_data` | Only one version has run, or either side is too thin to compare |

Versions below the run threshold are still *reported* — you can see the
numbers — they just aren't allowed to support a conclusion. In the demo,
Notify Agent's v2 is at 100% across 2 runs and the module still says
"need 5 before comparing," which is the correct and unsatisfying answer.

### On the thresholds

`MIN_RUNS_PER_VERSION = 5` and `MEANINGFUL_DELTA = 0.15` are blunt instruments,
deliberately. A Wilson lower bound on each version's completion rate is the
better tool and the obvious next step — it penalises thin evidence continuously
instead of via a cliff.

It isn't here yet because it isn't yet earned. A calculated-looking confidence
score is worse than a stated threshold until someone has checked that 0.8
actually means 80%, and checking that requires held-out data this hasn't got.
A number nobody has validated is just a fixed threshold wearing a costume.

---

## Files

```
models.py           Minimal Agent and Run tables — the substrate, ~100 lines
learning.py         The analysis. version_performance() and fleet_regressions()
demo.py             Seeded walkthrough of all four verdicts
test_learning.py    10 tests; the refusal cases get the most coverage
```

`fleet_regressions()` answers the operational version of the question: across
every agent, which ones did a human make measurably worse? That is the only
result worth interrupting somebody about. Everything else can wait to be asked.

---

## What this is not

This is an extract, not a product. It has no API, no UI, and no ingestion — in
the system it came from, those exist and this module is what sits underneath
the answer. It is here because the reasoning is portable and the surrounding
application is not.

The escalation signal it reads is also only as good as the agent's ability to
escalate. If an agent has no way to say "I can't do this confidently," every
run completes and `success_rate` degrades into a crash-rate. Getting that
plumbing right matters more than anything in this file.

## License

MIT
