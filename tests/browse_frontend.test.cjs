const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
    const requests = [], renders = [], statuses = [];
    const grid = { children: [] };
    let params = {};
    const context = {
        URLSearchParams, AbortController, console,
        document: { getElementById: () => grid },
        getRouteParams: () => params,
        updateRouteParams: value => { params = value; },
        showStatus: message => statuses.push(message),
        fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve }))
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/explore.js'), 'utf8'), context);
    context.renderLetterNav = context.renderDecadeNav = context.renderYearFilter = context.renderPagination = () => {};
    context.renderMovieGrid = movies => { renders.push(movies[0]?.name); grid.children = movies; };
    const load = letter => context.fetchExploreMovies(1, 'all', letter, null, null, 'all');
    const finish = (index, name) => requests[index].resolve({ ok: true, json: async () => ({ movies: [{ name }] }) });
    return { context, requests, renders, statuses, load, finish };
}

test('latest request wins even if cancellation does not stop the older response', async () => {
    const s = setup();
    const a = s.load('A'), b = s.load('B');
    assert.equal(s.requests[0].options.signal.aborted, true);
    s.finish(1, 'B'); await b;
    s.finish(0, 'A'); await a;
    assert.deepEqual(s.renders, ['B']);
    assert.equal(s.context.getCurrentExploreFilters().letter, 'B');
});

test('repeat route callbacks share the pending request', async () => {
    const s = setup();
    const first = s.load('A');
    await s.load('A');
    assert.equal(s.requests.length, 1);
    s.finish(0, 'A'); await first;
    await s.load('A');
    assert.equal(s.requests.length, 1);
});

test('a failed request is not cached as a successful page', async () => {
    const s = setup();
    const first = s.load('A');
    s.requests[0].resolve({ ok: false, json: async () => ({ detail: 'failure' }) }); await first;
    const retry = s.load('A');
    assert.equal(s.requests.length, 2);
    s.finish(1, 'A'); await retry;
    assert.deepEqual(s.renders, ['A']);
    assert.equal(s.statuses.length, 1);
});

test('returning to a cached page invalidates an intervening request', async () => {
    const s = setup();
    const a = s.load('A'); s.finish(0, 'A'); await a;
    const b = s.load('B');
    await s.load('A');
    s.finish(1, 'B'); await b;
    assert.deepEqual(s.renders, ['A']);
});

test('rapid changes combine against the URL, not old rendered controls', async () => {
    const s = setup();
    const a = s.load('A');
    const current = s.context.getCurrentExploreFilters();
    const b = s.context.fetchExploreMovies(1, current.filterType, current.letter, 1980, null, current.language);
    assert.match(s.requests[1].url, /letter=A/);
    assert.match(s.requests[1].url, /decade=1980/);
    s.finish(1, 'A in 1980s'); await b;
    s.finish(0, 'A'); await a;
    assert.deepEqual(s.renders, ['A in 1980s']);
});

test('pending decade navigation preserves the rendered year when returning to its cached page', async () => {
    const s = setup();
    let yearChip;
    s.context.renderYearFilter = (counts, year) => { yearChip = year; };
    s.context.clearYearFilterUI = () => { yearChip = null; };
    s.context.clearDecadeFilter = () => {};
    const first = s.context.fetchExploreMovies(1, 'all', null, null, 1980, 'all');
    s.finish(0, '1980'); await first;
    s.context.jumpToDecade(1990);
    await s.context.fetchExploreMovies(1, 'all', null, null, 1980, 'all');
    assert.equal(yearChip, 1980);
    assert.equal(s.requests[1].options.signal.aborted, true);
    s.finish(1, '1990s');
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(s.renders, ['1980']);
});
