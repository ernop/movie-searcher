"""Model selection and provider completion checks without production startup."""
import ast
import json
import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def ai():
    tree = ast.parse(Path('main.py').read_text())
    names = {'AI_MODELS', 'AI_PRICING', 'JSON_FENCE_PATTERN', 'resolve_ai_model',
             'request_anthropic_message', 'anthropic_response_text', 'parse_ai_response_json', 'estimate_ai_cost'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names or
             isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    namespace = {'json': json, 're': re, 'logger': logging.getLogger(__name__), 'Decimal': Decimal, 'ROUND_HALF_UP': ROUND_HALF_UP}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), namespace)
    return SimpleNamespace(**namespace)


def test_requested_models_and_defaults(ai):
    from core.models import AiSearchRequest, RelatedMoviesRequest, ReviewRequest
    assert ai.resolve_ai_model('anthropic')['model_id'] == 'claude-fable-5-1'
    assert ai.resolve_ai_model('gpt-6-astra')['provider'] == 'openai'
    for request in [AiSearchRequest, RelatedMoviesRequest, ReviewRequest]:
        assert request.model_fields['provider'].default == 'claude-fable-5-1'
    assert ai.estimate_ai_cost('gpt-6-astra', 1000, 1000)[1] == .06
    for path in ['index.html', 'static/js/movie-details.js']:
        source = Path(path).read_text()
        assert '<option value="claude-fable-5-1" selected>' in source
        assert '<option value="gpt-6-astra">' in source


def client_with_message(message):
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_final_message(self): return message
    def stream(**kwargs):
        assert kwargs['max_tokens'] == 32768
        return Stream()
    return SimpleNamespace(messages=SimpleNamespace(stream=stream))


def test_output_limit_is_reported_before_json_parse(ai, caplog):
    message = SimpleNamespace(stop_reason='max_tokens', usage=SimpleNamespace(input_tokens=500, output_tokens=32768))
    with caplog.at_level(logging.INFO), pytest.raises(ValueError, match='cut short.*no partial list'):
        ai.request_anthropic_message(client_with_message(message), 'test', model='claude-fable-5-1')
    assert 'stop_reason=max_tokens' in caplog.text
    assert 'output_tokens=32768' in caplog.text


def test_complete_fenced_json_and_thinking_blocks(ai):
    message = SimpleNamespace(stop_reason='end_turn', usage=None, content=[
        SimpleNamespace(type='thinking', thinking='private reasoning'),
        SimpleNamespace(type='text', text='```json\n{"movies": []}\n```')])
    result = ai.request_anthropic_message(client_with_message(message), 'test', model='claude-fable-5-1')
    assert ai.parse_ai_response_json(ai.anthropic_response_text(result), 'test', 'anthropic') == {'movies': []}


def test_incomplete_json_is_not_silently_salvaged(ai):
    with pytest.raises(ValueError, match='Failed to parse'):
        ai.parse_ai_response_json('```json\n{"movies": [{"comment": "unfinished', 'test', 'anthropic')
