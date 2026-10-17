from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from interaction.sqlite_env import SQLiteEnvironment, bird_execution_equal
from interaction.trajectory import (
    BenchmarkTask,
    Trajectory,
    append_trajectory,
    load_trajectories,
)


def create_database(root: Path, db_id: str = "demo") -> Path:
    folder = root / db_id
    folder.mkdir(parents=True)
    path = folder / f"{db_id}.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, category TEXT)"
        )
        connection.executemany(
            "INSERT INTO items(name, category) VALUES (?, ?)",
            [("Alpha", "x"), ("Alpha", "x"), ("Beta", "y")],
        )
        connection.commit()
    return path


class SQLiteEnvironmentTest(unittest.TestCase):
    def test_row_limit_requires_an_extra_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = create_database(Path(directory))
            for limit in (3, 1_000, 1_001):
                for count in (0, limit - 1, limit, limit + 1):
                    with self.subTest(limit=limit, count=count):
                        environment = SQLiteEnvironment(database, max_rows=limit)
                        result = environment.execute(
                            "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL "
                            f"SELECT n + 1 FROM numbers WHERE n < {count}) "
                            f"SELECT n FROM numbers WHERE n <= {count}"
                        )
                        self.assertEqual(result.observation.status, "ok")
                        self.assertEqual(result.observation.row_count, min(count, limit))
                        self.assertEqual(result.observation.truncated, count > limit)
                        self.assertEqual(bird_execution_equal(result, result), count <= limit)

    def test_execution_is_read_only_and_bird_equality_ignores_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = create_database(Path(directory))
            environment = SQLiteEnvironment(database)
            duplicate = environment.execute(
                "SELECT name AS first_name FROM items WHERE category = 'x'"
            )
            distinct = environment.execute(
                "SELECT DISTINCT name AS other_name FROM items WHERE category = 'x'"
            )
            rejected = environment.execute("DELETE FROM items")

            self.assertTrue(bird_execution_equal(duplicate, distinct))
            self.assertNotEqual(
                duplicate.observation.fingerprints["visible_multiset"],
                distinct.observation.fingerprints["visible_multiset"],
            )
            self.assertEqual(rejected.observation.status, "rejected_non_read_query")
            self.assertEqual(
                environment.execute("SELECT COUNT(*) FROM items").rows,
                ((3,),),
            )


class TrajectoryTest(unittest.TestCase):
    def test_trajectory_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            trajectory = Trajectory.start(
                run_id="round-trip",
                policy="test",
                task=BenchmarkTask(
                    question_id=1,
                    db_id="demo",
                    question="List x names",
                    evidence=None,
                    difficulty="simple",
                ),
                config={"gold_visible_to_policy": False},
            )
            trajectory.finish(
                termination="submitted_executable_sql",
                total_elapsed_ms=1.0,
                final_sql="SELECT name FROM items WHERE category = 'x'",
            )
            trajectory_path = root / "trajectories.jsonl"
            append_trajectory(trajectory_path, trajectory)
            loaded = load_trajectories(trajectory_path)
            self.assertEqual(loaded[0].to_dict(), trajectory.to_dict())


if __name__ == "__main__":
    unittest.main()
