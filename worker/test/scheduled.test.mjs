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

/**
 * An R2 stub. `objects` maps key -> {json, size, etag}.
 *
 * `onlyIf` is modelled the way R2 actually behaves, because that is the whole
 * mechanism the conditional-GET path relies on: R2 evaluates the conditional
 * request headers itself, and when the precondition fails it returns an
 * R2Object with NO `body` property rather than null. `isNotModified` in the
 * Worker tests exactly that, so a stub that always returned a body would make
 * the 304 path untestable and quietly green.
 */
function bucket(objects = {}) {
  const body = (key, options) => {
    const stored = objects[key];
    if (stored === undefined) return null;
    const etag = stored.etag ?? '"etag"';
    const object = {
      size: stored.size ?? 1,
      httpEtag: etag,
      json: async () => {
        if (stored.json === undefined) throw new Error("not json");
        return stored.json;
      },
    };
    const ifNoneMatch = options?.onlyIf?.get?.("if-none-match");
    if (ifNoneMatch && ifNoneMatch === etag) return object; // precondition failed
    return { ...object, body: "body" };
  };
  return {
    get: async (key, options) => body(key, options),
    head: async (key) => body(key),
    // Paginated the way R2 is, because the Worker pages through it and a
    // stub that returned everything at once would leave that loop untested
    // and quietly green — the same trap the `onlyIf` note above describes.
    list: async ({ cursor, limit = 1000 } = {}) => {
      const keys = Object.keys(objects).sort();
      const start = cursor ? keys.indexOf(cursor) : 0;
      const page = keys.slice(start, start + limit);
      const next = start + limit;
      return {
        objects: page.map((key) => ({ key })),
        truncated: next < keys.length,
        cursor: next < keys.length ? keys[next] : undefined,
      };
    },
  };
}

/** A manifest the phone could actually play. */
const PLAYABLE = {
  json: { turns: [{ speaker: "A" }], duration_ms: 1000 },
};

/** ~3MB is a real briefing; anything this side of a kilobyte is not audio. */
const REAL_AUDIO = { size: 3_000_000 };

/** The browse surface: newsletters that arrived, stories that clustered. */
const DIGEST = {
  json: {
    briefing_id: "2026-08-30",
    schema_version: 1,
    newsletters: [{ id: "nl_1", source_name: "Stratechery" }],
    stories: [{ id: "st_c3", covered_in_audio: true, turn_index: 4 }],
  },
};

describe("GET /briefings", () => {
  const listing = {
    "2026-08-28.manifest.json": PLAYABLE,
    "2026-08-30.manifest.json": PLAYABLE,
    "2026-08-29.manifest.json": PLAYABLE,
  };

  test("returns every briefing id, newest first", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings", { headers: { "x-mercury-token": TOKEN } }),
      envFor(listing),
    );

    assert.equal(res.status, 200);
    const body = await res.json();
    // Ids are ISO dates, so lexicographic ordering IS chronological.
    assert.deepEqual(body.briefings, ["2026-08-30", "2026-08-29", "2026-08-28"]);
    assert.equal(body.count, 3);
  });

  test("lists manifests, not audio or digests", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings", { headers: { "x-mercury-token": TOKEN } }),
      envFor({
        "2026-08-30.manifest.json": PLAYABLE,
        "2026-08-30.m4a": REAL_AUDIO,
        "2026-08-30.digest.json": DIGEST,
      }),
    );

    // One briefing, not three. Keying off the audio or the digest would
    // count the same morning several times, and keying off the digest alone
    // would hide every briefing generated before the digest existed.
    assert.deepEqual((await res.json()).briefings, ["2026-08-30"]);
  });

  test("pages through a truncated listing rather than trusting the first page", async () => {
    const many = {};
    for (let d = 1; d <= 30; d += 1) {
      const day = String(d).padStart(2, "0");
      many[`2026-08-${day}.manifest.json`] = PLAYABLE;
    }
    const env = envFor(many);
    // Force truncation at a size the real bucket would not hit today, so
    // the loop is exercised rather than skipped because 30 < 1000.
    const inner = env.BRIEFINGS.list;
    env.BRIEFINGS.list = (opts) => inner({ ...opts, limit: 7 });

    const res = await worker.fetch(
      new Request("https://x/briefings", { headers: { "x-mercury-token": TOKEN } }),
      env,
    );

    assert.equal((await res.json()).count, 30);
  });

  test("skips keys that do not parse as a briefing id", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings", { headers: { "x-mercury-token": TOKEN } }),
      envFor({
        "2026-08-30.manifest.json": PLAYABLE,
        "not-a-date.manifest.json": PLAYABLE,
      }),
    );

    // A key that is not a briefing id would be handed straight back to a
    // client that puts it in a URL.
    assert.deepEqual((await res.json()).briefings, ["2026-08-30"]);
  });

  test("needs the token, like everything else that is about the user", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings"),
      envFor(listing),
    );
    assert.equal(res.status, 401);
  });
});

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
// `/status` asks about TODAY, derived from the wall clock in the configured
// zone — so the objects it looks for have to be keyed to today as well. These
// were written with "2026-08-30" hardcoded and passed for exactly one day;
// every run after that reported `missing` and four assertions were measuring
// nothing. A test that expires silently is worse than no test, because the
// suite stays green until somebody reads the count.
//
// Module scope rather than inside one `describe`, because a second copy in
// the digest suite is a second thing to forget to re-key.
const TODAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Lisbon",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());

/** Re-key fixture objects from the placeholder day onto today's. */
const forToday = (objects) =>
  Object.fromEntries(
    Object.entries(objects).map(([key, value]) => [
      key.replace(/^\d{4}-\d{2}-\d{2}/, TODAY),
      value,
    ]),
  );

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

  test("the fixtures are keyed to the day /status actually asks about", async () => {
    // Guard the premise. Without it the four tests below can all quietly go
    // back to reporting `missing` — a valid-looking answer — the moment a
    // hardcoded date creeps back in.
    const body = await status(forToday({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": REAL_AUDIO,
    }));
    assert.equal(body.briefing_id, TODAY);
    assert.notEqual(body.state, "missing");
  });

  test("a zero-byte audio object is not playable", async () => {
    // `head` returns an object for a zero-byte key, so the old existence
    // check called it ok. The phone gets a valid manifest and no audio, and
    // idempotency then preserves that state for the rest of the day.
    const body = await status(forToday({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": { size: 0 },
    }));
    assert.equal(body.state, "empty");
  });

  test("a truncated audio object is not playable", async () => {
    const body = await status(forToday({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": { size: 44 },
    }));
    assert.equal(body.state, "empty");
  });

  test("a real briefing is ok", async () => {
    const body = await status(forToday({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.m4a": REAL_AUDIO,
    }));
    assert.equal(body.state, "ok");
    assert.equal(body.ok, true);
  });

  test("a wav fallback is ok too", async () => {
    const body = await status(forToday({
      "2026-08-30.manifest.json": PLAYABLE,
      "2026-08-30.wav": REAL_AUDIO,
    }));
    assert.equal(body.state, "ok");
  });
});

describe("conditional GET", () => {
  // `private, no-cache` tells the phone to revalidate on every open, which is
  // only cheap if revalidation can answer 304. The iOS client caches the
  // prefetched audio and sends If-None-Match precisely because `--force`
  // regeneration reuses the same briefing id, so freshness alone proves
  // nothing. Without a 304 the phone re-downloads ~3MB every morning and the
  // background prefetch (PRD F-17) buys nothing.
  const objects = {
    "2026-08-30.manifest.json": { ...PLAYABLE, etag: '"m-1"' },
    "2026-08-30.m4a": { ...REAL_AUDIO, etag: '"a-1"' },
    "2026-08-30.digest.json": { ...DIGEST, etag: '"d-1"' },
  };

  const request = (path, ifNoneMatch) =>
    worker.fetch(
      new Request(`https://w${path}`, {
        headers: ifNoneMatch
          ? { "X-Mercury-Token": TOKEN, "If-None-Match": ifNoneMatch }
          : { "X-Mercury-Token": TOKEN },
      }),
      envFor(objects),
    );

  for (const [what, path, etag] of [
    ["manifest", "/briefing/2026-08-30", '"m-1"'],
    ["audio", "/briefing/2026-08-30/audio", '"a-1"'],
    // The digest shares serveJsonObject with the manifest, which is exactly
    // why it is proven here rather than assumed: one shared helper means one
    // place to get `cache-control` or the 304 wrong, and the response it
    // would be got wrong on is a list of who mails the user.
    ["digest", "/briefing/2026-08-30/digest", '"d-1"'],
  ]) {
    test(`a matching If-None-Match on the ${what} is a bodyless 304`, async () => {
      const response = await request(path, etag);
      assert.equal(response.status, 304);
      assert.equal(response.headers.get("etag"), etag);
      assert.equal(response.body, null);
      assert.equal(await response.text(), "");
    });

    test(`a stale If-None-Match on the ${what} returns the body`, async () => {
      const response = await request(path, '"stale"');
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("etag"), etag);
      assert.ok((await response.text()).length > 0);
    });

    test(`no If-None-Match on the ${what} returns the body`, async () => {
      const response = await request(path);
      assert.equal(response.status, 200);
      assert.ok((await response.text()).length > 0);
    });
  }
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

describe("the digest route", () => {
  const get = (id, headers = {}) =>
    worker.fetch(
      new Request(`https://w/briefing/${id}/digest`, { headers }),
      envFor({
        "2026-08-30.manifest.json": PLAYABLE,
        "2026-08-30.m4a": REAL_AUDIO,
        "2026-08-30.digest.json": DIGEST,
      }),
    );

  test("serves the digest object for a real briefing", async () => {
    const response = await get("2026-08-30", { "X-Mercury-Token": TOKEN });

    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "application/json");
    assert.ok((await response.text()).length > 0);
  });

  test("is behind the same token as everything else", async () => {
    // Not a second auth path, and not an unauthenticated one. The digest is
    // a list of who mails the user and when, which is at least as personal
    // as the transcript the manifest already guards.
    for (const headers of [{}, { "X-Mercury-Token": "wrong" }]) {
      const response = await get("2026-08-30", headers);
      assert.equal(response.status, 401, JSON.stringify(headers));
    }
  });

  test("refuses to serve before the Worker is configured", async () => {
    const response = await worker.fetch(
      new Request("https://w/briefing/2026-08-30/digest", {
        headers: { "X-Mercury-Token": TOKEN },
      }),
      { ...envFor({}), MERCURY_API_TOKEN: "" },
    );
    assert.equal(response.status, 503);
  });

  test("a weak token is refused here too", async () => {
    // Same floor as MIN_TOKEN_LENGTH in app/core/auth.py. A route that
    // skipped this would be the more exposed half of the system being the
    // more permissive one.
    const response = await worker.fetch(
      new Request("https://w/briefing/2026-08-30/digest", {
        headers: { "X-Mercury-Token": "short" },
      }),
      { ...envFor({}), MERCURY_API_TOKEN: "short" },
    );
    assert.equal(response.status, 503);
  });

  test("marks the response private", async () => {
    const response = await get("2026-08-30", { "X-Mercury-Token": TOKEN });
    assert.equal(response.headers.get("cache-control"), "private, no-cache");
  });

  test("a date-shaped impossible date is rejected before it reaches R2", async () => {
    // Same guard as the manifest route: the id becomes an object key, so
    // this is also what stops path traversal reaching the bucket.
    for (const id of ["2026-01-99", "2026-02-30", "../secrets"]) {
      const response = await get(encodeURIComponent(id), {
        "X-Mercury-Token": TOKEN,
      });
      assert.equal(response.status, 400, `${id} should be a 400`);
    }
  });

  test("a briefing with no digest is a 404, not an error", async () => {
    // Every briefing generated before the digest existed has none, and a run
    // whose enrichment failed still ships one. The client shows an
    // explanatory empty state; the audio stays playable either way.
    const response = await worker.fetch(
      new Request("https://w/briefing/2026-08-30/digest", {
        headers: { "X-Mercury-Token": TOKEN },
      }),
      envFor({
        "2026-08-30.manifest.json": PLAYABLE,
        "2026-08-30.m4a": REAL_AUDIO,
      }),
    );

    assert.equal(response.status, 404);
    assert.match((await response.json()).detail, /No digest for 2026-08-30/);
  });

  test("a missing digest does not make the briefing look broken", async () => {
    // The failure to avoid: an absent digest read as an absent briefing,
    // which would send the scheduler off regenerating a briefing that is
    // sitting in the bucket, playable, every morning.
    //
    // This asked `/status` about an EMPTY bucket and asserted only the 200,
    // so it was green whether or not briefingState looked at the digest —
    // with no manifest and no audio the honest answer is `missing`, which is
    // the very state the test claims to rule out. It now sets up a playable
    // briefing for the day /status actually asks about, and withholds only
    // the digest.
    const response = await worker.fetch(
      new Request("https://w/status", { headers: { "X-Mercury-Token": TOKEN } }),
      envFor(forToday({
        "2026-08-30.manifest.json": PLAYABLE,
        "2026-08-30.m4a": REAL_AUDIO,
      })),
    );

    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.briefing_id, TODAY, "premise: keyed to the day asked about");
    assert.equal(body.state, "ok");
  });

  test("POSTing to it is still refused", async () => {
    const response = await worker.fetch(
      new Request("https://w/briefing/2026-08-30/digest", {
        method: "POST",
        headers: { "X-Mercury-Token": TOKEN },
      }),
      envFor({}),
    );
    assert.equal(response.status, 405);
  });
});

describe("a broken USER_TIMEZONE", () => {
  // Intl.DateTimeFormat THROWS a RangeError for an unsupported zone, and that
  // threw straight out of scheduled() before briefingState or any dispatch
  // ran. Every checkpoint, every morning: no generation and no alarm, from one
  // mistyped Worker secret. app/cli.py has always fallen back to UTC here; the
  // two halves of one system disagreeing about that is what made it a hole.
  const broken = (objects = {}) => ({
    ...envFor(objects),
    USER_TIMEZONE: "Not/AZone",
  });

  test("does not stop the schedule", async () => {
    const calls = recordFetch();
    await worker.scheduled({ scheduledTime: GENERATE_CHECKPOINT }, broken());

    assert.equal(calls.length, 1);
    assert.equal(workflowOf(calls[0]), "briefing.yml");
    // 03:40 UTC is still 2026-08-30 in UTC, so the id is usable.
    assert.equal(calls[0].body.inputs.briefing_id, "2026-08-30");
    assert.ok(logs.error.some((line) => line.includes("USER_TIMEZONE")));
  });

  test("does not stop the alarm either", async () => {
    const calls = recordFetch();
    await worker.scheduled({ scheduledTime: ALARM_CHECKPOINT }, broken());
    assert.deepEqual(calls.map(workflowOf), ["briefing-watchdog.yml"]);
  });

  test("/health reports the zone actually in use, not the broken one", async () => {
    // The iOS client caches this value to derive briefing ids locally
    // (BriefingClock). Echoing back a zone the Worker itself could not use
    // hands the phone a value it cannot use either.
    const response = await worker.fetch(new Request("https://w/health"), broken());
    assert.equal((await response.json()).timezone, "UTC");
  });

  test("a usable zone is still reported and used", async () => {
    const response = await worker.fetch(new Request("https://w/health"), envFor({}));
    assert.equal((await response.json()).timezone, "Europe/Lisbon");
  });
});
