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
             'request_anthropic_message', 'anthropic_response_text', 'parse_ai_response_json', 'estimate_ai_cost',
             'match_library_titles'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names or
             isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    from fuzzywuzzy import fuzz, process
    namespace = {'json': json, 're': re, 'logger': logging.getLogger(__name__), 'Decimal': Decimal, 'ROUND_HALF_UP': ROUND_HALF_UP,
                 'fuzz': fuzz, 'process': process}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), namespace)
    return SimpleNamespace(**namespace)


def test_requested_models_and_defaults(ai):
    from core.models import AiSearchRequest, RelatedMoviesRequest, ReviewRequest
    assert ai.resolve_ai_model('anthropic')['model_id'] == 'claude-sonnet-5-5'
    assert ai.resolve_ai_model('unknown-model')['model_id'] == 'claude-sonnet-5-5'
    assert ai.resolve_ai_model('claude-opus-5-5')['display_name'] == 'Claude Opus 5.5'
    assert ai.resolve_ai_model('gpt-6-astra')['provider'] == 'openai'
    for request in [AiSearchRequest, RelatedMoviesRequest, ReviewRequest]:
        assert request.model_fields['provider'].default == 'claude-sonnet-5-5'
    assert ai.estimate_ai_cost('gpt-6-astra', 1000, 1000)[1] == .06
    assert ai.estimate_ai_cost('claude-sonnet-5-5', 1_000_000, 1_000_000)[1] == 12
    assert ai.estimate_ai_cost('claude-opus-5-5', 1_000_000, 1_000_000)[1] == 24
    assert all(m['model_id'] in ai.AI_PRICING for m in ai.AI_MODELS)
    assert [m['model_id'] for m in ai.AI_MODELS if m['provider'] == 'anthropic'] == ['claude-sonnet-5-5', 'claude-opus-5-5']
    assert ai.resolve_ai_model('claude-opus-4-8')['model_id'] == 'claude-sonnet-5-5'
    for path in ['index.html', 'static/js/movie-details.js']:
        source = Path(path).read_text()
        assert '<option value="claude-sonnet-5-5" selected>' in source
        assert '<option value="claude-opus-5-5">' in source
        assert '<option value="gpt-6-astra">' in source
        offered = set(re.findall(r'<option value="(claude-[^"]+)"', source))
        assert offered == {'claude-sonnet-5-5', 'claude-opus-5-5'}


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
        ai.request_anthropic_message(client_with_message(message), 'test', model='claude-sonnet-5-5')
    assert 'stop_reason=max_tokens' in caplog.text
    assert 'output_tokens=32768' in caplog.text


def test_complete_fenced_json_and_thinking_blocks(ai):
    message = SimpleNamespace(stop_reason='end_turn', usage=None, content=[
        SimpleNamespace(type='thinking', thinking='private reasoning'),
        SimpleNamespace(type='text', text='```json\n{"movies": []}\n```')])
    result = ai.request_anthropic_message(client_with_message(message), 'test', model='claude-sonnet-5-5')
    assert ai.parse_ai_response_json(ai.anthropic_response_text(result), 'test', 'anthropic') == {'movies': []}


def test_incomplete_json_is_not_silently_salvaged(ai):
    with pytest.raises(ValueError, match='Failed to parse'):
        ai.parse_ai_response_json('```json\n{"movies": [{"comment": "unfinished', 'test', 'anthropic')


def test_title_match_must_agree_on_year(ai):
    film = lambda name, year, path='': SimpleNamespace(name=name, year=year, path=path)
    jetee, remake, original = film('La Jetée', 1962), film('Solaris', 2002), film('Solaris', 1972)
    library = {'la jetée': [jetee], 'solaris': [original, remake]}
    # The AI's "Jet Lag" (2002) fuzzy-matches the library's "La Jetée" (score 86) by name only; the year rules it out.
    assert ai.match_library_titles('Jet Lag', 2002, library) == []
    assert ai.match_library_titles('La Jetée', 1962, library) == [jetee]
    assert ai.match_library_titles('Solaris', 1972, library) == [original]
    assert ai.match_library_titles('Solaris', 1990, library) == []  # a third, different Solaris
    library['elle'] = [film('Elle', 2016), undated := film('Elle', None)]
    assert ai.match_library_titles('Elle', 2011, library) == [undated]
    library['rango'] = [rango := film('Rango', 2009, '/movies/incoming/Rango-2011-415ec089/Rango (2009) [1080p]/Rango.mp4')]
    assert ai.match_library_titles('Rango', 2011, library) == [rango]  # mislabeled file, right film
    assert ai.match_library_titles('Jet Lag', None, library) == [jetee]  # no year to check
