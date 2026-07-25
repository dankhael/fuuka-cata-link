# YouTube residential proxy (Umbrel + Tailscale)

YouTube blocks the bot when it runs from a VPS because the VPS uses a **datacenter
IP**, which YouTube treats as a bot. The fix is to route *only the bot's YouTube
traffic* through a **residential IP** — your home internet — so YouTube sees a
normal household connection instead.

This guide sets up an always-on [Umbrel](https://umbrel.com/) box at home as a
small SOCKS5 proxy, links it to the VPS over a private [Tailscale](https://tailscale.com/)
tunnel, and points the bot at it. The proxy is **best-effort**: the bot checks
it is up before each YouTube download and goes direct when it isn't (see
[How the bot uses it](#how-the-bot-uses-it)), so it can never take the bot offline.

```
┌────────────────┐   Tailscale (encrypted,    ┌──────────────────┐
│  VPS (the bot) │   no open ports, works     │  Umbrel @ home   │
│  datacenter IP │◄──through CGNAT)──────────►│  SOCKS5 :1080    │──► YouTube
└────────────────┘                            └──────────────────┘   residential IP
        │                                              ▲
        └── YOUTUBE_PROXY=socks5h://…:1080 ────────────┘
```

> **Why Umbrel and not your desktop?** The proxy must stay up 24/7. A desktop
> that sleeps or reboots silently breaks the bot's YouTube path. Umbrel is
> always-on, low-power, and already runs Docker.

---

## Prerequisites

- An **Umbrel** machine on your home network (Raspberry Pi or x86 mini-PC), with
  SSH access enabled (Umbrel → Settings → Advanced → SSH).
- The **VPS** where the bot runs, with root/sudo access.
- A free **Tailscale** account (https://login.tailscale.com/start).
- ~10 minutes.

---

## Part 1 — Join both machines to one Tailscale tailnet

Tailscale gives each machine a stable `100.x.y.z` address that is reachable from
the other machine over an encrypted tunnel — **no router port-forwarding**, and it
works even if your home ISP uses CGNAT (both ends dial out).

### On the Umbrel

Umbrel ships Tailscale as a one-click app:

1. Open the Umbrel dashboard → **App Store** → search **Tailscale** → **Install**.
2. Open the Tailscale app, click **Log in**, and authenticate with your account.
3. Note the Umbrel's tailnet IP (shown in the app, e.g. `100.101.102.103`).

> No Tailscale app in your Umbrel store? SSH in and install the official client:
> `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`

### On the VPS

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Authenticate in the browser link it prints. Then confirm the VPS can see the
Umbrel:

```bash
tailscale status          # the Umbrel should be listed
ping -c3 100.101.102.103  # replace with your Umbrel's tailnet IP
```

If the ping succeeds, the tunnel is up.

---

## Part 2 — Run the SOCKS5 proxy on the Umbrel

SSH into the Umbrel and run a tiny SOCKS5 proxy in Docker. We protect it with a
username/password as defense-in-depth (the tailnet is already private, but auth
means a stray LAN device can't use it either):

```bash
docker run -d \
  --name youtube-proxy \
  --restart unless-stopped \
  -p 1080:1080 \
  -e PROXY_USER=botproxy \
  -e PROXY_PASSWORD='choose-a-long-random-password' \
  serjs/go-socks5-proxy
```

That's it — the proxy now listens on the Umbrel's port `1080`, reachable from the
VPS at `socks5://100.101.102.103:1080`.

> **Lock it down further (recommended):** in the Tailscale admin console
> (Access Controls) you can restrict who may reach the Umbrel's `:1080` to just
> the VPS. The SOCKS5 auth above is the simpler belt-and-suspenders option.

### Verify from the VPS

```bash
curl --socks5-hostname botproxy:'choose-a-long-random-password'@100.101.102.103:1080 \
  https://api.ipify.org
```

This should print **your home's public IP**, not the VPS IP. If it does, the
residential proxy works end-to-end.

---

## Part 3 — Point the bot at the proxy

Add one line to the bot's `.env` (on the VPS). Use `socks5h://` so DNS is also
resolved through the proxy — this avoids DNS leaks and ensures YouTube only ever
sees the residential side:

```dotenv
# Residential proxy for YouTube only (see docs/youtube-residential-proxy.md)
YOUTUBE_PROXY=socks5h://botproxy:choose-a-long-random-password@100.101.102.103:1080
```

Restart the bot:

```bash
docker compose up -d   # or: docker compose restart telegram-bot
```

### Docker networking note

The bot container does **not** need host networking or any special config. The
VPS host routes the `100.x.y.z` tailnet range via its `tailscale0` interface, and
Docker's default bridge NATs the container's outbound traffic through the host —
so the container reaches the Umbrel's tailnet IP automatically.

---

## How the bot uses it

The proxy is scoped to **YouTube only** — Twitter, Instagram, Reddit, etc. keep
going direct from the VPS, so they aren't slowed by the extra home-network hop.

The YouTube scraper ([`src/scrapers/youtube.py`](../src/scrapers/youtube.py))
**opens a TCP connection to the proxy before every YouTube download** and skips
it when that fails:

- **Proxy works** → YouTube sees your residential IP. 👍
- **Proxy down** (Umbrel reboot, Tailscale hiccup, container stopped) → the probe
  is refused, the bot logs `youtube_proxy_unreachable`, and the download runs
  directly instead.
- **Proxy up but the download through it fails** (wrong SOCKS password, proxy
  dies mid-transfer) → the bot logs `youtube_proxy_attempt_failed` and retries
  once directly.
- **`YOUTUBE_PROXY` unset** → behaves exactly as before, direct only.

This is why the proxy is an *optimization*, not a dependency.

> **Why probe instead of reading yt-dlp's error?** When the proxy host is down,
> yt-dlp reports only the underlying socket error — `[Errno 111] Connection
> refused` / `[WinError 10061] …`, in the OS's language, with no mention of a
> proxy. There is no error text to match on, so the socket is checked directly.
> The probe also spares yt-dlp its three connect retries against a dead host.

Both warnings log the proxy URL with its password masked
(`socks5h://botproxy:***@100.101.102.103:1080`), so `logs/errors.log` stays safe
to copy off the VPS.

---

## Verify it end-to-end

Share a YouTube link in an allowed chat and watch the logs:

```bash
docker compose logs -f telegram-bot
```

A successful extraction now egresses via home. To confirm the fallback works,
temporarily stop the proxy on the Umbrel (`docker stop youtube-proxy`) and share
another link — you should see a single `youtube_proxy_unreachable` warning
followed by a direct attempt. Start it again with `docker start youtube-proxy`.

Note that `docker stop` leaves the Umbrel host itself reachable, so the probe
gets an immediate connection *refused*. Pulling the Umbrel's network instead
makes the probe wait out its 3-second timeout before falling back — same
outcome, one slow request.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl … ipify` shows the **VPS** IP | proxy not actually used | Check the `--socks5-hostname` URL and that port `1080` is published on the Umbrel. |
| `youtube_proxy_unreachable` on every request | VPS can't reach the tailnet IP | `tailscale status` / `ping` the Umbrel from the VPS; ensure both are logged in to the **same** tailnet. |
| Proxy works but YouTube still bot-gates | residential IP also flagged, or needs a token | Add the `player_client=tv,web_safari` extractor arg or a PO-token provider (see the analysis notes). |
| Big videos are very slow | home **upload** bandwidth is the bottleneck | Expected — video flows YouTube → home → VPS. Fine for Shorts; large videos will crawl on slow uplinks. |
| Proxy dies after Umbrel reboot | container restart policy | The `--restart unless-stopped` flag handles this; re-run the `docker run` if you omitted it. |

---

## Notes & caveats

- **Bandwidth:** YouTube media is relayed YouTube → home → VPS → Telegram, so it
  consumes your home upload twice. Fine at the bot's typical volume (a few
  YouTube links/day); not suited to heavy traffic.
- **IP reputation:** your home IP becomes associated with the bot's YouTube
  requests. At personal volume the worst case is an occasional captcha when *you*
  browse YouTube. Don't crank up the request rate.
- **Security:** keep the SOCKS5 behind Tailscale and/or auth — never port-forward
  it to the public internet. An open SOCKS5 proxy *will* be found and abused.
- **ISP terms:** personal-use proxying of modest traffic is normal self-hosting;
  don't resell bandwidth.
