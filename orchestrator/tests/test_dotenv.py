import os

from orchestrator.settings import load_dotenv_file, load_settings


def test_dotenv_fills_unset_variables_and_never_overrides_the_shell(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# image settings\n"
        "\n"
        "IMAGE_PROVIDER=codex\n"
        "export MAX_BUDGET_USD='25'\n"
        'CLAUDE_MODEL="from-dotenv"\n'
        "not a setting\n",
        encoding="utf-8",
    )
    for name in ("IMAGE_PROVIDER", "MAX_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_MODEL", "from-shell")

    load_dotenv_file(env)

    assert os.environ["IMAGE_PROVIDER"] == "codex"
    assert os.environ["MAX_BUDGET_USD"] == "25"
    assert os.environ["CLAUDE_MODEL"] == "from-shell"
    settings = load_settings()
    assert settings.image_provider == "codex" and settings.max_budget_usd == 25.0


def test_a_missing_dotenv_is_fine(tmp_path):
    load_dotenv_file(tmp_path / "absent.env")
