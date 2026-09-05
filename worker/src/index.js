/**
 * Mercury read Worker — serves briefings from a private R2 bucket.
 *
 * The iOS client speaks FastAPI routes (`/briefing/{id}`,
 * `/briefing/{id}/audio`, `/briefing/{id}/digest`) while R2 stores flat
 * object keys (`{id}.manifest.json`, `{id}.m4a`, `{id}.digest.json`). This
 * translates between them so the client needs no changes — only a different
 * API_HOST.
 *
 * The bucket stays private. Only this Worker holds the binding, which is why
 * it exists rather than simply making the bucket public: the manifest and
 * audio are built from a real inbox and calendar, and a public bucket URL is
 * not secret just because it is unguessable.
 *
 * It also carries the morning schedule (ADR-010). GitHub's `schedule` event
 * is documented best-effort — "some queued jobs may be dropped" — and seven
 * consecutive days of `cron: "0 4 * * *"` were CREATED 32 minutes to 12 hours
 * late. Cloudflare Cron Triggers run on UTC time with no equivalent caveat,
 * so the punctual trigger lives here and calls workflow_dispatch, while
 * GitHub's own schedule is demoted to a backstop.
 */

const AUDIO_TYPES = {
  m4a: "audio/mp4",
  wav: "audio/wav",
};

// Same header the FastAPI backend uses, so the client is unchanged.
const TOKEN_HEADER = "X-Mercury-Token";

// The deadline the whole schedule exists to hit: the briefing is in R2 before
// 06:00 Europe/Lisbon. Lisbon is UTC+1 in summer, so the tight case is 05:00
// UTC and every checkpoint is sized against it. Cron is UTC-only and has no
// DST, here or on GitHub, so in WINTER this fixed UTC deadline sits an hour
// before the local one — the alarm can fire up to 52 minutes early. That is
// deliberate and it is the safe direction: an alarm about a briefing that is
// still missing 50 minutes after the last generation attempt is an early
// alarm about a real absence, not a false one, and the opposite error costs
// the user their morning. ADR-010 says the same thing.
const DEADLINE_UTC_MINUTES = 5 * 60;

// The alarm is raised at its own checkpoint, LATER than any checkpoint that
// dispatches generation, and it never shares a tick with one.
//
// It used to be dispatched immediately after the generation dispatch at 05:08.
// `workflow_dispatch` only queues a run, so the watchdog reached R2 first and
// reported "no briefing" about a briefing that was being written as it looked.
// A watchdog that cries wolf every morning is worse than no watchdog: the
// email it sends is the one signal this design has, and it stops being read
// the moment it is usually wrong. 32 minutes is more than ten times the
// observed 71-second generation, plus Actions queueing and checkout.
const ALARM_UTC_MINUTES = 5 * 60 + 40;

// Workflow file names, not ids. The REST API accepts either, and the file
// name survives the workflow being deleted and recreated.
const GENERATE_WORKFLOW = "briefing.yml";
const WATCHDOG_WORKFLOW = "briefing-watchdog.yml";

/**
 * PRIVATE, and revalidated every time.
 *
 * "private" rather than "public" because these responses are the transcript
 * and audio of the user's email and calendar, returned only after a token
 * check; "public" would authorise shared caches — Cloudflare's own edge, any
 * proxy in between — to store and re-serve them, which is at odds with the
 * entire reason the bucket is private.
 *
 * "no-cache" rather than "max-age=3600" because a briefing can be REPLACED.
 * A forced regeneration fixes a briefing that shipped empty or degraded, and
 * with an hour of freely reusable cache the phone would keep playing the
 * broken one for up to an hour after it was fixed — with no way to invalidate
 * it. "no-cache" still lets the client store the response; it just has to ask
 * first. Paired with the etag below that costs one conditional request and a
 * 304, not a re-download of ~3MB of audio.
 */
const CACHE_CONTROL = "private, no-cache";

// Same floor as MIN_PLAYABLE_AUDIO_BYTES in app/synthesis/storage.py. A real
// briefing is roughly 3MB and a bare WAV header is 44 bytes, so a kilobyte is
// three orders of magnitude below anything genuine and cannot reject a real
// file — while still catching the zero-byte and truncated objects that a
// half-finished upload leaves behind.
const MIN_PLAYABLE_AUDIO_BYTES = 1024;

// Same floor as MIN_TOKEN_LENGTH in app/core/auth.py. Without it the Worker
// would happily serve private briefings behind a secret the backend rejects
// as too weak — two halves of one system disagreeing about what counts as
// authenticated, with the more exposed half being the more permissive one.
const MIN_TOKEN_LENGTH = 32;

/** Constant-time compare — a plain === leaks the token prefix through timing. */
function tokensMatch(given, expected) {
  if (typeof given !== "string" || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) {
    diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...extraHeaders },
  });
}

/**
 * decodeURIComponent THROWS on a malformed escape such as "%zz", which would
 * otherwise escape the handler and surface as an uncaught 500 on an
 * authenticated request. A bad id is a client error, not a server one.
 */
function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

/**
 * Reject anything that is not a real YYYY-MM-DD date. The id becomes an R2
 * key, so this also stops path traversal ("../other-bucket-object") reaching
 * the bucket at all.
 *
 * Shape is not enough: `2026-01-99` and `2026-02-30` pass the regex and are
 * not days. Same hole, and the same fix, as `briefing_id_from_key` in
 * app/synthesis/storage.py — where a date-shaped impossible id reached the
 * retention path and got deleted.
 */
function validBriefingId(id) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(id)) return false;
  // Date rolls impossible components over silently (Feb 30 becomes Mar 2), so
  // round-trip it and insist the answer is the string we started with.
  const parsed = new Date(`${id}T00:00:00Z`);
  return (
    !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === id
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Liveness stays public: it exposes nothing, and guarding it breaks
    // uptime probes. It also reports the timezone the client uses to derive
    // briefing ids locally, matching the FastAPI /health contract.
    if (url.pathname === "/health") {
      return json({
        status: "ok",
        service: "mercury-worker",
        timezone: resolvedTimeZone(env),
      });
    }

    if (request.method !== "GET") {
      // Generation is a scheduled batch job (ADR-008), not something a Worker
      // can do — no Python pipeline, no 127-second budget.
      return json({ detail: "This endpoint is read-only." }, 405);
    }

    // Fail closed, exactly like app/core/auth.py: an unconfigured Worker
    // refuses rather than defaulting to open.
    if (!env.MERCURY_API_TOKEN) {
      return json(
        { detail: "Worker is not configured with MERCURY_API_TOKEN." },
        503,
      );
    }
    if (env.MERCURY_API_TOKEN.length < MIN_TOKEN_LENGTH) {
      // A placeholder like "changeme" reads as configured and is worse than
      // blank. Refuse, exactly as the backend does.
      return json(
        {
          detail:
            `MERCURY_API_TOKEN must be at least ${MIN_TOKEN_LENGTH} characters.`,
        },
        503,
      );
    }
    if (!tokensMatch(request.headers.get(TOKEN_HEADER) || "", env.MERCURY_API_TOKEN)) {
      return json({ detail: `Missing or invalid ${TOKEN_HEADER}.` }, 401);
    }

    // Authenticated, unlike /health: whether a briefing exists for today is
    // information about the user's morning, and the bucket is private for
    // exactly that kind of reason. The phone already sends the token, and so
    // can a human wondering "did it actually run?" — an answer that does not
    // depend on GitHub having noticed anything.
    if (url.pathname === "/status") {
      const id = briefingIdFor(new Date(), resolvedTimeZone(env));
      const state = await briefingState(env, id);
      return json({ briefing_id: id, state, ok: state === "ok" });
    }

    // Which mornings are still here.
    //
    // `list_briefing_ids()` has existed in both storage backends since the
    // R2 work and was reachable from nothing. Without this route an archive
    // screen has to guess dates and probe, which is thirty 404s to draw one
    // list — and thirty chances to be wrong about the timezone the ids were
    // derived in.
    //
    // The window is whatever the retention sweep has left (ADR-011, 30
    // days), so the client reads its horizon from the data rather than
    // hardcoding a number that can drift away from the sweeper's.
    if (url.pathname === "/briefings") {
      return serveBriefingList(env);
    }

    const audio = url.pathname.match(/^\/briefing\/([^/]+)\/audio$/);
    if (audio) {
      const id = safeDecode(audio[1]);
      if (id === null) return json({ detail: "Invalid briefing id." }, 400);
      return serveAudio(request, env, id);
    }

    // The browse surface: every newsletter that arrived and every story that
    // clustered, including the ones the ranker never gave to the hosts. A
    // SIBLING of the manifest, not part of it — the manifest is what the
    // player downloads to start playing audio, and folding ~100KB of browse
    // data into it would make every launch pay for a screen the user may
    // never open.
    //
    // Behind the same token check as everything else above, which is the
    // point of putting it here rather than in its own handler: this is a
    // list of who mails the user and when, which is at least as personal as
    // the transcript.
    const digest = url.pathname.match(/^\/briefing\/([^/]+)\/digest$/);
    if (digest) {
      const id = safeDecode(digest[1]);
      if (id === null) return json({ detail: "Invalid briefing id." }, 400);
      return serveDigest(request, env, id);
    }

    const manifest = url.pathname.match(/^\/briefing\/([^/]+)$/);
    if (manifest) {
      const id = safeDecode(manifest[1]);
      if (id === null) return json({ detail: "Invalid briefing id." }, 400);
      return serveManifest(request, env, id);
    }

    return json({ detail: "Not found." }, 404);
  },

  /**
   * The punctual morning trigger (ADR-010).
   *
   * Checkpoints are configured in wrangler.toml. Each one asks the same
   * question — is there a playable briefing for today? — and the answer
   * decides what happens, so a checkpoint that fires after a healthy run is
   * a single R2 read and nothing else.
   */
  async scheduled(controller, env) {
    const scheduledAt = new Date(controller.scheduledTime);
    const id = briefingIdFor(scheduledAt, resolvedTimeZone(env));
    const state = await briefingState(env, id);

    if (state === "ok") {
      console.log(`briefing ${id}: already in R2, nothing to do`);
      return;
    }

    const utcMinutes =
      scheduledAt.getUTCHours() * 60 + scheduledAt.getUTCMinutes();
    console.log(
      `briefing ${id}: ${state} at ${scheduledAt.toISOString()} ` +
        `(past deadline: ${utcMinutes >= DEADLINE_UTC_MINUTES})`,
    );

    // The alarm checkpoint raises the alarm and does NOTHING else. Every
    // generation attempt has already been made and has had half an hour to
    // land, so a briefing that is still missing here is missing for a reason
    // no further dispatch is going to fix — and dispatching one anyway is
    // exactly what made the alarm race the run it had just started.
    if (utcMinutes >= ALARM_UTC_MINUTES) {
      await dispatchWorkflow(env, WATCHDOG_WORKFLOW, { briefing_id: id });
      return;
    }

    // Name the briefing this checkpoint actually checked. Without it the
    // runner derives its own "today", so a run that starts after the user's
    // local midnight generates the RUNNER's day rather than the day found
    // missing here — the sixth date bug in a project that has had five.
    const inputs = { briefing_id: id };

    // An EMPTY briefing must be forced. Generation is idempotent, so a stored
    // manifest — even one with zero turns and zero milliseconds of audio —
    // makes every later run decline to replace it. That is how a single
    // total TTS failure turned into a briefing that was broken permanently
    // rather than for one morning.
    if (state === "empty") inputs.force = "true";

    await dispatchWorkflow(env, GENERATE_WORKFLOW, inputs);
  },
};

/**
 * Today's briefing id, in the USER's timezone.
 *
 * Must agree with `_today_briefing_id` in backend/app/cli.py. A trigger that
 * names the briefing differently from the generator asks for the wrong day
 * every morning the two disagree, and this project has already had five
 * separate date bugs. `formatToParts` rather than a locale that happens to
 * print ISO order.
 */
function briefingIdFor(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const at = (type) => parts.find((part) => part.type === type).value;
  return `${at("year")}-${at("month")}-${at("day")}`;
}

/**
 * The timezone this Worker will actually use — never one that throws.
 *
 * `Intl.DateTimeFormat` raises a RangeError for an unsupported zone, and that
 * threw straight out of `scheduled()` before `briefingState` or any dispatch
 * ran. One mistyped `wrangler secret put USER_TIMEZONE` therefore silenced
 * every checkpoint, every morning: no generation AND no alarm, which is the
 * whole failure mode ADR-010 exists to make impossible.
 *
 * UTC rather than refusing to start, because `_today_briefing_id` in
 * backend/app/cli.py does exactly that. The two halves of one system have to
 * name the same briefing the same way, and they still do while both are
 * falling back. Loud in the log, since the ids will be a day off for anyone
 * far enough from UTC.
 */
function resolvedTimeZone(env) {
  const configured = env.USER_TIMEZONE || "UTC";
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: configured });
    return configured;
  } catch {
    console.error(
      `USER_TIMEZONE ${JSON.stringify(configured)} is not a timezone this ` +
        "runtime knows; falling back to UTC. Briefing ids will be named in " +
        "UTC until it is fixed (wrangler secret put USER_TIMEZONE).",
    );
    return "UTC";
  }
}

/**
 * "ok" | "empty" | "missing" — is there a briefing the phone could play?
 *
 * Needs no credential: the R2 binding is already here. That is deliberate and
 * load-bearing. The alarm has to keep working when the GitHub token is the
 * thing that broke, otherwise the watchdog fails in exactly the situation it
 * exists for.
 *
 * "empty" is its own answer rather than folded into "missing" because the two
 * need opposite treatment: a missing briefing is generated, an empty one has
 * to be FORCED past the idempotency check that would otherwise preserve it
 * forever.
 */
async function briefingState(env, id) {
  const object = await env.BRIEFINGS.get(`${id}.manifest.json`);
  if (!object) return "missing";

  try {
    const manifest = await object.json();
    if (
      !Array.isArray(manifest.turns) ||
      manifest.turns.length === 0 ||
      !(manifest.duration_ms > 0)
    ) {
      return "empty";
    }
  } catch {
    // A manifest that will not parse is not one the phone can play either.
    return "empty";
  }

  // A manifest with no audio behind it is the shape `--force` used to leave
  // when a regeneration failed after deleting the old file: 200 on the
  // manifest, 404 on the audio.
  //
  // Existence is not enough. `head` returns an object for a zero-byte key, so
  // an upload that died mid-flight read as playable, suppressed both the retry
  // and the alarm, and — because generation is idempotent — kept doing so for
  // the rest of the day. Size is the cheap signal that separates "there is a
  // file" from "there is audio".
  for (const ext of ["m4a", "wav"]) {
    const object = await env.BRIEFINGS.head(`${id}.${ext}`);
    if (object && object.size >= MIN_PLAYABLE_AUDIO_BYTES) return "ok";
  }
  return "empty";
}

/**
 * Ask GitHub to run a workflow now.
 *
 * This is the one thing here that needs a credential, and it is the honest
 * cost of ADR-010: a Worker that previously held only a read token now also
 * holds a token that can start a workflow. It is scoped to this repository
 * and to Actions alone, and it is unreachable from the fetch handler — but it
 * is new attack surface, and pretending otherwise would be dishonest.
 */
async function dispatchWorkflow(env, workflow, inputs) {
  if (!env.GITHUB_DISPATCH_TOKEN || !env.GITHUB_REPO) {
    // Loud, because the silent version of this is a Worker that appears
    // healthy every morning while triggering nothing at all.
    console.error(
      `cannot dispatch ${workflow}: GITHUB_DISPATCH_TOKEN and GITHUB_REPO ` +
        "must both be set (wrangler secret put / wrangler.toml [vars])",
    );
    return false;
  }

  // `fetch` REJECTS on a transport failure — DNS, TLS, connection reset — and
  // only a fulfilled response ever reaches the response.ok check below. An
  // unhandled rejection here aborted scheduled() outright, so a morning where
  // GitHub was briefly unreachable left no decision in the log at all. A
  // failed dispatch is a return value, not an exception.
  let response;
  try {
    response = await fetch(
      `https://api.github.com/repos/${env.GITHUB_REPO}` +
        `/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
          accept: "application/vnd.github+json",
          "x-github-api-version": "2022-11-28",
          // GitHub rejects API requests without one.
          "user-agent": "mercury-briefings-worker",
        },
        body: JSON.stringify({
          ref: env.GITHUB_REF || "main",
          inputs,
        }),
      },
    );
  } catch (err) {
    console.error(`dispatch of ${workflow} failed: ${err}`);
    return false;
  }

  if (!response.ok) {
    // 404 here usually means the token cannot see the repo rather than that
    // the workflow is missing — GitHub hides repos a token has no permission
    // for. 403 usually means the fine-grained token is missing the Actions
    // read-and-write permission.
    console.error(
      `dispatch of ${workflow} failed: HTTP ${response.status} ` +
        `${await response.text()}`,
    );
    return false;
  }

  console.log(`dispatched ${workflow} (${JSON.stringify(inputs)})`);
  return true;
}

/**
 * R2 returns an object with no `body` when a conditional request's
 * precondition fails — that is the 304 case, and the only way to tell it
 * apart from a hit.
 */
function isNotModified(object) {
  return !("body" in object);
}

function notModified(object) {
  return new Response(null, {
    status: 304,
    headers: { "cache-control": CACHE_CONTROL, etag: object.httpEtag },
  });
}

/**
 * One JSON object out of the bucket, conditional-GET and all.
 *
 * Shared by the manifest and the digest deliberately. They differ only in
 * the key and the 404 wording, and a second copy of this is a second place
 * for `cache-control` or the 304 handling to be got wrong — the digest is
 * the private browse record of an inbox, so it is exactly the response that
 * must not end up in a shared cache because someone forgot a header.
 */
async function serveJsonObject(request, env, id, key, missingDetail) {
  if (!validBriefingId(id)) return json({ detail: "Invalid briefing id." }, 400);

  // Handing R2 the request headers lets it evaluate If-None-Match itself,
  // rather than this code hand-parsing etag lists and weak validators.
  const object = await env.BRIEFINGS.get(key, { onlyIf: request.headers });
  if (!object) return json({ detail: missingDetail }, 404);
  if (isNotModified(object)) return notModified(object);

  return new Response(object.body, {
    headers: {
      "content-type": "application/json",
      "cache-control": CACHE_CONTROL,
      etag: object.httpEtag,
    },
  });
}

/**
 * Every briefing id with a manifest in the bucket, newest first.
 *
 * Keyed off the MANIFEST, not the audio: a manifest with no audio is a
 * broken briefing the client cannot play, and one that appeared in an
 * archive list would be a row that does nothing when tapped.
 *
 * Paginated properly rather than trusting one page. Thirty days of
 * briefings is about ninety objects today, comfortably inside R2's default
 * page — but "comfortably inside" is a fact about the current retention
 * setting, not about the code, and raising retention should not silently
 * truncate this list.
 */
async function serveBriefingList(env) {
  const manifests = new Set();
  const audible = new Set();
  let cursor;

  do {
    const page = await env.BRIEFINGS.list({ cursor, limit: 1000 });
    for (const object of page.objects) {
      // The same validation every id-taking route applies, because a key
      // that does not parse as a briefing id is not one — and it would be
      // handed straight back to a client that will put it in a URL.
      if (object.key.endsWith(SUFFIX)) {
        const id = object.key.slice(0, -SUFFIX.length);
        if (validBriefingId(id)) manifests.add(id);
        continue;
      }
      for (const ext of AUDIO_EXTS) {
        if (!object.key.endsWith(ext)) continue;
        const id = object.key.slice(0, -ext.length);
        // Size, not existence. A zero-byte key is an upload that died
        // mid-flight and `head` returns an object for one, which is the
        // trap `briefingState` already documents.
        if (validBriefingId(id) && object.size >= MIN_PLAYABLE_AUDIO_BYTES) {
          audible.add(id);
        }
      }
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  // A manifest is necessary and not sufficient. `--force` used to leave
  // exactly this shape when a regeneration failed after deleting the old
  // audio — 200 on the manifest, 404 on the file — and an archive row for
  // one of those is a date that does nothing when tapped.
  //
  // Decided from the LISTING rather than by asking `briefingState` per id.
  // That version cost a manifest `get` plus an audio `head` each, so a
  // thirty-day archive spent sixty-plus subrequests against the Workers
  // free-plan limit of 50 — the route would not have failed a test, it
  // would have failed in production, on the plan ADR-008 is built on.
  // `list` already carries every key and its size, so this costs nothing
  // beyond the pages already being walked.
  //
  // The deliberate gap: this does NOT check that the manifest PARSES and
  // has turns, which `briefingState` does. That needs a read per briefing
  // and is the whole cost being avoided. Existence plus audio size covers
  // the failure modes that actually happen; a manifest that is present,
  // sized, and internally broken shows up when it is opened.
  const playable = [...manifests].filter((id) => audible.has(id));

  // Ids are ISO dates, so lexicographic ordering IS chronological — the one
  // place `YYYY-MM-DD` being a string is an advantage rather than a trap.
  playable.sort().reverse();

  return json(
    { briefings: playable, count: playable.length },
    200,
    // Short, not immutable: this list changes once a day, in both
    // directions — a new briefing at the top, an expired one off the
    // bottom. A stale archive that hides this morning is worse than a
    // request.
    { "cache-control": "private, max-age=60" },
  );
}

const SUFFIX = ".manifest.json";
const AUDIO_EXTS = [".m4a", ".wav"];

function serveManifest(request, env, id) {
  return serveJsonObject(
    request, env, id, `${id}.manifest.json`, `No briefing for ${id}.`,
  );
}

/**
 * 404 here is ORDINARY, not an error.
 *
 * Every briefing generated before the digest existed has no digest object,
 * and a run whose enrichment failed still ships one — so the client shows an
 * explanatory empty state rather than an error, and an old briefing stays
 * playable with no browse screen behind it.
 */
function serveDigest(request, env, id) {
  return serveJsonObject(
    request, env, id, `${id}.digest.json`, `No digest for ${id}.`,
  );
}

async function serveAudio(request, env, id) {
  if (!validBriefingId(id)) return json({ detail: "Invalid briefing id." }, 400);

  // Same order as KNOWN_AUDIO_FORMATS in app/synthesis/storage.py. save()
  // clears the superseded format once the replacement has landed, so only one
  // should ever exist.
  for (const ext of ["m4a", "wav"]) {
    const object = await env.BRIEFINGS.get(`${id}.${ext}`, {
      onlyIf: request.headers,
    });
    if (!object) continue;
    if (isNotModified(object)) return notModified(object);

    return new Response(object.body, {
      headers: {
        "content-type": AUDIO_TYPES[ext],
        // No explicit content-length: the runtime derives it from the R2
        // body. Setting it by hand only adds a way for the declared and
        // actual byte counts to disagree, which surfaces on the client as
        // an undiagnosable transport failure.
        "cache-control": CACHE_CONTROL,
        etag: object.httpEtag,
      },
    });
  }
  return json({ detail: `No audio for ${id}.` }, 404);
}
