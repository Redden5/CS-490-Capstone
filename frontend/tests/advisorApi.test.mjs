import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { askAdvisor } from '../src/lib/advisorApi.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('advisor data failures produce a service message', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'private path' }, { status: 503 });
  await assert.rejects(askAdvisor('CS 110', [], new AbortController().signal),
    /course information is temporarily unavailable/);
});

test('advisor timeouts are distinguished from connection failures', async () => {
  globalThis.fetch = async () => { throw new DOMException('timeout', 'TimeoutError'); };
  await assert.rejects(askAdvisor('CS 110', [], new AbortController().signal), /took too long/);
});
