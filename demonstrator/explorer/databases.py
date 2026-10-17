"""Allowlisted, read-only database discovery for custom questions."""

from functools import lru_cache
from pathlib import Path
import sqlite3

from interaction.bird_dev_matched import build_schema_only_context
from interaction.sqlite_env import SQLiteEnvironment


# Schema-based example questions, distinct from benchmark questions.
DATABASE_GUIDES = {
    "superhero": (
        "Superheroes",
        "Heroes, real names, publishers, powers and numerical attributes.",
        [
            "Show the five tallest heroes with their full names and height in centimetres.",
            "Count the heroes for each publisher and sort from most to fewest.",
            "Which ten powers are linked to the most heroes? Include each count.",
        ],
    ),
    "student_club": (
        "Student club",
        "Members, majors, events, attendance, budgets, income and expenses.",
        [
            "List the five events with the most attendance records, including their names and counts.",
            "Show the total recorded expense cost for each budget category.",
            "How many club members are recorded for each major?",
        ],
    ),
    "formula_1": (
        "Formula 1",
        "Drivers, constructors, circuits, races, standings, laps and pit stops.",
        [
            "Count the circuits in each country, starting with the highest count.",
            "List each season year and the number of races recorded for that year.",
            "Show the five races with the most recorded pit stops, including their year.",
        ],
    ),
    "california_schools": (
        "California schools",
        "School locations and types, enrolment, meal eligibility and SAT scores.",
        [
            "Count the school records in each county, from most to fewest.",
            "List the ten schools with the largest grade-12 enrolment in the SAT table.",
            "Show the number of schools for each school type in the meal eligibility table.",
        ],
    ),
    "card_games": (
        "Card games",
        "Card printings, sets, translations, legal formats and rulings.",
        [
            "Count card records by rarity, starting with the most common rarity.",
            "List the ten newest card sets with their release dates.",
            "Count the translated card records in each language.",
        ],
    ),
    "codebase_community": (
        "Code community",
        "Posts, users, tags, comments, votes, badges and edit history.",
        [
            "List the ten users with the highest reputation, with their display names.",
            "Show the ten most frequent badge names and their counts.",
            "List the five posts with the highest view counts, including their titles.",
        ],
    ),
    "debit_card_specializing": (
        "Fuel card transactions",
        "Customers, fuel stations, products, transaction records and monthly consumption.",
        [
            "Count the fuel stations in each country.",
            "Show the number of recorded transactions for each product description.",
            "List each customer segment and its number of customers.",
        ],
    ),
    "european_football_2": (
        "European football",
        "Players, teams, leagues, match scores and dated player and team attributes.",
        [
            "Show the number of recorded matches for each league and season.",
            "List the ten tallest players with their names and recorded heights.",
            "What is the average number of home goals per match for each league?",
        ],
    ),
    "financial": (
        "Banking records",
        "Accounts, clients, cards, loans, transfers and transactions. Some category codes need interpretation.",
        [
            "Show the number of accounts for each recorded statement frequency.",
            "List each loan duration and the average loan amount for that duration.",
            "Count the issued cards by their recorded card type.",
        ],
    ),
    "thrombosis_prediction": (
        "Clinical records",
        "Patient records, examinations and laboratory measurements. Use for dataset exploration, not clinical advice.",
        [
            "How many records are there in each of the Patient, Examination and Laboratory tables?",
            "Show the number of patients for each recorded sex value.",
            "List the earliest and latest recorded examination dates.",
        ],
    ),
    "toxicology": (
        "Molecules",
        "Molecule labels, atoms, elements, bonds and atom connections.",
        [
            "Count the atoms for each chemical element in this dataset.",
            "List the ten molecules with the most atoms, including their atom counts.",
            "Show each recorded bond type and its number of bonds.",
        ],
    ),
}


class DatabaseStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, db_id: str) -> Path:
        if db_id not in DATABASE_GUIDES:
            raise ValueError("Choose a database from the list.")
        path = (self.root / db_id / f"{db_id}.sqlite").resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("This database is unavailable locally.")
        return path

    def catalogue(self) -> list[dict]:
        return [
            {"id": db_id, "label": guide[0], "description": guide[1], "examples": guide[2]}
            for db_id, guide in DATABASE_GUIDES.items()
            if (self.root / db_id / f"{db_id}.sqlite").is_file()
        ]

    @lru_cache(maxsize=11)
    def detail(self, db_id: str) -> dict:
        path = self.path(db_id)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            tables = [
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
        guide = DATABASE_GUIDES[db_id]
        return {
            "id": db_id,
            "label": guide[0],
            "description": guide[1],
            "examples": guide[2],
            "tables": tables,
            "schema": build_schema_only_context(path),
        }

    def execute(self, db_id: str, sql: str) -> dict:
        return (
            SQLiteEnvironment(
                self.path(db_id),
                timeout_seconds=15,
                max_rows=50_000,
                preview_rows=8,
            )
            .execute(sql)
            .observation.to_dict()
        )
