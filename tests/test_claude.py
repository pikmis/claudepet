from clawd.claude import phrases


def test_cleans_up_model_output():
    out = "1. «Привет!»\n- Ещё строка\n\n\"Привет!\"\n• Третья\n" + "x" * 80
    assert phrases(out, 40) == ["Привет!", "Ещё строка", "Третья"]
    assert phrases("3 бага починил\n2) Готово", 40) == ["3 бага починил", "Готово"]
