from pathlib import Path


def test_template_response_uses_request_first_signature():
    source = Path('app/main.py').read_text(encoding='utf-8')
    assert 'TEMPLATES.TemplateResponse(\n            request,\n            "index.html"' in source
