from app.agents.verifier import verify_generated_inferences

def test_verifier_rejects_new_numbers_and_missing_citations():
    evidence = {"value": "2.8%", "documents": [{"citation": "D1"}]}
    accepted, warnings = verify_generated_inferences(["份额变化为2.8% [D1]", "涨幅达到9% [D1]", "没有引用"], evidence, {"D1"})
    assert accepted == ["份额变化为2.8% [D1]"]
    assert len(warnings) == 2
