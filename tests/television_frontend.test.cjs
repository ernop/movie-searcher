const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('a series deep link brings the requested season and episode into view', async () => {
    const scrolled = [];
    const episode = {scrollIntoView: () => scrolled.push('episode')};
    const season = {querySelector: selector => selector === '.tv-episode[data-episode="3"]' ? episode : null};
    const page = {classList: {add() {}, contains: () => true},
        querySelector: selector => selector === '.tv-season[data-season="2"]' ? season : null};
    const context = {
        URLSearchParams, location: {hash: '#/series/7?season=2&episode=3'},
        escapeHtml: value => String(value), window: {addEventListener() {}},
        CustomEvent: class {},
        document: {getElementById: id => id === 'pageSeries' ? page : {}, dispatchEvent() {}},
        fetch: async () => ({ok: true, json: async () => ({id: 7, title: 'Example', name: 'Example', episodes: []})})
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/television.js'), 'utf8'), context);
    await context.loadSeriesPage(7);
    assert.deepEqual(scrolled, ['episode']);
});
