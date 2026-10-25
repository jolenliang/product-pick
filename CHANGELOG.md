# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/), versioning follows [SemVer](https://semver.org/).

## [1.1.0] — 2026-10-25

Three fixes after the first real run (sleeping bag selection), all driven by what actually went wrong in practice.

### Added

- **Weight confirmation gate** (Step 2.5). Computing initial weights no longer means weights are settled. New four-step gate: anchor-annotated display → three health checks → structured confirmation (A/B/C/D) → lock statement. Only an explicit "A" passes; silence is never treated as consent.
- **Three health checks**, of which the third matters most:
  - Is the top weight too high? (ROC gives 45.7% at n=5 — most people are surprised)
  - Has the bottom weight become meaningless? (<5% can't flip a ranking alone, only dilutes the top)
  - **Did a gate sneak into the weighted attributes?** Once "could this hurt me" becomes a 3% deduction, the matrix will output "warmth rating 3°C short but ¥300 cheaper — better value." Numerically correct, practically dangerous.
- **Brand-first candidate discovery** (Step 1.5b, rewritten as three stages): scan 1–3 authoritative rankings (last 1–2 years) → extract *brands only* → open each brand's full product line → then pick models. New full spec in `references/search-protocol.md` § "二之二".
  - Authority judged on five criteria; the fastest filter is the fifth: **does the ranking say what it does *not* recommend?** A list that only praises is an ad slot.
  - Deliberately does *not* record product rankings from those lists — a ranking is someone else's weights, and showing it pollutes the user's Step 2.5 ordering.
  - Country coverage is a hard requirement: Chinese and English sources in parallel, either side at zero fails the stage.
- **Score contribution breakdown** (`pick.py` § 3.1). Each attribute's contribution = normalized value × weight × 100, summing horizontally to the total. Plus a "max possible" row.

### Fixed

- **Scale mismatch in the sleeping bag run.** Tianshi's packing volume had been derived from its stuff-sack dimensions (12.6 L) and compared against Black Ice's measured compressed volume (6.0 L) — two different definitions. This invented a 2.76-point gap and nearly turned "functionally equivalent" into "Black Ice wins." After correcting: exact tie at 72.81.
- **Candidates with missing values vanished silently.** They were mentioned only in a trailing note, so the score table simply had one fewer row. Now the table itself states "excluded — failed hard gate" / "excluded — incomplete data" directly beneath the ranking.
- Single-attribute and flat-attribute edge cases no longer emit duplicate notices.

### Why this matters

The scale-mismatch bug is the one worth remembering: **a normalization matrix has zero immunity to a definition mismatch.** It quietly converts the error into a plausible-looking score difference. When two scores are close, suspect the data before suspecting the weights.

## [1.0.0] — 2026-10-25

Initial release.

- 13-step workflow built on PrOACT (*Smart Choices*), ROC weights (Barron & Barrett 1996), Pareto frontiers, and tripwires (*Decisive*).
- 9 reference documents covering elicitation, search protocol, attribute design, weighting, sourcing, scoring, and category axes.
- `pick.py`: pure-stdlib scoring engine with closed-form sensitivity analysis, worst-case data-error testing, and dual frontiers.
- Price is displayed but never scored — grounded in the even-swap path-dependency findings (Hämäläinen et al. 2015).
