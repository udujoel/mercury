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

// Mirrors `MAX_REGENERATIONS_PER_DAY` in the Worker, which is not exported.
// If the Worker's number changes and this one does not, the concurrency
// test fails loudly rather than asserting the wrong ceiling.
const MAX_PER_DAY = 3;

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
        // Size included, as R2 does — the archive route reads it from the
        // listing rather than spending a `head` per briefing.
        objects: page.map((key) => ({ key, size: objects[key]?.size ?? 1 })),
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
  // Audio alongside every manifest: the route lists what can actually be
  // PLAYED, so a manifest on its own is deliberately not enough.
  const listing = {
    "2026-08-28.manifest.json": PLAYABLE,
    "2026-08-28.m4a": REAL_AUDIO,
    "2026-08-30.manifest.json": PLAYABLE,
    "2026-08-30.m4a": REAL_AUDIO,
    "2026-08-29.manifest.json": PLAYABLE,
    "2026-08-29.m4a": REAL_AUDIO,
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
      many[`2026-08-${day}.m4a`] = REAL_AUDIO;
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
        "2026-08-30.m4a": REAL_AUDIO,
        "not-a-date.manifest.json": PLAYABLE,
        "not-a-date.m4a": REAL_AUDIO,
      }),
    );

    // A key that is not a briefing id would be handed straight back to a
    // client that puts it in a URL.
    assert.deepEqual((await res.json()).briefings, ["2026-08-30"]);
  });

  test("omits a briefing whose audio never made it", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings", { headers: { "x-mercury-token": TOKEN } }),
      envFor({
        "2026-08-30.manifest.json": PLAYABLE,
        "2026-08-30.m4a": REAL_AUDIO,
        // The shape `--force` used to leave when a regeneration failed after
        // deleting the old file: 200 on the manifest, 404 on the audio.
        "2026-08-29.manifest.json": PLAYABLE,
      }),
    );

    assert.deepEqual((await res.json()).briefings, ["2026-08-30"]);
  });

  test("omits a briefing whose audio is a zero-byte upload", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings", { headers: { "x-mercury-token": TOKEN } }),
      envFor({
        "2026-08-29.manifest.json": PLAYABLE,
        // `head` returns an object for a zero-byte key, so existence alone
        // read as playable — the trap `briefingState` already documents.
        "2026-08-29.m4a": { size: 0 },
      }),
    );

    assert.deepEqual((await res.json()).briefings, []);
  });

  test("needs the token, like everything else that is about the user", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefings"),
      envFor(listing),
    );
    assert.equal(res.status, 401);
  });
});

describe("settings", () => {
  /** A bucket that also records writes, which `bucket()` does not. */
  // Read-your-writes, and `onlyIf` on PUT honoured the way R2 honours it.
  //
  // Both matter. The regeneration cap reserves its slot with a conditional
  // write and re-reads on a lost race, so a stub that ignored `onlyIf` — or
  // that served `get` from the ORIGINAL objects rather than from what was
  // written — would make the concurrency path untestable and quietly green.
  // That is the same trap the `onlyIf`-on-GET note above describes.
  function writableBucket(objects = {}) {
    const base = bucket(objects);
    const written = {};
    let version = 0;

    const etagOf = (key) =>
      written[key] === undefined ? undefined : `"w-${written[key].version}"`;

    return {
      ...base,
      // Bodies only, so assertions stay `written[key]` as they were.
      get written() {
        return Object.fromEntries(
          Object.entries(written).map(([k, v]) => [k, v.body]),
        );
      },
      get: async (key, options) => {
        const mine = written[key];
        if (mine === undefined) return base.get(key, options);
        return {
          etag: `"w-${mine.version}"`,
          httpEtag: `"w-${mine.version}"`,
          size: mine.body.length,
          body: mine.body,
          text: async () => mine.body,
          json: async () => JSON.parse(mine.body),
        };
      },
      put: async (key, body, options) => {
        const onlyIf = options?.onlyIf;
        const current = etagOf(key);
        if (onlyIf?.etagDoesNotMatch === "*" && current !== undefined) {
          return null; // exists, and the caller asked for a create
        }
        if (onlyIf?.etagMatches !== undefined && onlyIf.etagMatches !== current) {
          return null; // someone else wrote since the caller read
        }
        version += 1;
        written[key] = { body, version };
        return { key, etag: `"w-${version}"` };
      },
    };
  }

  test("GET /settings returns the stored body, not just a 200", async () => {
    // The first version of this asserted only the status and the header, and
    // `bucket()` serves a literal "body" string — so it would have passed
    // even if the route returned the wrong object entirely.
    const env = envFor({});
    env.BRIEFINGS = {
      get: async (key) =>
        key === "settings.json"
          ? { body: JSON.stringify({ briefing_length: "express" }) }
          : null,
      list: async () => ({ objects: [] }),
    };

    const res = await worker.fetch(
      new Request("https://x/settings", { headers: { "x-mercury-token": TOKEN } }),
      env,
    );

    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { briefing_length: "express" });
    // Never cached: a stale read would show a preference just replaced.
    assert.equal(res.headers.get("cache-control"), "no-store");
  });

  test("PUT /settings refuses a field of the wrong type", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        // Passed the name filter before and was stored; Pydantic would then
        // reject the whole artifact on the next run, silently reverting
        // every setting to the environment.
        body: JSON.stringify({ briefing_length: 123 }),
      }),
      env,
    );

    assert.equal(res.status, 400);
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("PUT /settings measures the cap in bytes, not characters", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    // Each of these is one JS character and three UTF-8 bytes, so the body
    // is well under 16k by `String.length` and well over it in bytes.
    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        body: JSON.stringify({ feeds: ["\u3042".repeat(7000)] }),
      }),
      env,
    );

    assert.equal(res.status, 413);
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("GET /settings 404s when none were ever saved", async () => {
    const res = await worker.fetch(
      new Request("https://x/settings", { headers: { "x-mercury-token": TOKEN } }),
      envFor({}),
    );
    // Ordinary, not an error: every deployment starts here.
    assert.equal(res.status, 404);
  });

  test("PUT /settings stores only declared fields", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        body: JSON.stringify({
          briefing_length: "quick",
          voice_a: "v1",
          // Not a declared field. The generation job parses this file, so a
          // write path that forwards unknown keys is a write path that
          // forwards whatever a future client bug puts in them.
          arbitrary: { nested: "junk" },
        }),
      }),
      env,
    );

    assert.equal(res.status, 200);
    const stored = JSON.parse(env.BRIEFINGS.written["settings.json"]);
    assert.equal(stored.briefing_length, "quick");
    assert.equal(stored.voice_a, "v1");
    assert.equal("arbitrary" in stored, false);
  });

  test("PUT /settings needs the token", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        body: JSON.stringify({ briefing_length: "quick" }),
      }),
      env,
    );

    assert.equal(res.status, 401);
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("PUT /settings refuses a body that is not a JSON object", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    for (const body of ["not json", "[1,2,3]", "null"]) {
      const res = await worker.fetch(
        new Request("https://x/settings", {
          method: "PUT",
          headers: { "x-mercury-token": TOKEN },
          body,
        }),
        env,
      );
      assert.equal(res.status, 400, body);
    }
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("PUT /settings caps the size", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        body: JSON.stringify({ feeds: [Array(20000).join("x")] }),
      }),
      env,
    );

    assert.equal(res.status, 413);
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("GET /voices reshapes the upstream list", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          voices: [
            {
              voice_id: "v1",
              name: "Brian",
              preview_url: "https://cdn/x.mp3",
              // Fields the picker has no use for. Reshaping rather than
              // proxying is what keeps them off the phone.
              settings: { stability: 0.5 },
              fine_tuning: { state: "fine_tuned" },
              labels: { accent: "american" },
            },
          ],
        }),
        { headers: { "content-type": "application/json" } },
      );

    try {
      const env = { ...envFor({}), ELEVENLABS_API_KEY: "k" };
      const res = await worker.fetch(
        new Request("https://x/voices", { headers: { "x-mercury-token": TOKEN } }),
        env,
      );

      assert.equal(res.status, 200);
      const body = await res.json();
      assert.equal(body.voices.length, 1);
      assert.equal(body.voices[0].name, "Brian");
      assert.equal("settings" in body.voices[0], false);
      assert.equal("fine_tuning" in body.voices[0], false);
    } finally {
      globalThis.fetch = original;
    }
  });

  test("GET /voices without a key is an explained empty list, not a failure", async () => {
    const res = await worker.fetch(
      new Request("https://x/voices", { headers: { "x-mercury-token": TOKEN } }),
      envFor({}),
    );

    assert.equal(res.status, 200);
    const body = await res.json();
    assert.deepEqual(body.voices, []);
    // The reason matters: an empty list with no explanation reads as
    // "there are no voices".
    assert.ok(body.detail);
  });

  test("GET /voices still needs the token", async () => {
    const res = await worker.fetch(new Request("https://x/voices"), envFor({}));
    assert.equal(res.status, 401);
  });

  test("POST /regenerate dispatches the same workflow the cron runs", async () => {
    const original = globalThis.fetch;
    const calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push({ url: String(url), body: JSON.parse(init.body) });
      return new Response(null, { status: 204 });
    };

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      env.BRIEFINGS = writableBucket({});

      const res = await worker.fetch(
        new Request("https://x/regenerate", {
          method: "POST",
          headers: { "x-mercury-token": TOKEN },
        }),
        env,
      );

      assert.equal(res.status, 200);
      assert.equal(calls.length, 1);
      assert.match(calls[0].url, /\/actions\/workflows\/.+\/dispatches$/);
      // force, or the run is a no-op: today's briefing already exists,
      // which is the entire reason someone is pressing this.
      assert.equal(calls[0].body.inputs.force, "true");
    } finally {
      globalThis.fetch = original;
    }
  });

  test("POST /regenerate needs the token", async () => {
    const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/regenerate", { method: "POST" }),
      env,
    );

    assert.equal(res.status, 401);
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("POST /regenerate stops at the daily cap", async () => {
    const original = globalThis.fetch;
    let dispatches = 0;
    globalThis.fetch = async () => {
      dispatches += 1;
      return new Response(null, { status: 204 });
    };

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      const store = {};
      env.BRIEFINGS = {
        get: async (k) => (store[k] ? { text: async () => store[k] } : null),
        put: async (k, v) => {
          store[k] = v;
        },
        list: async () => ({ objects: [] }),
      };

      let last;
      for (let i = 0; i < 5; i += 1) {
        last = await worker.fetch(
          new Request("https://x/regenerate", {
            method: "POST",
            headers: { "x-mercury-token": TOKEN },
          }),
          env,
        );
      }

      // Three allowed, then refused — and no further dispatch after the cap.
      assert.equal(dispatches, 3);
      assert.equal(last.status, 429);
      const body = await last.json();
      assert.equal(body.used, 3);
    } finally {
      globalThis.fetch = original;
    }
  });

  test("an unconfigured dispatch token says so, rather than 'try again'", async () => {
    // The button returned a bare 502 for months while the cause was a
    // secret nobody had set — and the reason sat in a Worker log the phone
    // cannot read. "Could not start" reads as bad luck; this reads as a
    // thing to go and fix.
    const env = { ...envFor({}), GITHUB_REPO: "o/r" };
    delete env.GITHUB_DISPATCH_TOKEN;
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/regenerate", {
        method: "POST",
        headers: { "x-mercury-token": TOKEN },
      }),
      env,
    );

    assert.equal(res.status, 503, "not transient, so not a 502");
    const body = await res.json();
    assert.match(body.detail, /dispatch token is not configured/);
    // And the same token drives the morning trigger, which is the half
    // nobody would connect on their own.
    assert.match(body.detail, /punctual morning trigger/);
  });

  test("a failed dispatch does not burn the day's allowance", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response("nope", { status: 500 });

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      env.BRIEFINGS = writableBucket({});

      const res = await worker.fetch(
        new Request("https://x/regenerate", {
          method: "POST",
          headers: { "x-mercury-token": TOKEN },
        }),
        env,
      );

      assert.equal(res.status, 502);
      // The allowance is what matters, not whether an object exists.
      //
      // This used to assert nothing had been written at all, which was true
      // of the old write-late order and is not true of reserve-then-release
      // — the counter is written, then written back. Asserting the byte
      // instead of the behaviour would have failed a fix that preserved
      // exactly the property the test was named for, so it now asserts the
      // property: the day still has its full allowance.
      const counter = JSON.parse(
        env.BRIEFINGS.written[
          Object.keys(env.BRIEFINGS.written).find((k) =>
            k.endsWith(".regenerations.json"),
          )
        ],
      );
      assert.equal(counter.count, 0);
    } finally {
      globalThis.fetch = original;
    }
  });

  test("simultaneous taps cannot dispatch past the cap", async () => {
    const original = globalThis.fetch;
    let dispatches = 0;
    globalThis.fetch = async () => {
      dispatches += 1;
      // A dispatch that does not resolve immediately, so every request is
      // in flight at once. With the old read-check-dispatch-write order all
      // eight read `used === 0`, all eight dispatch, and all eight write
      // `count: 1` — the cap never engages and this asserts 3, not 8.
      await new Promise((resolve) => setTimeout(resolve, 5));
      return new Response(null, { status: 204 });
    };

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      env.BRIEFINGS = writableBucket({});

      const responses = await Promise.all(
        Array.from({ length: 8 }, () =>
          worker.fetch(
            new Request("https://x/regenerate", {
              method: "POST",
              headers: { "x-mercury-token": TOKEN },
            }),
            env,
          ),
        ),
      );

      assert.equal(dispatches, MAX_PER_DAY);
      assert.equal(
        responses.filter((r) => r.status === 200).length,
        MAX_PER_DAY,
      );
      // Everyone else is refused — 429 over the cap, 409 if they lost the
      // write race often enough to give up. Neither is a 500 and neither
      // is a silent success.
      assert.ok(
        responses.every((r) => [200, 429, 409].includes(r.status)),
        responses.map((r) => r.status).join(","),
      );

      const [key] = Object.keys(env.BRIEFINGS.written);
      assert.equal(JSON.parse(env.BRIEFINGS.written[key]).count, MAX_PER_DAY);
    } finally {
      globalThis.fetch = original;
    }
  });

  test("a counter with a malformed count is treated as spent", async () => {
    // Parses as JSON and carries no usable number: a partial write, or a
    // field renamed by a future version. `reserveRegeneration` read this as
    // `undefined || 0` and handed out three fresh paid generations, while
    // `releaseRegeneration` refused to touch the same object — two halves
    // of one counter disagreeing about what malformed means.
    for (const body of [
      '{"briefing_id":"x"}',
      '{"count":null}',
      '{"count":"2"}',
      // Fractional: `Number.isFinite` accepted it, granting a generation and
      // storing 2.5 — a counter that keeps working while never holding a
      // number anything wrote.
      '{"count":1.5}',
      '{"count":-1}',
    ]) {
      const original = globalThis.fetch;
      let dispatches = 0;
      globalThis.fetch = async () => {
        dispatches += 1;
        return new Response(null, { status: 204 });
      };
      try {
        const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
        env.BRIEFINGS = {
          get: async () => ({ etag: '"c"', text: async () => body }),
          put: async () => ({ key: "k", etag: '"d"' }),
          list: async () => ({ objects: [] }),
        };

        const res = await worker.fetch(
          new Request("https://x/regenerate", {
            method: "POST",
            headers: { "x-mercury-token": TOKEN },
          }),
          env,
        );

        assert.equal(res.status, 429, `${body} was treated as an allowance`);
        assert.equal(dispatches, 0, `${body} dispatched a paid workflow`);
      } finally {
        globalThis.fetch = original;
      }
    }
  });

  test("a corrupt counter is treated as spent, not as zero", async () => {
    const original = globalThis.fetch;
    let dispatches = 0;
    globalThis.fetch = async () => {
      dispatches += 1;
      return new Response(null, { status: 204 });
    };

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      // Whatever wrote this, it is not something to bill a briefing
      // against. Served for every key, because the handler reads exactly
      // one and the test does not need to recompute today's id to name it.
      env.BRIEFINGS = {
        get: async () => ({
          etag: '"c"',
          text: async () => "{ this is not json",
        }),
        put: async () => ({ key: "k", etag: '"d"' }),
        list: async () => ({ objects: [] }),
      };

      const res = await worker.fetch(
        new Request("https://x/regenerate", {
          method: "POST",
          headers: { "x-mercury-token": TOKEN },
        }),
        env,
      );

      assert.equal(res.status, 429);
      assert.equal(dispatches, 0);
    } finally {
      globalThis.fetch = original;
    }
  });

  test("a rollback cannot undo someone else's reservation", async () => {
    const original = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = async () => {
      calls += 1;
      if (calls === 1) {
        // The first dispatch is slow AND fails, which is the window the
        // rollback has to be safe in.
        await new Promise((r) => setTimeout(r, 25));
        return new Response("nope", { status: 500 });
      }
      return new Response(null, { status: 204 });
    };

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      env.BRIEFINGS = writableBucket({});
      const post = () =>
        worker.fetch(
          new Request("https://x/regenerate", {
            method: "POST",
            headers: { "x-mercury-token": TOKEN },
          }),
          env,
        );

      const first = post();
      // Long enough for the first to have reserved and be waiting on its
      // dispatch, so the second reserves on top of it.
      await new Promise((r) => setTimeout(r, 5));
      const second = post();
      const [a, b] = await Promise.all([first, second]);

      assert.equal(a.status, 502, "the first dispatch failed");
      assert.equal(b.status, 200, "the second one started");

      // EXACTLY one generation is running, so exactly one is spent.
      //
      // Both failure modes this has had land on this one number, which is
      // why it is pinned rather than bounded:
      //   0 — the rollback wrote unconditionally and handed back an
      //       allowance the successful request was holding.
      //   2 — the rollback was conditional and simply gave up when the
      //       precondition failed, leaking the failed request's slot and
      //       bringing the cap on a request early, every day.
      const [key] = Object.keys(env.BRIEFINGS.written);
      const counter = JSON.parse(env.BRIEFINGS.written[key]);
      assert.equal(
        counter.count,
        1,
        `one generation started, so one is spent — got ${counter.count}`,
      );
    } finally {
      globalThis.fetch = original;
    }
  });

  test("a rollback that loses the race retries instead of leaking the slot", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response("nope", { status: 500 });

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      const bucket = writableBucket({});
      const key = () =>
        Object.keys(bucket.written).find((k) =>
          k.endsWith(".regenerations.json"),
        );

      // Exactly one interfering write, slipped in between the rollback's
      // read and its write — which is the only moment at which the
      // rollback's precondition can fail, and the moment a rollback that
      // gives up on a failed precondition leaks its slot forever.
      let interfered = false;
      env.BRIEFINGS = {
        ...bucket,
        get written() {
          return bucket.written;
        },
        get: bucket.get,
        list: bucket.list,
        put: async (k, body, options) => {
          const decrementing =
            options?.onlyIf?.etagMatches !== undefined &&
            JSON.parse(body).count === 0;
          if (decrementing && !interfered) {
            interfered = true;
            // Another request reserves. Unconditional, standing in for a
            // write that has already committed by the time ours is tried.
            await bucket.put(
              k,
              JSON.stringify({ count: 2, briefing_id: "x" }),
              {},
            );
            // Ours is now stale, exactly as R2 would report it.
            return null;
          }
          return bucket.put(k, body, options);
        },
      };

      const res = await worker.fetch(
        new Request("https://x/regenerate", {
          method: "POST",
          headers: { "x-mercury-token": TOKEN },
        }),
        env,
      );
      assert.equal(res.status, 502, "the dispatch failed");

      // The interfering write left 2. Our rollback owns one of those slots
      // and must give exactly that one back, whatever else happened — so 1.
      // A rollback that gave up on the failed precondition would leave 2,
      // charging the day for a generation that never started and bringing
      // the cap on a request early, cumulatively, every day.
      const counter = JSON.parse(bucket.written[key()]);
      assert.equal(
        counter.count,
        1,
        `the rollback must retry, not leak — got ${counter.count}`,
      );
    } finally {
      globalThis.fetch = original;
    }
  });

  test("the regeneration counter is keyed so retention sweeps it", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response(null, { status: 204 });

    try {
      const env = { ...envFor({}), GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r" };
      env.BRIEFINGS = writableBucket({});

      await worker.fetch(
        new Request("https://x/regenerate", {
          method: "POST",
          headers: { "x-mercury-token": TOKEN },
        }),
        env,
      );

      // `YYYY-MM-DD.` prefixed, which is what `briefing_id_from_key`
      // matches — so `delete_briefing` removes it with the briefing rather
      // than leaving one small object per day in the bucket forever.
      const [key] = Object.keys(env.BRIEFINGS.written);
      assert.match(key, /^\d{4}-\d{2}-\d{2}\.regenerations\.json$/);
    } finally {
      globalThis.fetch = original;
    }
  });

  test("every field UserSettings carries survives a save", async () => {
    // The whitelist drops anything it does not name, and a dropped field
    // looks exactly like a setting that applied and did nothing. `music_set`
    // was missing from it for a whole release: the picker sent it, the
    // pipeline read it, and this Worker deleted it in between.
    //
    // Listed explicitly rather than derived, so ADDING a field to
    // `UserSettings` without adding it here fails on the next run of this
    // test rather than on someone's morning.
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    const sent = {
      schema_version: 1,
      briefing_length: "standard",
      tts_provider: "elevenlabs",
      voice_a: "a",
      voice_b: "b",
      music_set: "broadsheet",
      disabled_segments: ["weather", "follow_ups"],
      feeds: ["https://example.com/feed.xml"],
      muted_senders: ["noreply@example.com"],
    };

    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        body: JSON.stringify(sent),
      }),
      env,
    );

    assert.equal(res.status, 200);
    const saved = JSON.parse(env.BRIEFINGS.written["settings.json"]);
    for (const [key, value] of Object.entries(sent)) {
      assert.deepEqual(saved[key], value, `${key} was dropped on save`);
    }
  });

  test("a segment list of the wrong shape is refused, not dropped", async () => {
    const env = envFor({});
    env.BRIEFINGS = writableBucket({});

    const res = await worker.fetch(
      new Request("https://x/settings", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        body: JSON.stringify({ disabled_segments: "weather" }),
      }),
      env,
    );

    assert.equal(res.status, 400, "a string is not a list of segments");
    assert.equal(Object.keys(env.BRIEFINGS.written).length, 0);
  });

  test("PUT to anything else is still refused", async () => {
    const res = await worker.fetch(
      new Request("https://x/briefing/2026-08-30", {
        method: "PUT",
        headers: { "x-mercury-token": TOKEN },
        body: "{}",
      }),
      envFor({}),
    );
    // The write exception is settings and nothing else.
    assert.equal(res.status, 405);
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
