# Deployment

The live demo is at **https://airline-ops.streamlit.app/**, hosted on Streamlit Community Cloud.

## App settings

| Setting | Value |
| --- | --- |
| Repository | `jameskbb/airline-ops` |
| Branch | `main` |
| Main file | `app.py` |
| Python (Advanced settings) | 3.12 |
| Secrets | None required |

Community Cloud installs from the first dependency file it finds, in the order `uv.lock`, `Pipfile`,
`environment.yml`, `requirements.txt`, `pyproject.toml`. This repository only has `requirements.txt`, which pins the
versions the test suite passes with. `app.py` adds `src/` to `sys.path`, so the `flightops` package needs no install
step. When you change a dependency, update the floor in `pyproject.toml` and the pin in `requirements.txt` together,
and run the tests before pushing.

Python 3.12 matches CI; any version from 3.11 up satisfies `requires-python`. Community Cloud sets the Python version
when an app is created; changing it later means deleting the app and deploying it again with the same URL.

The optional Anthropic narrator stays off unless `FLIGHTOPS_BRIEF_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` are set,
either as environment variables or in the app's Secrets. The public demo runs without them, on the deterministic
brief.

## How updates reach the live app

- **Every push to `main` redeploys.** Community Cloud watches the branch and pulls each new commit within a few
  minutes; a change to `requirements.txt` triggers a reinstall. Nothing else has to run.
- **Data refreshes deploy themselves.** The [refresh workflow](../.github/workflows/refresh-data.yml) runs every
  Monday at 11:17 UTC (and on demand), tests the new data and pushes a `data: refresh BTS on-time data through
  YYYY-MM` commit straight to `main` when BTS has published a new month. That push redeploys the app. Because
  Parquet files are not source code, a running server would otherwise keep serving cached results, so `app.py` calls
  `refresh_if_data_changed()` on each rerun: when the committed data files change it clears `st.cache_data` and
  reopens the DuckDB store.
- **Code changes** go through CI on the push or pull request; merge to `main` to deploy.
- **Reboot** from the app's menu in the Community Cloud workspace if the app is stuck or after changing settings.

## Behavior to expect

- **Sleep.** An app with no traffic for 12 hours goes to sleep. The next visitor sees a button to wake it, and the
  first load takes a little longer while the server starts and warms its caches.
- **Sharing.** Who can view the app is set under the app's Settings, Sharing, in Community Cloud. When it is set to
  private, anonymous visitors are sent to a Streamlit sign-in page; set it to public for an open demo.
- **Concurrency.** All sessions share one DuckDB store per process. Each query runs on its own cursor, which DuckDB
  requires for use across threads; `tests/test_store_threads.py` guards this.
- **Header.** Community Cloud paints Streamlit's fixed 60px header opaque, so `assets/style.css` keeps 80px of top
  padding on the main block to keep page titles visible; `tests/test_style.py` guards this.
