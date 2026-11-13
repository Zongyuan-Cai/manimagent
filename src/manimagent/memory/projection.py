"""Stage-specific projection of already compiled effective parameters."""

from __future__ import annotations


def user_context(
    shared: dict,
    *,
    role: str,
    effective_profile: dict | None = None,
    **current,
) -> dict:
    """Keep compiled parameters and shared content before request-specific sections.

    The audit snapshot is unchanged. Ordering allows provider prefix reuse,
    without sorting dynamic feedback ahead of the shared material.
    """
    if {"snapshot", "user_memory", "recent_user_memory"}.intersection(
        shared.keys() | current.keys()
    ):
        raise ValueError("Raw memory context is unsupported; compile an EffectiveProfile first")
    if effective_profile is not None:
        from manimagent.personalization import EffectiveProfile

        profile = EffectiveProfile.model_validate(effective_profile)
        preferences = {}
        if role in {"answer_writer", "video_planner", "scene_generator"}:
            preferences["teaching_spec"] = profile.teaching.model_dump()
        if role in {"video_planner", "scene_generator"}:
            preferences["visual_spec"] = profile.visual.model_dump()
        return {**preferences, **shared, **current}
    return {**shared, **current}
