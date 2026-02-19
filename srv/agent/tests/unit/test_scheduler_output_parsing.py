from app.services.scheduler import _extract_content_from_output


def test_extracts_result_from_json_string() -> None:
    output = '{"result":"Top 3 items:\\n- A\\n- B\\n- C","status":"ok"}'
    extracted = _extract_content_from_output(output)
    assert "Top 3 items" in extracted
    assert "status" not in extracted


def test_repairs_truncated_json_result() -> None:
    output = '{"result":"Market summary: rates are up'
    extracted = _extract_content_from_output(output)
    assert extracted == "Market summary: rates are up"


def test_extracts_json_from_code_fence() -> None:
    output = """```json
{"summary":"All tasks completed","count":4}
```"""
    extracted = _extract_content_from_output(output)
    assert extracted == "All tasks completed"


def test_extracts_result_from_truncated_python_dict_string() -> None:
    output = "{'result': 'Comprehensive Summary of Recent Dredging and Maritime News:\\n\\n1. Recent Dredging Projects:\\n- Brunswick Harbor ... tens of million"
    extracted = _extract_content_from_output(output)
    assert extracted.startswith("Comprehensive Summary of Recent Dredging and Maritime News:")
    assert "{'result'" not in extracted
