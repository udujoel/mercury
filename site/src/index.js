/**
 * Mercury's public pages: a home page and a privacy policy.
 *
 * These exist for one reason. Google will not publish an OAuth consent screen
 * out of Testing mode without both, on a domain the developer has verified —
 * and Testing mode expires refresh tokens after seven days, which is what
 * silently broke the morning run for three days running.
 *
 * Every claim in the privacy policy below is checked against the code rather
 * than written from a template. Where the two ever disagree, the code is the
 * thing that is true and this page is the bug.
 */

const UPDATED = "2 September 2026";
const CONTACT = "udujoel@gmail.com";

/**
 * The visual register follows ADR-012: paper, not glass. A system font stack
 * rather than a webfont, so the page has no third-party dependency at all —
 * which is a claim the privacy policy makes, and a Google Fonts request would
 * have quietly made it false.
 */
const STYLE = `
:root{--paper:#FBFAF8;--raised:#FFF;--rule:#E6E3DE;--ink:#191A1F;--muted:#5A5B66;--faint:#8A8B96;--accent:#8E3F51}
@media (prefers-color-scheme:dark){:root{--paper:#111114;--raised:#1A1A1F;--rule:#2C2C33;--ink:#F2F1EE;--muted:#A8A8B2;--faint:#77777F;--accent:#D98BA0}}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);margin:0;padding:clamp(28px,6vw,72px) clamp(20px,6vw,32px) 96px;
 font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:660px;margin:0 auto}
h1{font-family:ui-serif,Georgia,"Times New Roman",serif;font-weight:400;font-size:clamp(34px,6vw,46px);
 line-height:1.08;letter-spacing:-.015em;margin:0 0 8px;text-wrap:balance}
h2{font-family:ui-serif,Georgia,serif;font-weight:400;font-size:22px;line-height:1.25;margin:40px 0 12px;
 padding-top:20px;border-top:1px solid var(--rule)}
p,li{color:var(--muted);margin:0 0 14px}
strong{color:var(--ink);font-weight:600}
.eyebrow{font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0 0 18px}
.lede{font-size:18px;color:var(--ink);margin-bottom:28px}
ul{padding-left:20px;margin:0 0 14px}
a{color:var(--accent)}
.foot{margin-top:56px;padding-top:20px;border-top:1px solid var(--rule);font-size:13.5px;color:var(--faint)}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--raised);border:1px solid var(--rule);
 padding:1px 5px;border-radius:3px;color:var(--ink)}
`;

const page = (title, body) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><style>${STYLE}</style></head>
<body><div class="wrap">${body}
<p class="foot">Mercury is a personal project by Joel Udu. Questions: <a href="mailto:${CONTACT}">${CONTACT}</a>.<br>
<a href="/mercury">Home</a> &middot; <a href="/mercury/privacy">Privacy</a></p>
</div></body></html>`;

const HOME = page(
  "Mercury — your morning briefing",
  `<p class="eyebrow">Personal project</p>
<h1>One briefing, every morning, before you are awake.</h1>
<p class="lede">Mercury reads the day ahead overnight and turns it into a short conversation between two hosts. You press play once and hear what matters.</p>

<h2>What it does</h2>
<p>Every night it looks at the things that would otherwise be twenty minutes of scrolling: what is on your calendar, what arrived in your inbox, and what the news and newsletters you follow are covering. It works out which of those are the same story, decides what is worth saying, writes a script, and narrates it.</p>
<p>By the time you wake up the audio is finished and waiting. There is no feed to scroll and nothing to triage.</p>

<h2>How it is built</h2>
<p>Generation happens on a server overnight, because it takes minutes and a phone cannot do that in the background. The app on the phone is a player: it downloads the finished audio and a timing file, and plays them.</p>
<p>It reads Google data with <strong>read-only</strong> permissions. It is structurally incapable of sending, deleting, or changing anything in your mail or your calendar.</p>

<h2>Who it is for</h2>
<p>One person. Mercury is a personal project built for its author's own mornings, not a product with users or a sign-up. It is published here because Google requires a home page and a privacy policy for any app that reads Gmail, including one with a single user.</p>
<p>Read the <a href="/mercury/privacy">privacy policy</a> for exactly what it touches and what it keeps.</p>`
);

const PRIVACY = page(
  "Mercury — privacy policy",
  `<p class="eyebrow">Updated ${UPDATED}</p>
<h1>Privacy policy</h1>
<p class="lede">Mercury is used by one person: its author. It reads that person's own Gmail and Google Calendar to produce their own morning briefing. Nothing is sold, shared, or used for advertising, and there are no other users to share it with.</p>

<h2>What it accesses</h2>
<p>Two Google scopes, both read-only:</p>
<ul>
<li><code>gmail.readonly</code> — to see which messages arrived overnight and read newsletters</li>
<li><code>calendar.readonly</code> — to see the day's events</li>
</ul>
<p><strong>Neither scope permits writing.</strong> Mercury cannot send, reply to, delete, label, or modify a message or an event. That is not a policy choice that could be reversed in a later version; it is the permission the application asks for, and Google enforces it.</p>

<h2>What is kept, and what is not</h2>
<p><strong>Raw email bodies are never stored.</strong> They are held in memory for the two to four minutes a briefing takes to generate, used to write the script, and discarded before anything is saved. There is no database of your mail.</p>
<p>What is saved is what the briefing is made of:</p>
<ul>
<li>the narrated audio file</li>
<li>the script the hosts read, and the timings that let the app follow along</li>
<li>source references — a sender's name, a subject line, a link — so the app can show where something came from</li>
</ul>
<p>These are stored in a private Cloudflare R2 bucket that is not publicly readable, and are <strong>deleted automatically after 30 days</strong>.</p>

<h2>Who else sees it</h2>
<p>Producing a briefing means sending the day's material to two Google services, under Google's own terms:</p>
<ul>
<li><strong>Gemini</strong>, which writes the script</li>
<li><strong>Google Cloud Text-to-Speech</strong>, which narrates it</li>
</ul>
<p>News articles are fetched from publishers' public RSS feeds. No personal data is sent to them — the request is the same one any reader's browser makes.</p>
<p>Beyond that, nothing. There is no analytics, no advertising, no tracking, and no third party receives your data. This page itself loads no external fonts, scripts, or images, and sets no cookies.</p>

<h2>Where it runs</h2>
<p>Generation runs as a scheduled job on GitHub Actions. Storage and delivery are Cloudflare R2 and Cloudflare Workers.</p>
<p>Reading a finished briefing requires a bearer token. That token is held in three places, all controlled by the author: the app on their phone, an encrypted GitHub Actions secret used by the nightly job, and an encrypted Cloudflare Worker secret used to check incoming requests. Nobody else holds it, and without it the stored briefings cannot be read — the R2 bucket is not publicly reachable.</p>

<h2>Google API Services user data</h2>
<p>Mercury's use of information received from Google APIs adheres to the <a href="https://developers.google.com/terms/api-services-user-data-policy" rel="noopener">Google API Services User Data Policy</a>, including its Limited Use requirements. Specifically: data from Google APIs is used only to generate the user's own briefing, is never transferred to others except as required to provide that feature, is never used for advertising, and is never read by a human other than the account owner.</p>

<h2>Removing your data</h2>
<p>Access can be withdrawn at any time from <a href="https://myaccount.google.com/permissions" rel="noopener">your Google account permissions page</a>, which immediately stops all further reading. Stored briefings expire within 30 days on their own, and can be deleted sooner on request to ${CONTACT}.</p>

<h2>Changes</h2>
<p>If what the software does changes, this page changes with it. The date at the top is the last time that happened.</p>`
);

export default {
  async fetch(request) {
    const { pathname } = new URL(request.url);

    // Read-only by construction: this Worker serves two constants and has no
    // bindings, no storage and no secrets. Anything but a GET is refused
    // rather than silently treated as one.
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("This page is read-only.", {
        status: 405,
        headers: { allow: "GET, HEAD", "content-type": "text/plain; charset=utf-8" },
      });
    }

    // Trailing slashes are normalised so /mercury/ and /mercury are the same
    // page — Google stores whichever URL is pasted into the console, and a
    // 404 there would fail review for a reason nobody would think to check.
    const path = pathname.replace(/\/+$/, "") || "/";

    const html =
      path === "/mercury/privacy" ? PRIVACY : path === "/mercury" ? HOME : null;

    if (html === null) {
      return new Response("Not found.", {
        status: 404,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    return new Response(html, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        // Public and cacheable: unlike mercury-briefings, there is nothing
        // here that belongs to anyone.
        "cache-control": "public, max-age=3600",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
      },
    });
  },
};
