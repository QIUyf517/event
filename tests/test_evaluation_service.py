from datetime import date

from app.evaluation.service import evaluate_questions, save_evaluation


def test_evaluation_produces_auditable_summary(tmp_path):
    result = evaluate_questions(tmp_path / "evaluation.sqlite3", date(2026, 9, 30),
        ["分析当前资金信号", "预测明天涨跌"])
    assert result["total"] == 2
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert result["pass_rate"] == 1
    assert all("duration_ms" in item and "failures" in item for item in result["items"])
    output = save_evaluation(result, tmp_path / "latest.json")
    assert output.is_file()
    assert "预测明天涨跌" in output.read_text(encoding="utf-8")
