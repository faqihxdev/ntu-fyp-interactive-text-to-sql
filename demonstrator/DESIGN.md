# SQL interaction explorer: design

## Purpose and flows

The demonstrator explains the primary BIRD comparison through ten
selected cases. These illustrate outcomes rather than form a representative
sample.

- **Saved cases:** show the complete conversation by default, with optional
  step-by-step replay and a side-by-side method comparison.
- **Alternative replies:** let users branch from a saved clarification. The
  branch inherits only the selected prefix, without future saved answers.
- **Custom questions:** offer a database picker, schema-based descriptions,
  table names and three new starter questions. A starter fills the input;
  the user chooses when to send it.

## Visual rules

- Use React, Tailwind 4, Public Sans for text and IBM Plex Mono for SQL.
- Keep the interface dark: canvas `#0b0b0b`, panels `#151515`, raised surfaces
  `#222222`, text `#ececec` and secondary text `#a3a3a3`.
- Use neon accents for meaning: cyan `#66e8ff` for actions, green `#82ffb1`
  for matches, amber `#ffd166` for mismatches and violet `#c6a0ff` for
  clarification. Avoid glow and decorative gradients.
- Separate sections with spacing and surface changes. Use filled buttons;
  reserve outlines for keyboard focus and cases where a boundary is needed.
- Use a centred 1280px workspace, a 256px desktop sidebar, a 24px main gutter
  and 4/8/12/16/24px spacing increments. Controls should be at least 44px high.
- Keep question, method outcomes and conversation in that reading order.
  Place context, aggregate results, scoring explanations and source details
  in disclosures.
- Put three outcome icons beside each case's ID and database. Use Lucide
  icons with accessible method/status labels and an info disclosure for the
  legend. Read outcomes from saved records, not the current replay position.
- Use compact cross, message and tick markers for trajectory steps. Preserve
  descriptions without repeating SQL/Ask text on every marker.
- Clip rounded outer containers around scrollable SQL and dialogs. Keep
  keyboard scrolling and focus visible. Give search fields one outer focus ring.
- On mobile, use a case selector and wrapping controls. Support reduced motion.

## Model calls and information boundaries

Browsing, replay and SQL previews make no model calls. Each button labelled
API triggers at most one model call, including repair and suggested replies.
The server retains the API key and applies the shared spend guard.

Original-target branches use benchmark rejection feedback and the gold-aware
simulator. Changed-intent branches use the user's feedback to continue.
Custom questions have no gold answer or oracle: executable SQL is unverified,
and users judge the result or request a revision.

Both modes reuse the read-only SQLite executor. Saved prompt hashes are
verified, and live logs stay under ignored `.local/`, separate from frozen
benchmark records. Displayed context means supplied prompts and structured
outputs, not hidden model reasoning.

## Verification

See [VERIFICATION.md](VERIFICATION.md) for test coverage and commands. Coverage
includes replay authenticity, prefix isolation, gold-information boundaries,
read-only SQL, user-triggered calls, stale requests, keyboard access and
mobile overflow.
