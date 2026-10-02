const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

for (const [file, operation] of [
    ['ai-search.js', 'performAiSearch'],
    ['movie-details.js', 'generateReview'],
    ['movie-details.js', 'generateRelatedMovies']
]) {
    test(`${operation} preserves a server error across fragmented SSE chunks`, async () => {
        const messages = [], elements = new Map();
        const detail = 'The AI response was cut short by its output limit; no partial list was saved.';
        const payload = `data: invalid-json\n\ndata: ${JSON.stringify({ type: 'error', detail })}\n\n`;
        const chunks = [payload.slice(0, 35), payload.slice(35)];
        const context = {
            TextDecoder, console: { warn() {}, error() {} },
            document: {
                addEventListener() {},
                getElementById(id) {
                    if (!elements.has(id)) elements.set(id, {
                        value: 'query', textContent: '', innerHTML: '', dataset: {},
                        removeAttribute() {}, querySelector() { return null; }
                    });
                    return elements.get(id);
                }
            },
            alert: message => messages.push(message),
            showStatus: message => messages.push(message),
            fetch: async () => ({ ok: true, body: { getReader: () => ({
                read: async () => chunks.length
                    ? { done: false, value: Buffer.from(chunks.shift()) }
                    : { done: true }
            }) } })
        };
        vm.createContext(context);
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', file), 'utf8'), context);
        await context[operation](42);
        assert.ok(messages.includes(`Error: ${detail}`), JSON.stringify(messages));
        assert.ok(!messages.some(message => message.includes('No result received')));
    });
}

test('AI search renders a complete result without waiting for a broken stream to close', async () => {
    const elements = new Map(), alerts = [], rendered = [];
    let reads = 0;
    const context = {
        TextDecoder, console,
        document: {
            addEventListener() {},
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, {
                    value: 'query', dataset: {}, removeAttribute() {}, querySelector() { return null; }
                });
                return elements.get(id);
            }
        },
        alert: message => alerts.push(message),
        fetch: async () => ({ok: true, body: {getReader: () => ({
            read: async () => {
                if (++reads > 1) throw new Error('Connection reset after result');
                return {done: false, value: Buffer.from('data: {"type":"result","movie_list_id":48}\n\n')};
            },
            cancel: async () => {}
        })}})
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/ai-search.js'), 'utf8'), context);
    context.renderAiResults = data => rendered.push(data.movie_list_id);
    await context.performAiSearch();
    assert.deepEqual(rendered, [48]);
    assert.deepEqual(alerts, []);
    assert.equal(reads, 1);
});

for (const [when, expectRunning] of [['after the server accepted the search', true], ['before the request reached the server', false]]) {
    test(`AI search explains a dropped connection ${when}`, async () => {
        const elements = new Map(), alerts = [];
        let reads = 0;
        const context = {
            TextDecoder, console: { warn() {}, error() {} },
            document: {
                addEventListener() {},
                getElementById(id) {
                    if (!elements.has(id)) elements.set(id, {
                        value: 'query', textContent: '', innerHTML: '', dataset: {},
                        removeAttribute() {}, querySelector() { return null; }, append() {}
                    });
                    return elements.get(id);
                }
            },
            alert: message => alerts.push(message)
        };
        vm.createContext(context);
        // The page's own realm raises the TypeError, as a browser does for a dropped stream.
        const NetworkError = vm.runInContext('TypeError', context);
        context.fetch = async () => {
            if (!expectRunning) throw new NetworkError('NetworkError when attempting to fetch resource.');
            return {ok: true, body: {getReader: () => ({
                read: async () => {
                    if (++reads > 1) throw new NetworkError('Error in input stream');
                    return {done: false, value: Buffer.from('data: {"type":"progress","step":2,"total":4,"message":"Waiting"}\n\n')};
                }
            })}};
        };
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/ai-search.js'), 'utf8'), context);
        await context.performAiSearch();
        assert.equal(alerts.length, 1);
        assert.equal(alerts[0].includes('keeps running on the server'), expectRunning, alerts[0]);
        assert.ok(!alerts[0].includes('Error in input stream'), alerts[0]);
    });
}
