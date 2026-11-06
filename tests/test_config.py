import pytest

from bot.config import Config


def test_config_resolves_bothost_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "test-token-not-used")
    monkeypatch.setenv("MASTER_IDS", "464396208")
    monkeypatch.setenv("CAMP_CHAT_ID", "-1003338522867")
    monkeypatch.setenv("CAMP_THREAD_ID", "24")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", "camp.sqlite3")
    c = Config.from_env()
    assert c.masters == {464396208}
    assert c.thread_id == 24
    assert c.db_path == str(tmp_path / "camp.sqlite3")


def test_config_fails_with_missing_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        Config.from_env()
