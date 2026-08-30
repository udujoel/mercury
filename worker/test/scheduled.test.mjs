/**
 * Tests for the morning schedule in src/index.js (ADR-010).
 *
 * There was no test harness here at all, which is how four separate silent
 * failures reached review in one PR: a rejected `fetch` aborting `scheduled()`,
 * a dispatch that never named the briefing it had just checked, an alarm
 * racing the generation it had itself triggered, and a zero-byte audio object
 * counting as playable. Every one of them is a morning with no briefing and
 * nothing in the log saying so, and none of them is visible by reading the
 * diff.
 *
 * No dependencies on purpose: `node --test test/` runs this anywhere Node is
 * installed, including a machine that has never run `npm install`. Workers
 * only need the R2 binding and `fetch` stubbed to exercise `scheduled()`.
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";

import worker from "../src/index.js";

const TOKEN = "x".repeat(40);

/** An R2 stub. `objects` maps key -> {json, size}. */
function bucket(objects = {}) {
  const body = (key) => {
    const stored = objects[key];
    if (stored === undefined) return null;
    return {
      size: stored.size ?? 1,
      httpEtag: '"etag"',
      body: "body",
      json: async () => {
        if (stored.json === undefined) throw new Error("not json");
        return stored.json;
      },
    };
  };
  return { get: async (key) => body(key), head: async (key) => body(key) };
}

/** A manifest the phone could actually play. */
const PLAYABLE = {
  json: { turns: [{ speaker: "A" }], duration_ms: 1000 },
};

/** ~3MB is a real briefing; anything this side of a kilobyte is not audio. */
const REAL_AUDIO = { size: 3_000_000 };

function envFor(objects) {
  return {
    BRIEFINGS: bucket(objects),
    USER_TIMEZONE: "Europe/Lisbon",
    MERCURY_API_TOKEN: TOKEN,
    GITHUB_DISPATCH_TOKEN: "ghp_test",
    GITHUB_REPO: "udujoel/mercury",
  };
}

/** Records every workflow_dispatch the Worker attempts. */
function recordFetch(impl) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    if (impl) return impl(url, init);
    return { ok: true, status: 204, text: async () => "" };
  };
  return calls;
}

function workflowOf(call) {
  return call.url.split("/workflows/")[1].split("/")[0];
}

const realFetch = globalThis.fetch;
let logs;
const realConsole = { log: console.log, error: console.error };

beforeEach(() => {
  logs = { log: [], error: [] };
  console.log = (...args) => logs.log.push(args.join(" "));
  console.error = (...args) => logs.error.push(args.join(" "));
});

afterEach(() => {
  globalThis.fetch = realFetch;
  console.log = realConsole.log;
  console.error = realConsole.error;
});

// Summer: Lisbon is UTC+1, so these UTC times are 04:40 and 06:40 local.
const GENERATE_CHECKPOINT = Date.parse("2026-08-30T03:40:00Z");
const DEADLINE_CHECKPOINT = Date.parse("2026-08-30T05:08:00Z");
const ALARM_CHECKPOINT = Date.parse("2026-08-30T05:40:00Z");

describe("scheduled()", () => {
  test("a rejected fetch does not escape the handler", async () => {
    recordFetch(() => Promise.reject(new TypeError("network error")));

    // The bug: the rejection propagated out of dispatchWorkflow and aborted
    // scheduled(), so a morning where GitHub was unreachable produced an
    // unhandled rejection and no record of what the Worker had decided.
    await worker.scheduled(
      { scheduledTime: DEADLINE_CHECKPOINT },
      envFor({}),
    );

    assert.ok(
      logs.error.some((line) => line.includes("network error")),
      `expected the rejection to be logged, got ${JSON.stringify(logs.error)}`,
    );
  });

  test("a rejected fetch at the alarm checkpoint is still logged", async () => {
    recordFetch(() => Promise.reject(new TypeError("network error")));
    await worker.scheduled({ scheduledTime: ALARM_CHECKPOINT }, envFor({}));
    assert.ok(logs.error.some((line) => line.includes("briefing-watchdog.yml")));
  });

  test("every generation dispatch names the briefing it checked", async () => {
    const calls = recordFetch();
    await worker.scheduled(
      { scheduledTime: GENERATE_CHECKPOINT },
      envFor({}),
    );

    // Without this the Actions runner derives its own "today". A run that
    // starts after the user's local midnight then generates the runner's day
    // rather than the day this checkpoint found missing — the sixth date bug
    // in a project that has already had five.
    assert.equal(calls.length, 1);
    assert.equal(workflowOf(calls[0]), "briefing.yml");
    assert.equal(calls[0].body.inputs.briefing_id, "2026-08-30");
  });

  test("a forced regeneration also names the briefing", async () => {
    const calls = recordFetch();
    await worker.scheduled(
      { scheduledTime: GENERATE_CHECKPOINT },
      envFor({ "2026-08-30.manifest.json": { json: { turns: [], duration_ms: 0 } } }),
    );
    assert.equal(calls[0].body.inputs.force, "true");
    assert.equal(calls[0].body.inputs.briefing_id, "2026-08-30");
  });

  test("the alarm is never dispatched in the same tick as generation", async () => {
    const calls = recordFetch();
    await worker.scheduled(
      { scheduledTime: DEADLINE_CHECKPOINT },
      envFor({}),
    );

    // The watchdog asks R2 whether the briefing is there. Dispatching it
    // alongside the generation run it is about to wait for guarantees it
    // looks before that run has written anything, and a watchdog that cries
    // wolf every morning is worse than no watchdog.
    assert.deepEqual(calls.map(workflowOf), ["briefing.yml"]);
  });

  test("the alarm checkpoint alarms and does not generate", async () => {
    const calls = recordFetch();
    await worker.scheduled({ scheduledTime: ALARM_CHECKPOINT }, envFor({}));
    assert.deepEqual(calls.map(workflowOf), ["briefing-watchdog.yml"]);
    assert.equal(calls[0].body.inputs.briefing_id, "2026-08-30");
  });

  test("a healthy morning dispatches nothing at all", async () => {
    const calls = recordFetch();
    await worker.scheduled(
      { scheduledTime: ALARM_CHECKPOINT },
      envFor({
        "2026-08-30.manifest.json": PLAYABLE,
        "2026-08-30.m4a": REAL_AUDIO,
      }),
    );
    assert.deepEqual(calls, []);
  });
});

describe("briefingState", () => {
  const status = async (objects) => {
    const response = await worker.fetch(
      new Request("https://w/status", { headers: { "X-Mercury-Token": TOKEN } }),
      envFor(objects),
    );
    return response.json();
  };

  test("a zero-byte audio object is not playable", async () => {
    // `head` returns an object for a zero-byte key, so the old existence
    // check called it ok. The phone gets a valid manifest and no audio, and
    // idempotency then preserves that state for the rest of the day.
    const body = await status({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": { size: 0 },
    });
    assert.equal(body.state, "empty");
  });

  test("a truncated audio object is not playable", async () => {
    const body = await status({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": { size: 44 },
    });
    assert.equal(body.state, "empty");
  });

  test("a real briefing is ok", async () => {
    const body = await status({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": REAL_AUDIO,
    });
    assert.equal(body.state, "ok");
    assert.equal(body.ok, true);
  });

  test("a wav fallback is ok too", async () => {
    const body = await status({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.wav": REAL_AUDIO,
    });
    assert.equal(body.state, "ok");
  });
});

describe("briefing ids", () => {
  const fetchId = async (id) =>
    worker.fetch(
      new Request(`https://w/briefing/${id}`, {
        headers: { "X-Mercury-Token": TOKEN },
      }),
      envFor({}),
    );

  test("a date-shaped impossible date is rejected", async () => {
    // Same hole as briefing_id_from_key in app/synthesis/storage.py: the
    // regex says YYYY-MM-DD and 2026-01-99 passes it.
    for (const id of ["2026-01-99", "2026-02-30", "2026-13-01"]) {
      const response = await fetchId(id);
      assert.equal(response.status, 400, `${id} should be a 400`);
    }
  });

  test("a real date reaches the bucket", async () => {
    const response = await fetchId("2026-02-29"); // 2026 is not a leap year
    assert.equal(response.status, 400);
    const leap = await fetchId("2024-02-29");
    assert.equal(leap.status, 404);
  });
});
