from evals import narrated


class MemoryClient:
    def __init__(self, state):
        self.state = state
        self.gets = []

    def get(self, path):
        self.gets.append(path)
        state = self.state

        class Response:
            status_code = 200

            def json(self):
                return {"state": state, "usage": {"model_calls": 1}}

        return Response()


def test_narrated_acceptance_records_failed_learning_without_retry(tmp_path):
    client = MemoryClient("failed")
    result = narrated.wait_learning(client, "job_test", tmp_path)
    assert result["state"] == "failed" and result["wait_timed_out"] is False
    assert client.gets == ["/api/v2/memory/learning/job_test"]
    assert (tmp_path / "memory-learning.json").is_file()


def test_narrated_acceptance_stops_waiting_at_thirty_seconds(tmp_path, monkeypatch):
    clock = iter([0, 30])
    monkeypatch.setattr(narrated.time, "monotonic", lambda: next(clock))
    client = MemoryClient("running")
    result = narrated.wait_learning(client, "job_test", tmp_path)
    assert result["state"] == "running" and result["wait_timed_out"] is True
    assert len(client.gets) == 1
