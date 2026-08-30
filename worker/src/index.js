/**
 * Mercury read Worker — serves briefings from a private R2 bucket.
 *
 * The iOS client speaks FastAPI routes (`/briefing/{id}`,
 * `/briefing/{id}/audio`) while R2 stores flat object keys
 * (`{id}.manifest.json`, `{id}.m4a`). This translates between them so the
 * client needs no changes — only a different API_HOST.
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
// UTC and every checkpoint is sized against it; winter gets an extra hour of
// margin for free. Cron is UTC-only and has no DST, here or on GitHub.
const DEADLINE_UTC_MINUTES = 5 * 60;

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

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
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
 * Reject anything that is not a plain YYYY-MM-DD. The id becomes an R2 key,
 * so this also stops path traversal ("../other-bucket-object") reaching the
 * bucket at all.
 */
function validBriefingId(id) {
  return /^\d{4}-\d{2}-\d{2}$/.test(id);
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
        timezone: env.USER_TIMEZONE || "UTC",
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
      const id = briefingIdFor(new Date(), env.USER_TIMEZONE || "UTC");
      const state = await briefingState(env, id);
      return json({ briefing_id: id, state, ok: state === "ok" });
    }

    const audio = url.pathname.match(/^\/briefing\/([^/]+)\/audio$/);
    if (audio) {
      const id = safeDecode(audio[1]);
      if (id === null) return json({ detail: "Invalid briefing id." }, 400);
      return serveAudio(request, env, id);
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
    const id = briefingIdFor(scheduledAt, env.USER_TIMEZONE || "UTC");
    const state = await briefingState(env, id);

    if (state === "ok") {
      console.log(`briefing ${id}: already in R2, nothing to do`);
      return;
    }

    const pastDeadline =
      scheduledAt.getUTCHours() * 60 + scheduledAt.getUTCMinutes() >=
      DEADLINE_UTC_MINUTES;
    console.log(
      `briefing ${id}: ${state} at ${scheduledAt.toISOString()} ` +
        `(past deadline: ${pastDeadline})`,
    );

    // An EMPTY briefing must be forced. Generation is idempotent, so a stored
    // manifest — even one with zero turns and zero milliseconds of audio —
    // makes every later run decline to replace it. That is how a single
    // total TTS failure turned into a briefing that was broken permanently
    // rather than for one morning.
    const inputs = state === "empty" ? { force: "true" } : {};
    await dispatchWorkflow(env, GENERATE_WORKFLOW, inputs);

    if (pastDeadline) {
      // Generating anyway is still worth it — the user wakes at 07:00. But
      // the deadline is blown, and the whole point of ADR-010 is that they
      // are told rather than opening the app to yesterday's audio.
      await dispatchWorkflow(env, WATCHDOG_WORKFLOW, { briefing_id: id });
    }
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
  for (const ext of ["m4a", "wav"]) {
    if (await env.BRIEFINGS.head(`${id}.${ext}`)) return "ok";
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

  const response = await fetch(
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

async function serveManifest(request, env, id) {
  if (!validBriefingId(id)) return json({ detail: "Invalid briefing id." }, 400);

  // Handing R2 the request headers lets it evaluate If-None-Match itself,
  // rather than this code hand-parsing etag lists and weak validators.
  const object = await env.BRIEFINGS.get(`${id}.manifest.json`, {
    onlyIf: request.headers,
  });
  if (!object) return json({ detail: `No briefing for ${id}.` }, 404);
  if (isNotModified(object)) return notModified(object);

  return new Response(object.body, {
    headers: {
      "content-type": "application/json",
      "cache-control": CACHE_CONTROL,
      etag: object.httpEtag,
    },
  });
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
