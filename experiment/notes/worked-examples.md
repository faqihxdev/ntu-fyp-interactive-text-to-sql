# Worked examples

These two saved BIRD cases illustrate contrasting recovery outcomes. They come
from the primary 1,534-task study and are available in the demonstrator.

## Clarification recovers: task 780, superhero

Question: "List the powers of Hunter Zolomon."

All five SQL-only attempts filter on `superhero_name` and return zero rows.
The clarification asks which name field identifies Hunter Zolomon. The simulated
answer selects `full_name`; the next SQL query returns four rows and passes.

The SQL model receives the question, schema, rejection feedback and clarification
answer. The evaluator and simulator can access the target SQL; the SQL model
does not receive it directly.

## Retry recovers: task 1342, student_club

Question: "What was the cost for the 'Posters' on 2019/9/4?"

Retry changes the date literal from `2019/9/4` to `2019-09-04`. Its second query
returns one matching row. All five clarification queries retain the original
date literal and return zero rows despite four answers.

This is one of the 17 tasks solved by retry but not clarification. Both scoring
rules agree on every saved attempt in these two cases. Neither case requires
invalid-SQL repair. The examples illustrate behaviour, not its frequency.

## Evidence checks

The individual records were checked against the merged task records and BIRD
metadata on 6 October 2026. Input locations are listed in the
[demonstrator guide](../../demonstrator/README.md).

| Input | SHA-256 |
| --- | --- |
| Merged task records | `f20f519be238de22c7319eb1015af8c72cce55a706e6fd7b3924cb54fac68e18` |
| BIRD metadata | `630272f2b1c44d8cef2c3b246f623355cf0bbc1e832c81061df895530dfc2f06` |
| Task 780 record | `d89cba1de7ba3740b83878ba6c598d9c1ff2d7932b102e9dfe3e5911599b38bf` |
| Task 1342 record | `f37d34264e6a2aeb92ebdf63a59a645cbe3fecfa42badbb32e261a2af86d91ac` |
