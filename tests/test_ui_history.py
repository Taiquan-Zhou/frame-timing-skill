import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from frame_timing_agent.ui.history import RunHistoryStore, RunRecord


def _record(root: Path, run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        created_at="2026-07-15T10:00:00+08:00",
        updated_at="2026-07-15T10:00:00+08:00",
        frame_dir=root / "frames",
        artifact_dir=root / "output" / run_id,
        fps=30.0,
        analyzed_count=120,
        estimated_output_count=90,
        output_count=None,
        output_dir=None,
        status="analyzed",
        strategy_name="reconstruction_balanced",
    )


class RunHistoryStoreTest(unittest.TestCase):
    def test_upsert_round_trip_and_update_existing_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunHistoryStore(root / "app-data" / "run_history.json")
            record = _record(root)

            store.upsert(record)
            exported = replace(
                record,
                updated_at="2026-07-15T10:05:00+08:00",
                output_count=88,
                output_dir=record.artifact_dir / "output_frames",
                status="exported",
            )
            store.upsert(exported)

            self.assertEqual(store.list_records(), [exported])
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertEqual(len(payload["runs"]), 1)

    def test_newest_runs_are_listed_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunHistoryStore(root / "run_history.json")
            older = _record(root, "older")
            newer = replace(
                _record(root, "newer"),
                created_at="2026-07-15T11:00:00+08:00",
                updated_at="2026-07-15T11:00:00+08:00",
            )

            store.upsert(older)
            store.upsert(newer)

            self.assertEqual([record.run_id for record in store.list_records()], ["newer", "older"])

    def test_corrupt_history_is_reported_without_overwriting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_history.json"
            path.write_text("not-json", encoding="utf-8")
            store = RunHistoryStore(path)

            with self.assertRaisesRegex(ValueError, "cannot read run history"):
                store.list_records()

            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")

    def test_malformed_record_does_not_hide_valid_records_or_block_upsert(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "run_history.json"
            valid = _record(root)
            malformed = {"run_id": "broken"}
            path.write_text(
                json.dumps({"version": 1, "runs": [malformed, valid.to_dict()]}),
                encoding="utf-8",
            )
            store = RunHistoryStore(path)

            self.assertEqual(store.list_records(), [valid])
            store.upsert(_record(root, "run-2"))

            self.assertEqual({record.run_id for record in store.list_records()}, {"run-1", "run-2"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(malformed, payload["runs"])

    def test_paths_are_persisted_as_absolute_paths(self):
        relative = _record(Path("relative-root"))

        payload = relative.to_dict()

        self.assertTrue(Path(payload["frame_dir"]).is_absolute())
        self.assertTrue(Path(payload["artifact_dir"]).is_absolute())

    def test_delete_removes_only_the_selected_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunHistoryStore(root / "run_history.json")
            first = _record(root, "run-1")
            second = _record(root, "run-2")
            store.upsert(first)
            store.upsert(second)

            store.delete(first.run_id)

            self.assertEqual(store.list_records(), [second])


if __name__ == "__main__":
    unittest.main()
