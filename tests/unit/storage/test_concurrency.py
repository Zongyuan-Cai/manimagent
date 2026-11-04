from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from manimagent.config import Settings
from manimagent.content_store import new_id
from tests.support import store_for


def test_polling_and_publication_across_store_instances_never_lose_json(tmp_path):
    first, second = (
        store_for(Settings(runs_dir=tmp_path)),
        store_for(Settings(runs_dir=tmp_path)),
    )
    identifier = new_id("job")
    first.save("records", identifier, {"revision": 0})
    barrier = Barrier(2)

    def publish():
        barrier.wait()
        for i in range(1, 301):
            first.save("records", identifier, {"revision": i})

    def poll():
        barrier.wait()
        for _ in range(1500):
            assert 0 <= second.load_record(identifier)["revision"] <= 300

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish), pool.submit(poll)]
        for future in futures:
            future.result()
    assert second.load_record(identifier)["revision"] == 300
