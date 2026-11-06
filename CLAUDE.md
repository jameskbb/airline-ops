# FlightOps Intelligence agent rules

`CLAUDE.md` and `AGENTS.md` are the same document under two names so Claude
Code and Codex follow identical rules. Change both together;
`tests/test_writing_rules.py` fails if they differ.

## Purpose

FlightOps Intelligence (repository `airline-ops`) is a public Streamlit app
built on U.S. DOT BTS Marketing Carrier On-Time Performance data. It turns
monthly flight records into a governed view of network reliability, airport
friction, route health, carrier benchmarking, delay causes and delay
propagation, with deterministic operational signals. Every aggregated number
traces to the query that produced it through the Data Explorer.

Core value: an operations executive opens the landing page and, without
clicking, understands how healthy the network was in the latest reported
period, what changed and where the friction is. Every number comes from one
governed metric layer and reconciles across pages.

## Layout

- `app.py`: Streamlit entry point (`st.navigation`/`st.Page`); adds `src/` to
  `sys.path`.
- `src/flightops/`: `data` (pipeline, store), `metrics`, `analytics`,
  `charts`, `narrative`, `pages`, `ui`, `utils`, `config.py`.
- `scripts/sync_bts.py`: idempotent data refresh CLI.
  `scripts/capture_screenshots.py`: README screenshots.
- `data/`: committed processed Parquet partitions (one per month);
  `data/raw/` is gitignored.
- `docs/deployment.md`: Community Cloud settings and the update flow.

## Install, run, test

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
streamlit run app.py
```

Checks (the same as CI):

```bash
python -m ruff check src tests scripts app.py
python -m pytest -q
python -c "from flightops.data.pipeline import reconcile; print(len(reconcile()), 'months reconciled')"
```

The optional Anthropic narrator needs `pip install -e ".[llm]"`,
`FLIGHTOPS_BRIEF_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`; the default
brief is deterministic and needs no key.

## Constraints

- Stack: Python, Streamlit, DuckDB, Parquet, pandas, Plotly, PyDeck.
- The core experience needs no paid services or API keys.
- Interactions stay near-instant: pre-aggregated tables, `st.cache_data`,
  DuckDB predicate pushdown.
- Honesty: no fake metrics, no implied real-time data, no overstated
  causality, minimum-volume logic on every ranking.
- Processed data must stay small enough to commit, which bounds table grain.
- Keep metric definitions centralized, keep fact tables additive, and add
  tests for denominator and edge-case behavior.
- When a dependency changes, update the floor in `pyproject.toml` and the pin
  in `requirements.txt` together.

## Branch model and landing

- `main` only; there is no prod branch. Contributions arrive as pull requests
  against `main`, and CI runs the checks above.
- Every push to `main` deploys the public demo on Streamlit Community Cloud.
  After a code change lands, the maintainer reboots the app (see
  `docs/deployment.md`).
- The weekly refresh workflow pushes `data: refresh ...` commits to `main`
  itself, so pull before working.
- Commit messages carry no AI attribution trailers.

## Never do

- Never write an em dash anywhere (docs, code, comments, UI text, commit
  messages). Reword with a colon, semicolon, comma, parentheses or a new
  sentence; do not substitute another dash. `tests/test_writing_rules.py`
  enforces it.
- Never commit secrets, `.env` files or downloaded BTS ZIPs.
- Never force-push or rewrite `main`, and never change the repository's
  visibility or settings.
- Never commit `.planning/`, `.claude/settings.local.json` or
  `.claude/worktrees/`.

## UI changes

An interface or data change updates the README images in the same change:
re-shoot with

```bash
uv run --no-project --with-requirements requirements.txt --with playwright==1.62.0 \
  python scripts/capture_screenshots.py
```
