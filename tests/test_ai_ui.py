from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_ai_service_is_not_called_until_user_clicks_generate(tmp_path, monkeypatch):
    import app.config as config
    from app.agents.llm_research import ModelDrivenResearchAgent

    calls = []

    def record_run(self, *args, **kwargs):
        calls.append(args)
        raise RuntimeError("UI click reached the AI path")

    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "ui.sqlite3")
    monkeypatch.setattr(config, "SETTINGS_DATABASE_PATH", tmp_path / "settings.sqlite3")
    monkeypatch.setattr(config, "DATA_MODE", "demo")
    monkeypatch.setattr(ModelDrivenResearchAgent, "run", record_run)
    page_path = Path(__file__).parents[1] / "app" / "frontend" / "streamlit_app.py"
    page = AppTest.from_file(str(page_path)).run()
    assert not page.exception and not calls
    next(item for item in page.radio if item.label == "研究方式").set_value("AI 协作研究").run()
    assert not page.exception and not calls
    next(item for item in page.button if item.label == "生成研究简报").click().run()
    assert len(calls) == 1
    assert any("本次研究未完成" in item.value for item in page.error)
