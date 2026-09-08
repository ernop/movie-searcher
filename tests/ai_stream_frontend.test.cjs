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
