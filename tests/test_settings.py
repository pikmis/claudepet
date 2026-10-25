from clawd.settings import Settings


def test_round_trips_and_tolerates_missing_fields(tmp_path):
    path = tmp_path / "config" / "settings.json"
    settings = Settings(language="en", claude_path="/opt/claude", listen=False, watch=True, llm=False)
    settings.save(path)
    assert Settings.load(path) == settings

    path.write_text('{"language":"ru"}', encoding="utf-8")
    partial = Settings.load(path)
    assert partial.language == "ru"
    assert partial.listen and partial.llm and partial.claude_path is None

    path.write_text("garbage", encoding="utf-8")
    assert Settings.load(path).listen


def test_ignores_values_of_the_wrong_type(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"listen": "yes", "language": "fr", "claude_path": 5, "extra": 1}', encoding="utf-8")
    loaded = Settings.load(path)
    assert loaded.listen is True and loaded.claude_path is None
    assert loaded.language in ("ru", "en")
