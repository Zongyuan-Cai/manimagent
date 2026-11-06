"""Persistence and restart transitions shared by manager and executor."""

import logging

from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.memory.audit import publish_memory_context
from manimagent.schemas import Answer, Error, Generation, Job, now


def save_job(store: ContentStore, job: Job) -> None:
    job.updated_at = now()
    store.save("jobs", job.job_id, job)


def restore_published_answer(store: ContentStore, job: Job) -> bool:
    if not job.resource_ref or job.resource_ref.type != "generation":
        return False
    generation = store.load("generations", job.resource_ref.id, Generation)
    if not generation.answer_id:
        return False
    store.load("answers", generation.answer_id, Answer)
    if generation.output_mode == "both" and not generation.video_id:
        job.status, job.message = "partial", "文字已保存；动画中断，可用同一回答重新生成视频"
        generation.stages["video"].status = "interrupted"
        generation.stages["video"].error = job.error
        store.save("generations", generation.generation_id, generation)
    else:
        job.status, job.message, job.error = "completed", "结果已保存", None
    job.result = job.resource_ref
    return True


def interrupt(store: ContentStore, job: Job, message: str) -> None:
    job.status, job.message = "interrupted", message
    job.error = Error(code="interrupted", message=message, retryable=True)
    if not restore_published_answer(store, job) and job.resource_ref:
        generation = store.load("generations", job.resource_ref.id, Generation)
        for stage in generation.stages.values():
            if stage.status in {"running", "pending"}:
                stage.status, stage.error = "interrupted", job.error
        generation.updated_at = now()
        store.save("generations", generation.generation_id, generation)
    save_job(store, job)
    try:
        record = store.load_record(job.job_id)
        previous_error = record.get("memory_context_export_error")
        publish_memory_context(store.root, record, status=job.status)
        if previous_error != record.get("memory_context_export_error"):
            store.save("records", job.job_id, record)
    except (OSError, ContentError):
        logging.getLogger(__name__).warning("中断任务的记忆审计文件暂未更新", exc_info=True)
