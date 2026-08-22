from __future__ import annotations

import unittest

from interaction.make_mini_interact_split import choose_pilot


def row(index: int) -> dict:
    return {
        "instance_id": "alien_1" if index == 0 else f"db_{index}",
        "selected_database": f"database_{index % 12}",
        "high_level": bool(index % 2),
        "user_query_ambiguity": {
            "critical_ambiguity": [{}] * (1 + index % 4),
        },
        "knowledge_ambiguity": [{}] * (index % 3),
    }


class MiniInteractSplitTest(unittest.TestCase):
    def test_split_is_deterministic_and_preserves_forced_smoke(self) -> None:
        rows = [row(index) for index in range(30)]

        first = choose_pilot(rows, pilot_size=10, seed=20260820)
        second = choose_pilot(list(reversed(rows)), pilot_size=10, seed=20260820)

        self.assertEqual(first, second)
        self.assertEqual(first[0], "alien_1")
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(first), 10)


if __name__ == "__main__":
    unittest.main()
