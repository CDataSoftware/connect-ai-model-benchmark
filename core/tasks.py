"""Task registry + rescoring, shared by aggregate.py and export_raw.py.

Both need the same thing: given a saved run JSON, work out which task it belongs to and re-derive
its score with the *current* scoring logic rather than trusting whatever was stored at run time
(so a scorer fix retroactively applies to every completed run without re-spending on the models).

Read tasks rescore from `final_answer`; the action task rescores from `queue_rows`, the snapshot of
REVIEW_QUEUE taken right after the run by core.verifier.
"""
import os

import yaml

from . import scorer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEGACY_TASK = "r1"          # R1 runs predate the task field in the filename format
ACTION_SCORING = "action"


def load_tasks(config_path=None):
    """{task_id: {id, scoring, golden, conditions, ...}} from config/models.yaml."""
    path = config_path or os.path.join(HERE, "config", "models.yaml")
    cfg = yaml.safe_load(open(path, encoding="utf-8"))
    out = {}
    for t in cfg.get("tasks", []):
        t = dict(t)
        gpath = os.path.join(HERE, t["golden_file"])
        t["golden"] = scorer.load_golden(gpath) if os.path.exists(gpath) else []
        out[t["id"]] = t
    return out


def task_of(rec):
    return rec.get("task") or LEGACY_TASK


def is_action(task):
    return bool(task) and task.get("scoring") == ACTION_SCORING


def rescore(rec, tasks):
    """Recompute a run's score with the live scorer. Falls back to the stored score if the run's
    graded artifact is missing (e.g. an errored run)."""
    task = tasks.get(task_of(rec))
    stored = rec.get("score") or {}
    if not task or not task.get("golden"):
        return stored
    mode = task["scoring"]
    if mode == ACTION_SCORING:
        rows = rec.get("queue_rows")
        if rows is None:
            return stored
        s = scorer.score_action(rows, task["golden"])
        s.update(scorer.write_target_score(rec))
        return s
    answer = rec.get("final_answer")
    if not answer:
        return stored
    if mode == "answer_r2":
        return scorer.score_r2(answer, task["golden"])
    return scorer.score(answer, task["golden"])


def outcome(rec, score, task):
    """Task-appropriate coarse outcome label."""
    if is_action(task):
        return scorer.classify_action_outcome(score)
    return scorer.classify_outcome(score, rec.get("hit_turn_cap"))
