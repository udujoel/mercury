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
 */

const AUDIO_TYPES = {
  m4a: "audio/mp4",
  wav: "audio/wav",
};

// Same header the FastAPI backend uses, so the client is unchanged.
const TOKEN_HEADER = "X-Mercury-Token";

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
    if (!tokensMatch(request.headers.get(TOKEN_HEADER) || "", env.MERCURY_API_TOKEN)) {
      return json({ detail: `Missing or invalid ${TOKEN_HEADER}.` }, 401);
    }

    const audio = url.pathname.match(/^\/briefing\/([^/]+)\/audio$/);
    if (audio) return serveAudio(env, decodeURIComponent(audio[1]));

    const manifest = url.pathname.match(/^\/briefing\/([^/]+)$/);
    if (manifest) return serveManifest(env, decodeURIComponent(manifest[1]));

    return json({ detail: "Not found." }, 404);
  },
};

async function serveManifest(env, id) {
  if (!validBriefingId(id)) return json({ detail: "Invalid briefing id." }, 400);

  const object = await env.BRIEFINGS.get(`${id}.manifest.json`);
  if (!object) return json({ detail: `No briefing for ${id}.` }, 404);

  return new Response(object.body, {
    headers: {
      "content-type": "application/json",
      // PRIVATE, not public. This response is returned only after a token
      // check and its body is the transcript of the user's email and
      // calendar. "public" would authorise shared caches — Cloudflare's own
      // edge, any proxy in between — to store and re-serve it, which is at
      // odds with the entire reason the bucket is private. "private" keeps
      // the client-side caching benefit, the only one actually wanted.
      "cache-control": "private, max-age=3600",
      etag: object.httpEtag,
    },
  });
}

async function serveAudio(env, id) {
  if (!validBriefingId(id)) return json({ detail: "Invalid briefing id." }, 400);

  // Same order as KNOWN_AUDIO_FORMATS in app/synthesis/storage.py. A forced
  // regeneration deletes every format first, so only one should ever exist.
  for (const ext of ["m4a", "wav"]) {
    const object = await env.BRIEFINGS.get(`${id}.${ext}`);
    if (object) {
      return new Response(object.body, {
        headers: {
          "content-type": AUDIO_TYPES[ext],
          // No explicit content-length: the runtime derives it from the R2
          // body. Setting it by hand only adds a way for the declared and
          // actual byte counts to disagree, which surfaces on the client as
          // an undiagnosable transport failure.
          "cache-control": "private, max-age=3600",
          etag: object.httpEtag,
        },
      });
    }
  }
  return json({ detail: `No audio for ${id}.` }, 404);
}
