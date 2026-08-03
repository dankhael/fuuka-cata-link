# YouTube PO tokens (self-refreshing, no cookie babysitting)

YouTube increasingly requires a **Proof-of-Origin (PO) token** on video requests.
Without one, the affected clients get `HTTP Error 403`, `Sign in to confirm you're
not a bot`, or silently reduced format availability — the failure modes this bot
has been papering over with cookies and a residential proxy.

The fix is **not** a token you paste into `.env`. Tokens are bound to the video ID
and expire, so a static token is worthless within hours. The fix is a small
**token provider** running next to the bot that mints a fresh token per request.
Set it up once and there is nothing left to refresh — no cookie exports, no
manual token rotation.

```
┌───────────────────────────────┐          ┌────────────────────────────┐
│ telegram-bot (this repo)      │  HTTP    │ bgutil-provider (sidecar)  │
│   yt-dlp subprocess           │  POST    │   BotGuard via BgUtils     │
│   + bgutil plugin  ───────────┼─────────►│   :4416 — mints a PO token │
└───────────────┬───────────────┘          └─────────────┬──────────────┘
                │ media                                  │ attestation
                │ (via YOUTUBE_PROXY)                    │ (same proxy)
                ▼                                        ▼
             YouTube                                  YouTube
```

> **This is complementary to [the residential proxy](youtube-residential-proxy.md),
> not a replacement.** The proxy fixes *where* the request comes from; the PO token
> fixes *what the request proves about itself*. Heavily flagged IPs usually need both.

---

## Background: why cookies keep breaking and a token won't

| Approach | Refresh burden | Verdict |
|---|---|---|
| Account cookies (`COOKIES_FILE`) | YouTube rotates cookies on any open tab; exports go stale in days, and the account risks a ban | Only for private/age-restricted content |
| Static `po_token` extractor arg | Token is bound to a **video ID** and may live as little as 12h | Dead end — upstream calls manual extraction "no longer recommended" |
| **PO token provider plugin** | **None.** A token is generated per request, on demand | **Recommended by yt-dlp upstream** |

A PO token is produced by an attestation engine (BotGuard on web, DroidGuard on
Android, iOSGuard on iOS) to prove the request came from a genuine client. yt-dlp
**cannot generate one itself** — it needs far more of a JS/browser environment than
its extractor has, which is why the answer is an out-of-process provider. The
`--remote-components ejs:github` / `--js-runtimes deno` this repo already passes
solves a *different* problem (the n-signature JS challenge); it does not produce
PO tokens.

Three contexts need tokens, and which ones apply depends on the player client:

- **GVS** — Google Video Server requests (the actual media transfer: https, dash, hls)
- **Player** — Innertube `player` calls that return format URLs
- **Subs** — subtitle requests

| Client | PO token required for |
|---|---|
| `web` | Subs, GVS |
| `web_safari` | GVS (its HLS formats currently exempt) |
| `mweb`, `tv_simply`, `web_music`, `web_creator` | GVS |
| `android`, `ios` | GVS or Player |
| `visionos`, `android_vr`, `tv`, `web_embedded` | not required |

yt-dlp's default client set is `visionos,android_vr,web`, deliberately weighted
toward clients that *don't* need a token — which is exactly why the bot currently
lands on degraded or bot-gated paths. Give it a provider and the full-fat clients
open up.

---

## Prerequisites

- yt-dlp **≥ 2025.05.22** (this repo builds far newer — `2026.03.17` locally).
- Docker Compose on the VPS (already the deployment model here).
- ~5 minutes. No YouTube account, no browser, no cookies.

---

## Part 1 — Run the provider as a compose sidecar

Add to [`docker-compose.yml`](../docker-compose.yml):

```yaml
services:
  telegram-bot:
    # ...existing config...
    depends_on:
      - bgutil-provider

  bgutil-provider:
    image: brainicism/bgutil-ytdlp-pot-provider:1.3.1
    container_name: fuuka-bgutil-provider
    restart: unless-stopped
    init: true
```

Notes:

- **Pin the tag.** The plugin warns when its version and the server's disagree;
  pinning both to `1.3.1` keeps them in lockstep. `:latest` is the Node.js flavor,
  `:deno` the Deno one — either is fine.
- **No `ports:` needed.** Only the bot talks to it, over the compose network, as
  `http://bgutil-provider:4416`. Publishing 4416 to the host is an unnecessary
  attack surface — this service will happily mint tokens for anyone who asks.
- `init: true` gives it a proper PID 1 so the Node process reaps its children.

Start it:

```bash
docker compose up -d bgutil-provider
```

## Part 2 — Install the plugin into the bot image

The plugin lives *in the bot container*, alongside yt-dlp — it is what hooks into
yt-dlp's PO Token Provider framework and calls the sidecar. Add to the
[`Dockerfile`](../Dockerfile), right after the existing `pip install`:

```dockerfile
RUN pip install --no-cache-dir .

# PO token provider plugin (see docs/youtube-po-token.md). Installed into the same
# environment as yt-dlp so it is autoloaded from the yt_dlp_plugins namespace.
RUN pip install --no-cache-dir bgutil-ytdlp-pot-provider==1.3.1
```

Rebuild: `docker compose up -d --build telegram-bot`.

> Because this bot shells out to the `yt-dlp` **binary**, the plugin only works if
> it is installed into the same Python environment that provides that binary. It is
> — both come from the image's site-packages.

## Part 3 — Point yt-dlp at the sidecar

⚠️ **This part is not implemented in the code yet.** The plugin auto-detects a
provider at `http://127.0.0.1:4416`, but our provider is a separate container, so
its URL has to be passed explicitly.

### Option A — a setting (recommended, matches repo conventions)

In [`src/config.py`](../src/config.py):

```python
    # PO token provider (bgutil sidecar) for YouTube; see docs/youtube-po-token.md.
    # Unset = no PO tokens, and yt-dlp falls back to clients that don't need one.
    ytdlp_pot_base_url: str | None = None
```

In [`src/utils/ytdlp.py`](../src/utils/ytdlp.py), alongside `_fast_fail_args()`:

```python
def _pot_provider_args() -> list[str]:
    """Extractor args pointing the bgutil plugin at our provider sidecar.

    The plugin only auto-detects a provider on localhost; ours runs as a separate
    compose service. The arg is namespaced to the provider, so it is inert for
    every non-YouTube extractor.
    """
    if not settings.ytdlp_pot_base_url:
        return []
    return [
        "--extractor-args",
        f"youtubepot-bgutilhttp:base_url={settings.ytdlp_pot_base_url}",
    ]
```

…then `cmd.extend(_pot_provider_args())` in **both** `ytdlp_info()` and
`ytdlp_download()` — the metadata probe is bot-gated the same way the download is,
and [`youtube.py`](../src/scrapers/youtube.py) skips the video outright when the
probe fails, so a probe without a token silently costs you videos.

And in `.env` / [`.env.example`](../.env.example):

```dotenv
# PO token provider sidecar — see docs/youtube-po-token.md
YTDLP_POT_BASE_URL=http://bgutil-provider:4416
```

### Option B — no code change

yt-dlp reads `/etc/yt-dlp.conf` on every invocation. Bake it into the image:

```dockerfile
RUN echo '--extractor-args "youtubepot-bgutilhttp:base_url=http://bgutil-provider:4416"' \
    > /etc/yt-dlp.conf
```

Works today with zero Python changes, at the cost of being invisible from the
codebase — a future reader has no way to know yt-dlp is being configured behind
their back. Prefer Option A unless you want to test before committing to it.

## Part 4 — Stop feeding YouTube cookies (optional but the point of all this)

Once tokens are flowing, YouTube cookies stop being load-bearing for ordinary
public videos. Dropping them removes the recurring "re-export cookies.txt" chore
*and* the ban risk of pointing an account at a bot.

`_download_with_proxy_fallback()` in [`src/scrapers/youtube.py`](../src/scrapers/youtube.py#L107-L109)
passes the global `settings.cookies_file`, which is shared with Facebook/Instagram.
To take YouTube off cookies without disturbing them:

```python
    async def _download_with_proxy_fallback(self, url: str) -> YtdlpResult:
        # No cookies for YouTube: the PO token provider covers the bot-gate, and
        # account cookies rotate constantly + carry a ban risk (docs/youtube-po-token.md).
        return await self._with_proxy_fallback(url, ytdlp_download)
```

Keep cookies if you actually need **age-restricted, private, or members-only**
videos — a PO token proves the client is genuine, never that it is *you*.

---

## Verify

Provider reachable from the bot container:

```bash
docker compose exec telegram-bot curl -s http://bgutil-provider:4416/ping
```

Plugin loaded, and a token actually minted:

```bash
docker compose exec telegram-bot yt-dlp -v --skip-download \
  --extractor-args "youtube:pot_trace=true" \
  --extractor-args "youtubepot-bgutilhttp:base_url=http://bgutil-provider:4416" \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Look for these lines:

```
[debug] [youtube] [pot] PO Token Providers: bgutil:http-1.3.1 (external), ...
[debug] [youtube] [pot] Fetching GVS PO Token for web client
[debug] [youtube] [pot] Retrieved a GVS PO Token for web client from "bgutil:http" provider
```

`PO Token Providers:` listing `bgutil:http` proves **Part 2**; a `Retrieved a …
PO Token` line proves **Parts 1 and 3**. If the provider is listed as
`(unavailable)`, the plugin is installed but can't reach the server.

Then share a YouTube link in an allowed chat and watch `docker compose logs -f
telegram-bot` as usual.

---

## How it interacts with `YOUTUBE_PROXY`

The plugin forwards yt-dlp's `--proxy` (and source address) to the provider in the
token request, and the provider runs its attestation through that same proxy —
including `socks5h://`. So the token is minted from the **same residential egress**
the media download uses, which is what you want: a token attested from one network
and spent from another is exactly the mismatch YouTube looks for.

Two consequences:

- The **provider container must be able to reach your proxy**. For a Tailscale
  `100.x.y.z` address this is automatic (the host routes the tailnet range and
  Docker NATs the container out through it) — same reasoning as the bot container
  in [the proxy doc](youtube-residential-proxy.md#docker-networking-note). Only a
  proxy bound to `127.0.0.1` *on the host* would need `network_mode: host`.
- The plugin's own hop to `bgutil-provider:4416` deliberately **bypasses** the
  proxy, so the sidecar stays reachable even when the proxy is down.

Failures are soft, matching the proxy's best-effort contract: if the provider is
unreachable, yt-dlp logs a warning and continues without a token rather than
aborting. A dead sidecar degrades YouTube extraction; it cannot take the bot down.

---

## What this fixes — and what it doesn't

**Fixes:** `HTTP Error 403` on format URLs, most `Sign in to confirm you're not a
bot` gates, formats that silently vanish on token-requiring clients, and the
endless cookie-refresh loop.

**Doesn't fix:**

- **Age-restricted / private / members-only** videos — needs account cookies.
- **Rate limiting** (`This content isn't available, try again later`). Guest
  sessions are capped around ~300 videos/hour; upstream suggests 5–10s between
  downloads. Not a concern at this bot's volume.
- **A thoroughly burned IP.** Upstream is explicit: a PO token "does not guarantee
  bypassing 403 errors or bot checks, but it *may* help your traffic seem more
  legitimate." Pair it with the residential proxy.

If bot-gating survives both, the documented escalation is to force the mobile-web
client, which upstream's TL;DR recommends specifically in combination with a token:

```
--extractor-args "youtube:player_client=default,mweb"
```

---

## Maintenance

There is no token or cookie to rotate — that is the entire point. What remains:

- **Keep the two versions matched.** Bump the compose image tag and the pip pin
  together; a mismatch logs a version-mismatch warning.
- **Update occasionally.** BotGuard changes; provider releases follow it. If PO
  token errors reappear after months of quiet, update the sidecar and plugin first.
- **Watch the first-call latency.** Token generation costs roughly a second on a
  warm server (the sidecar caches per session); the plugin gives up after 20s,
  well inside this bot's 90s `YTDLP_TIMEOUT_SECONDS` ceiling. Keep server mode —
  the alternative script mode spawns a Node process per call and is explicitly not
  recommended for concurrent use.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No `[pot] PO Token Providers:` line at all | Plugin not installed, or installed in a different environment than the `yt-dlp` binary | Rebuild the image with the Part 2 `pip install`; confirm with `docker compose exec telegram-bot pip show bgutil-ytdlp-pot-provider` |
| Provider listed as `(unavailable)`, or "failed to fetch" warnings | Wrong `base_url`, sidecar down, or not on the same compose network | `docker compose ps bgutil-provider`; `docker compose exec telegram-bot curl -s http://bgutil-provider:4416/ping` |
| Works via CLI, not from the bot | Part 3 not applied — the bot's yt-dlp calls never pass `base_url` | Apply Option A (or B) |
| Version mismatch warning | Image tag and pip pin drifted | Set both to the same release |
| Tokens fetched but still bot-gated | IP reputation, not attestation | Enable `YOUTUBE_PROXY`; then try `player_client=default,mweb` |
| `Sign in to confirm your age` | Not a PO token problem | Supply account cookies for those videos |
| Provider container restarts / OOMs | BotGuard's JS is memory-hungry on tiny VPS instances | Give the VPS swap, or run the `:deno` flavor |

---

## References

- [PO Token Guide — yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [YouTube extractor notes — yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) (maintained by a yt-dlp maintainer)
- [yt-dlp-getpot-wpc](https://github.com/coletdjnz/yt-dlp-getpot-wpc) — browser-based alternative provider, useful as a fallback
- [PO Token Provider framework docs](https://github.com/yt-dlp/yt-dlp/tree/master/yt_dlp/extractor/youtube/pot/README.md) (for writing your own)
