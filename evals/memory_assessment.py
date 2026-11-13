"""Evidence-based assessment of retained live memory/visual trajectory artifacts.

Run after any completed rounds: python -m evals.memory_assessment --root PATH.
No model calls or mutations of the tested memory database. Injection, observable
adoption and causal benefit are deliberately reported as different outcomes.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from evals.support import save
from manimagent.memory.projection import model_memory
from manimagent.memory.store import normalized


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def entries(state):
    return (state or {}).get("entries", [])


def indexed(state):
    return {entry["memory_id"]: entry for entry in entries(state)}


def check(name, failures, *, available=True, evidence=None):
    return {
        "check": name,
        "status": "unknown" if not available else "fail" if failures else "pass",
        "failures": failures,
        "evidence": evidence,
    }


def request_payloads(record):
    for call in (record or {}).get("calls", []):
        if call.get("event") != "request":
            continue
        messages = call["request"]["messages"]
        if len(messages) < 2 or not isinstance(messages[1]["content"], str):
            continue
        yield call["role"], json.loads(messages[1]["content"])


def matches(entry, phrase):
    """Bind an oracle phrase through actual value or original evidence, not a merge."""
    return phrase in entry["value"] or any(phrase in e["quote"] for e in entry["evidence"])


def reasoning_entries(state):
    return [
        e
        for e in entries(state)
        if matches(e, "先说明依据，再写计算")
        and (e["field"].startswith("explanation.") or "依据" in e["value"])
    ]


def matches_snapshot_expectation(entry, phrase):
    # R6 is explicitly authored as the same semantic preference. Accept its
    # content as a retrieval hit while keeping its separate ID/support and the
    # fragmentation diagnostic. This is oracle matching, never a storage merge.
    alternatives = [phrase]
    if phrase == "先说明依据，再写计算":
        alternatives.append("先弄明白每一步为什么成立")
    return any(matches(entry, alternative) for alternative in alternatives)


def expected_checks(case, before, after, snapshot, history, actions):
    expected = case.get("expectations", {})
    checks = []
    prior, current = indexed(before), indexed(after)
    frozen_ids = set(indexed(snapshot))
    reasoning = reasoning_entries(after)
    if "reasoning_support" in expected:
        failures = []
        if len(reasoning) != 1:
            failures.append({"expected_one_stable_fact": True, "actual_fragments": len(reasoning)})
        if any(e["independent_sessions"] != expected["reasoning_support"] for e in reasoning):
            failures.append(
                {
                    "expected_support": expected["reasoning_support"],
                    "observed_supports": [e["independent_sessions"] for e in reasoning],
                }
            )
        checks.append(
            check(
                "stable_original_statement_support",
                failures,
                available=after is not None,
                evidence=[
                    {k: e[k] for k in ("memory_id", "field", "value", "independent_sessions")}
                    for e in reasoning
                ],
            )
        )
    if "reasoning_layer" in expected:
        found = any(e["layer"] == expected["reasoning_layer"] for e in reasoning)
        checks.append(
            check(
                "stable_reasoning_expected_layer",
                [] if found else [expected["reasoning_layer"]],
                available=after is not None,
            )
        )
    if expected.get("same_session_does_not_promote"):
        repeated = [
            e
            for e in reasoning_entries(before)
            if any(v["session_id"] == case["session_id"] for v in e["evidence"])
        ]
        bad = [
            e["memory_id"]
            for e in repeated
            if e["memory_id"] not in current
            or current[e["memory_id"]]["independent_sessions"] != e["independent_sessions"]
        ]
        checks.append(
            check(
                "same_session_repetition_does_not_add_support",
                bad,
                available=bool(repeated),
                evidence={
                    "prior_same_session_ids": [e["memory_id"] for e in repeated],
                    "note": "No prior successful observation means repetition was not exercised.",
                },
            )
        )
    if expected.get("snapshot_empty"):
        checks.append(
            check(
                "initial_snapshot_empty",
                ["entries present"] if frozen_ids else [],
                available=snapshot is not None,
            )
        )
    if expected.get("snapshot_absent"):
        checks.append(
            check(
                "snapshot_absent",
                [] if snapshot is None else ["snapshot present"],
                available=bool(history and history[-1].get("record")),
            )
        )
    if expected.get("no_new_memory") or expected.get("store_unchanged_after_generation"):
        checks.append(
            check(
                "no_observation_effect_expected",
                [] if prior == current else ["store changed"],
                available=before is not None and after is not None,
            )
        )
    # Resolve membership through real IDs, preserving multiple differently worded
    # fragments. This never merges observations or alters independent support.
    known = {}
    for item in history:
        for source in (item.get("before"), item.get("after")):
            for entry in entries(source):
                known[entry["memory_id"]] = entry
    for kind in ("snapshot_contains", "snapshot_excludes"):
        for phrase in expected.get(kind, []):
            candidates = {
                identifier
                for identifier, entry in known.items()
                if matches_snapshot_expectation(entry, phrase)
            }
            actual = sorted(frozen_ids & candidates)
            if kind == "snapshot_contains":
                failure = (
                    []
                    if actual
                    else [{"missing_expected_preference": phrase, "known_ids": sorted(candidates)}]
                )
            else:
                failure = [{"unexpected_preference": phrase, "ids": actual}] if actual else []
            checks.append(
                check(
                    kind,
                    failure,
                    available=snapshot is not None,
                    evidence={"phrase": phrase, "ids": actual},
                )
            )
    if expected.get("exclude_scopes"):
        bad = [e for e in entries(snapshot) if e["scope"] in expected["exclude_scopes"]]
        checks.append(check("excluded_scope_absent", bad, available=snapshot is not None))
    new = [e for identifier, e in current.items() if identifier not in prior]
    if expected.get("new_memory_temporary"):
        checks.append(
            check(
                "temporary_observation_created",
                []
                if new and all(e["temporary"] for e in new)
                else [
                    {
                        "new_ids": [e["memory_id"] for e in new],
                        "temporary": [e["temporary"] for e in new],
                    }
                ],
                available=after is not None and before is not None,
            )
        )
    for label, key in (("new_memory_scope", "scope"), ("new_memory_layer", "layer")):
        if label in expected:
            checks.append(
                check(
                    label,
                    []
                    if new and all(e[key] == expected[label] for e in new)
                    else [{"expected": expected[label], "observed": [e[key] for e in new]}],
                    available=after is not None and before is not None,
                )
            )
    if "new_visual_memory_layer" in expected:
        visual = [e for e in new if e["field"].startswith("visual.")]
        failures = (
            []
            if visual
            and all(
                e["layer"] == expected["new_visual_memory_layer"]
                and e["independent_sessions"] == expected["new_visual_memory_support"]
                for e in visual
            )
            else ["visual preference missing or incorrect layer/support"]
        )
        checks.append(
            check("new_visual_preference", failures, available=after is not None, evidence=visual)
        )
    if "visual_memory_layer" in expected:
        visual = [e for e in entries(snapshot) if e["field"].startswith("visual.")]
        checks.append(
            check(
                "confirmed_visual_preference_in_snapshot",
                []
                if visual and all(e["layer"] == expected["visual_memory_layer"] for e in visual)
                else ["missing or wrong layer"],
                available=snapshot is not None,
                evidence=visual,
            )
        )
    for phrase in expected.get("must_not_learn", []):
        bad = [e["memory_id"] for e in new if matches(e, phrase)]
        checks.append(check("must_not_learn", bad, available=after is not None, evidence=phrase))
    if expected.get("all_short_term_expired"):
        checks.append(
            check(
                "short_term_removed_after_clock_advance",
                [e["memory_id"] for e in current.values() if e["layer"] == "short_term"],
                available=after is not None,
            )
        )
    if expected.get("deep_survives_cleanup"):
        last = history[-2].get("after") if len(history) > 1 else None
        deep_ids = {
            e["memory_id"] for e in entries(last) if e["layer"] == "deep" and e["state"] == "active"
        }
        checks.append(
            check(
                "deep_survives_cleanup",
                sorted(deep_ids - set(current)),
                available=bool(deep_ids),
                evidence=sorted(deep_ids),
            )
        )
    if expected.get("expired_entries_removed"):
        cleanup = [a for a in actions if a["action"]["type"] == "cleanup"]
        removed = sum(a.get("response", {}).get("expired", 0) for a in cleanup)
        checks.append(
            check(
                "cleanup_removed_expired_entries",
                [] if removed > 0 else ["no expired removals recorded"],
                available=bool(cleanup),
                evidence={"expired": removed},
            )
        )
    if expected.get("semantic_paraphrase_probe"):
        fragments = [e for e in new if matches(e, "先弄明白每一步为什么成立")]
        checks.append(
            check(
                "semantic_paraphrase_reuses_existing_fact",
                [e["memory_id"] for e in fragments],
                available=after is not None and before is not None,
                evidence={
                    "new_fragments": fragments,
                    "classification": "capability diagnostic; exact-match storage does not promise semantic merging",
                },
            )
        )
    for action in actions:
        kind = action["action"]["type"]
        if kind not in {"confirm", "correct", "delete"} or action.get("status") != "completed":
            continue
        identifier = action.get("memory_id")
        response = action.get("response", {})
        failures = []
        if kind == "delete":
            if identifier in prior or identifier in frozen_ids:
                failures.append("deleted memory still present")
        else:
            identifier = response.get("memory_id")
            entry = prior.get(identifier)
            if entry is None or entry["layer"] != "deep":
                failures.append("management result not persisted as deep")
            if kind == "correct":
                if entry and (
                    entry["value"] != normalized(action["action"]["value"])
                    or entry["independent_sessions"] != 1
                ):
                    failures.append("correction value or support incorrect")
                if action.get("original_memory_id", action.get("memory_id")) in prior:
                    failures.append("old correction target not removed")
        checks.append(
            check(
                "management_effect_" + kind, failures, available=before is not None, evidence=action
            )
        )
    return checks


def assess_round(case, before, after, snapshot, learning, record, history, actions):
    """Assess data contracts from actual before/after snapshots and call payloads.

    history is a list of already-loaded round bundles, including the current one.
    Missing evidence remains unknown; an empty memory store is not proof that
    consolidation succeeded.
    """
    checks = []
    prior, current = indexed(before), indexed(after)
    number = case["round"]
    enabled, learn = case.get("memory_enabled", True), case.get("learn", True)
    state = (learning or {}).get("state")
    expected_state = "completed" if enabled and learn else "disabled"
    checks.append(
        check(
            "background_learning",
            [] if state == expected_state else [state],
            available=learning is not None,
            evidence={"state": state, "error": (learning or {}).get("error")},
        )
    )
    if enabled and learn and history:
        published = history[-1].get("answer") is not None
        error_code = ((learning or {}).get("error") or {}).get("code")
        gate_ok = published or (state == "cancelled" and error_code == "answer_not_published")
        checks.append(
            check(
                "no_learning_without_published_answer",
                [] if gate_ok else ["unpublished answer did not cancel learning"],
                available=learning is not None,
                evidence={"answer_published": published, "learning_state": state},
            )
        )

    source_texts = {
        item["result"]["job_id"]: item["case"]["instruction"]
        for item in history
        if item.get("result", {}).get("job_id")
    }
    ungrounded, unknown_sources, bad_support, bad_layer, task_commands = [], [], [], [], []
    job_id = (history[-1].get("result") or {}).get("job_id") if history else None
    for entry in current.values():
        observations = [e for e in entry["evidence"] if e["kind"] == "observation"]
        for item in observations:
            source = source_texts.get(item["source_id"])
            if source is None:
                unknown_sources.append(item["source_id"])
            elif item["quote"] not in source:
                ungrounded.append({"memory_id": entry["memory_id"], "evidence": item})
            if item["source_id"] == job_id and item["quote"].strip() in {
                "请帮我理解这道题并制作讲解视频。",
                "请制作讲解视频。",
            }:
                task_commands.append(
                    {
                        "memory_id": entry["memory_id"],
                        "field": entry["field"],
                        "value": entry["value"],
                        "quote": item["quote"],
                    }
                )
        # The trajectory has at most 20 turns, matching the retained evidence cap.
        sessions = {
            e["session_id"] for e in entry["evidence"] if e["kind"] in {"observation", "correction"}
        }
        if len(entry["evidence"]) < 20 and len(sessions) != entry["independent_sessions"]:
            bad_support.append(
                {
                    "memory_id": entry["memory_id"],
                    "reported": entry["independent_sessions"],
                    "evidence_sessions": sorted(sessions),
                }
            )
        if entry["temporary"] and entry["layer"] != "short_term":
            bad_layer.append(entry["memory_id"])
        if (
            entry["layer"] == "deep"
            and entry["confirmed_at"] is None
            and entry["independent_sessions"]
            < (after or {}).get("policy", {}).get("promotion_sessions", 3)
        ):
            bad_layer.append(entry["memory_id"])
        if entry["layer"] == "deep" and entry["expires_at"] is not None:
            bad_layer.append(entry["memory_id"])
    checks.append(
        check(
            "observation_quotes_from_student_instruction",
            ungrounded,
            available=after is not None and not unknown_sources,
            evidence={"unknown_source_ids": sorted(set(unknown_sources))},
        )
    )
    checks.append(check("independent_session_support", bad_support, available=after is not None))
    checks.append(
        check("task_command_not_learned_as_preference", task_commands, available=after is not None)
    )
    checks.append(
        check("promotion_and_temporary_layer_invariants", bad_layer, available=after is not None)
    )

    invalid_snapshot = []
    if snapshot:
        at = datetime.fromisoformat(snapshot["captured_at"])
        for item in snapshot["entries"]:
            original = prior.get(item["memory_id"])
            if original is None:
                invalid_snapshot.append(
                    {"memory_id": item["memory_id"], "reason": "not_in_before_state"}
                )
                continue
            reasons = []
            if original["scope"] not in {"global", case["scope"]}:
                reasons.append("wrong_scope")
            if original["state"] != "active":
                reasons.append("inactive")
            if original["temporary"] and original["session_id"] != case["session_id"]:
                reasons.append("temporary_from_other_session")
            if original["expires_at"] and datetime.fromisoformat(original["expires_at"]) <= at:
                reasons.append("expired")
            if any(
                item[key] != original[key]
                for key in ("value", "field", "scope", "layer", "temporary")
            ):
                reasons.append("mutated_after_freeze")
            if reasons:
                invalid_snapshot.append({"memory_id": item["memory_id"], "reasons": reasons})
    checks.append(
        check(
            "snapshot_scope_expiry_and_frozen_before_learning",
            invalid_snapshot,
            available=before is not None and snapshot is not None and enabled,
        )
    )
    payloads = list(request_payloads(record))
    mismatched, used_roles = [], []
    for role, payload in payloads:
        if "user_memory" not in payload and "recent_user_memory" not in payload:
            if snapshot is not None and role in {
                "answer_writer",
                "answer_checker",
                "video_planner",
                "scene_generator",
                "plan_checker",
            }:
                mismatched.append(role + ": missing memory payload")
            continue
        used_roles.append(role)
        projected = model_memory(snapshot, role)
        if (
            payload.get("user_memory") != {"deep": projected["deep"]}
            or payload.get("recent_user_memory") != projected["recent"]
        ):
            mismatched.append(role)
    checks.append(
        check(
            "actual_role_payload_matches_snapshot",
            mismatched,
            available=bool(used_roles or mismatched),
            evidence=sorted(set(used_roles)),
        )
    )
    if not enabled:
        checks.append(
            check(
                "disabled_memory_no_context",
                ["memory present"] if snapshot or used_roles else [],
                available=record is not None,
            )
        )
    if not enabled or not learn or case.get("expectations", {}).get("store_unchanged"):
        checks.append(
            check(
                "read_only_store_unchanged",
                ["entries changed"] if prior != current else [],
                available=before is not None and after is not None,
            )
        )
    action_failures = [a for a in actions if a.get("status") != "completed"]
    if case.get("actions_before"):
        checks.append(
            check(
                "management_actions_completed",
                action_failures,
                available=len(actions) == len(case["actions_before"]),
                evidence=actions,
            )
        )
    checks.extend(expected_checks(case, before, after, snapshot, history, actions))
    return {
        "round": number,
        "case_id": case.get("case_id"),
        "checks": checks,
        "counts": dict(Counter(item["status"] for item in checks)),
        "before_entries": len(prior),
        "after_entries": len(current),
        "snapshot_entries": len(entries(snapshot)),
        "new_entry_ids": sorted(set(current) - set(prior)),
        "removed_entry_ids": sorted(set(prior) - set(current)),
        "roles_with_memory": sorted(set(used_roles)),
    }


def load_round(directory):
    return {
        "directory": directory,
        **{
            key: read(directory / name)
            for key, name in {
                "case": "case.json",
                "result": "result.json",
                "before": "memory-before.json",
                "after": "memory-after.json",
                "snapshot": "memory-snapshot.json",
                "learning": "memory-learning.json",
                "record": "record.json",
                "actions": "actions.json",
                "answer": "answer.json",
                "plan": "plan.json",
                "before_actions": "memory-before-actions.json",
            }.items()
        },
    }


def adaptive_control_chain(bundles):
    """Assess the preregistered R4/R10/R18 repeat after the extraction prompt fix.

    IDs and source evidence, not a semantic merge in this assessor, must establish
    continuity. Original R2/R3 fragmentation failures remain in the main report.
    """
    by_round = {item["case"]["round"]: item for item in bundles}
    control = (4, 10, 18)
    stages, source_ids = [], []
    for support, number in enumerate(control, 1):
        item = by_round.get(number)
        if item is None:
            stages.append({"round": number, "status": "pending", "expected_support": support})
            continue
        source_id = (item.get("result") or {}).get("job_id")
        if source_id:
            source_ids.append(source_id)
        candidates = []
        for entry in reasoning_entries(item["after"]):
            observed_ids = {e["source_id"] for e in entry["evidence"] if e["kind"] == "observation"}
            if set(source_ids) <= observed_ids:
                candidates.append(entry)
        failures = []
        if len(source_ids) != support or len(candidates) != 1:
            failures.append("No unique real entry retains every control observation")
        elif candidates[0]["independent_sessions"] != support:
            failures.append("Independent support differs from the control trajectory")
        expected_layer = "deep" if support == 3 else "short_term"
        if candidates and (
            candidates[0]["layer"] != expected_layer or candidates[0]["confirmed_at"]
        ):
            failures.append("Layer incorrect or manual confirmation confounds automatic promotion")
        stages.append(
            {
                "round": number,
                "expected_support": support,
                "expected_layer": expected_layer,
                **check(
                    "adaptive_exact_repeat",
                    failures,
                    available=item["after"] is not None,
                    evidence=[
                        {
                            key: entry[key]
                            for key in (
                                "memory_id",
                                "field",
                                "value",
                                "independent_sessions",
                                "layer",
                                "confirmed_at",
                                "evidence",
                            )
                        }
                        for entry in candidates
                    ],
                ),
            }
        )
    return {
        "planned_rounds": list(control),
        "stages": stages,
        "rationale": "Repeat the same explicit preference after R2/R3 extraction fragmentation; no storage merge or artificial confirmation.",
    }


def assess_run(root: Path):
    bundles = [
        load_round(path)
        for path in sorted(root.glob("round-*"))
        if (path / "result.json").is_file()
    ]
    rounds = [
        assess_round(
            item["case"],
            item["before"],
            item["after"],
            item["snapshot"],
            item["learning"],
            item["record"],
            bundles[: index + 1],
            item["actions"] or [],
        )
        for index, item in enumerate(bundles)
    ]
    for bundle, assessment in zip(bundles, rounds, strict=True):
        # Concrete excerpts support human evaluation; keyword overlap cannot prove
        # that remembered preferences caused the response or were semantically used.
        plan = bundle.get("plan") or {}
        assessment["behavior_evidence"] = {
            "status": "requires_manual_review",
            "current_instruction": bundle["case"]["instruction"],
            "answer_opening": (bundle.get("answer") or {}).get("markdown", "")[:1500],
            "scene_openings": [
                {
                    "scene_id": scene["scene_id"],
                    "beats": [
                        {
                            key: beat.get(key)
                            for key in ("beat_id", "screen_text", "formulas", "diagram")
                        }
                        for beat in scene["beats"][:2]
                    ],
                }
                for scene in plan.get("scenes", [])
            ],
            "caveat": "Injection is checked separately. Semantics and math/visual meaning need artifact review; no causal claim from coincident default behavior.",
        }
    result = {
        "completed_round_artifacts": len(rounds),
        "checks": dict(Counter(check["status"] for item in rounds for check in item["checks"])),
        "rounds": rounds,
        "adaptive_control_chain": adaptive_control_chain(bundles),
        "causal_personalization_benefit": "unknown: no paired memory-off counterfactual for identical input",
        "scope": "One synthetic student; live extraction and actual call payloads. No population generalization.",
    }
    save(root / "memory-assessment.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    result = assess_run(parser.parse_args().root)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "rounds"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
