from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from interaction.context import ContextBuilder
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


class SharedContextAndTrajectoryTest(unittest.TestCase):
    def test_value_retrieval_cache_is_task_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = create_database(root)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("ALTER TABLE items ADD COLUMN alpha_name TEXT")
                connection.execute("ALTER TABLE items ADD COLUMN beta_name TEXT")
                connection.execute(
                    "UPDATE items SET alpha_name = 'Shared', beta_name = 'Different'"
                )
                connection.execute("UPDATE items SET beta_name = 'Shared' WHERE id = 3")
                connection.commit()
            builder = ContextBuilder(
                retrieval_query_budget=2,
                retrieval_match_limit=1,
            )
            alpha = builder.build(
                BenchmarkTask(
                    question_id=101,
                    db_id="demo",
                    question="Find alpha_name equal to 'Shared'.",
                    evidence=None,
                    difficulty="simple",
                ),
                database,
            )
            beta = builder.build(
                BenchmarkTask(
                    question_id=102,
                    db_id="demo",
                    question="Find beta_name equal to 'Shared'.",
                    evidence=None,
                    difficulty="simple",
                ),
                database,
            )
            self.assertGreater(alpha.retrieval_queries, 0)
            self.assertGreater(beta.retrieval_queries, 0)
            self.assertEqual(alpha.retrieved_values[0].column, "alpha_name")
            self.assertEqual(beta.retrieved_values[0].column, "beta_name")

            alpha_cached = builder.build(
                BenchmarkTask(
                    question_id=101,
                    db_id="demo",
                    question="Find alpha_name equal to 'Shared'.",
                    evidence=None,
                    difficulty="simple",
                ),
                database,
            )
            self.assertTrue(alpha_cached.retrieval_cache_hit)
            self.assertEqual(alpha_cached.retrieval_queries, 0)
            self.assertEqual(alpha_cached.retrieved_values, alpha.retrieved_values)

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
