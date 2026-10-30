"""A data refresh pulled into a running server must not leave stale cached results."""

from __future__ import annotations


def test_caches_drop_when_committed_data_changes(monkeypatch):
    from flightops.ui import data

    cleared = []
    stamps = iter(["a", "a", "b", "b"])
    monkeypatch.setattr(data, "data_stamp", lambda: next(stamps))
    monkeypatch.setattr(data.st.cache_data, "clear", lambda: cleared.append("cache_data"))
    monkeypatch.setattr(data.store, "clear", lambda: cleared.append("store"))
    data._seen_stamp().clear()

    assert data.refresh_if_data_changed() is False  # first sight records the stamp
    assert data.refresh_if_data_changed() is False
    assert data.refresh_if_data_changed() is True
    assert cleared == ["cache_data", "store"]
    assert data.refresh_if_data_changed() is False
