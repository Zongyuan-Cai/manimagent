"""Stable semantic context derived from, without changing, an audit snapshot."""

from __future__ import annotations


def model_memory(snapshot: dict | None, role: str) -> dict[str, list[dict]]:
    """Project already-selected winners; never summarize, merge or re-rank facts.

    The snapshot owns validity and conflict resolution. IDs, evidence, observation
    timestamps and session bookkeeping belong only to that audit record. Roles
    filter known irrelevant fields conservatively; custom fields remain intact.
    """
    result: dict[str, list[dict]] = {"deep": [], "recent": []}
    if snapshot is None or role in {"material_reader", "memory_extractor"}:
        return result
    for entry in snapshot.get("entries", []):
        field = entry["field"]
        if role in {"answer_writer", "answer_checker"} and field.startswith("visual."):
            continue
        item = {key: entry[key] for key in ("field", "value", "scope")}
        if entry["layer"] == "deep":
            result["deep"].append(item)
        else:
            # Keep the difference between a current-session exception and a
            # recent, unconfirmed preference, without injecting changing dates.
            item["temporary"] = entry.get("temporary", False)
            result["recent"].append(item)
    for entries in result.values():
        entries.sort(key=lambda item: (item["scope"], item["field"], item["value"]))
    return result


def user_context(shared: dict, *, role: str, snapshot: dict | None = None, **current) -> dict:
    """Keep stable memory and shared content before request-specific sections.

    The audit snapshot is unchanged. Ordering allows provider prefix reuse,
    without sorting dynamic feedback ahead of the shared material.
    """
    if snapshot is None:
        return {**shared, **current}
    memory = model_memory(snapshot, role)
    return {
        "user_memory": {"deep": memory["deep"]},
        **shared,
        "recent_user_memory": memory["recent"],
        **current,
    }
