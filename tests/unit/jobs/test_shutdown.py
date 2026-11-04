from manimagent.jobs.manager import JobManager
from manimagent.schemas import Generation, Job, TextGenerationRequest
from tests.support.pipeline import settings_for


def test_shutdown_updates_both_job_and_generation_stages(tmp_path):
    queued = JobManager(settings_for(tmp_path / "queued"))
    job = queued.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "inputs": [{"type": "text", "text": "a=3"}],
                "instruction": "q",
            }
        ),
    )
    queued.stop()
    result = queued.store.load("jobs", job.job_id, Job)
    generation = queued.store.load("generations", job.resource_ref.id, Generation)
    assert result.status == "interrupted" and result.error.code == "interrupted"
    assert all(s.status == "interrupted" for s in generation.stages.values())
