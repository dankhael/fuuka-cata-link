# YouTube cookies (account-authenticated scraping)

From a datacenter IP, YouTube answers most unauthenticated requests with
`Sign in to confirm you're not a bot`. Cookies from a logged-in account are the
quickest way through that gate. Use a **throwaway account** created for this —
yt-dlp warns that heavy automated use can get an account flagged, and the jar
gives full access to it.

The bot passes the jar to **both** yt-dlp calls it makes per link: the metadata
probe (`--dump-json`, duration/size pre-check) and the download. Until #27 the
probe ran without cookies, which is why every YouTube link failed at the gate
even with a valid jar mounted.

> Cookies expire and YouTube rotates them; expect to redo this every few weeks.
> The bot keeps up with the rotation on its own (see below) — what kills a jar is
> **anything else** using the same session: the browser it was exported from, or
> a manual `yt-dlp --cookies` test against a copy of the file.
> For a set-and-forget setup see [the PO token provider](youtube-po-token.md),
> which needs no cookies at all. The [residential proxy](youtube-residential-proxy.md)
> is complementary: it changes *where* the request comes from, cookies change
> *who* it is from.

## Export the jar (the way yt-dlp recommends)

YouTube invalidates a session's cookies whenever another request with them
arrives with a different state, so the export has to be the *last* thing that
session does:

1. Open a **private/incognito window** and log in to YouTube with the scraping
   account.
2. In that same window, open `https://www.youtube.com/robots.txt` — a page that
   makes no further requests, so nothing rotates the cookies after export.
3. Export cookies for `youtube.com` in Netscape format with a browser extension
   (e.g. "Get cookies.txt LOCALLY").
4. **Close the private window** without visiting anything else. Never use that
   session again.

The file must start with `# Netscape HTTP Cookie File` and contain rows for
`.youtube.com` — `LOGIN_INFO`, `SID`, `__Secure-1PSID`, `SAPISID` are the ones
that matter.

## Install on the VPS

```bash
scp cookies.txt vps:/root/fuuka-cata-link/cookies.txt
ssh vps 'cd /root/fuuka-cata-link && docker compose restart telegram-bot'
```

`.env` needs `COOKIES_FILE=/app/cookies.txt` and `docker-compose.yml` the
`./cookies.txt:/app/cookies.txt:ro` mount — the deploy workflow re-enables the
mount on every push, so a restart is enough after replacing the file.

## How rotation is handled (and why you must not test with the jar)

Google rotates `__Secure-1PSIDTS` / `__Secure-3PSIDTS` on every request and
soon rejects the previous values. yt-dlp saves the rotated cookies into the
`--cookies` file it was given, so the bot keeps a **live jar** at
`$COOKIES_STATE_DIR/cookies.txt.live` (inside the persistent volume): each run
works on a private copy of it and merges the rotation back afterwards
(`src/utils/cookie_jar.py`). The mounted `cookies.txt` is never written to.

Consequences:

- A fresh export only needs to land on the mount with a newer mtime — the live
  jar is re-seeded from it on the next run. `scp` (without `-p`) does that.
- **Don't run `yt-dlp --cookies` by hand against a copy of the jar** to "check"
  it. That copy rotates the session and the live jar is left holding dead
  values. Check the bot's own logs instead (`media_extracted platform=youtube`).
- The verification command below is the one exception: run it **once**, right
  after installing a fresh export, and copy the jar back over the mount if you
  do (`docker cp` the rotated file out), or simply re-export.

## Verify

```bash
ssh vps 'docker exec fuuka-cata-link-bot yt-dlp --dump-json --no-download \
  --cookies /app/cookies.txt --js-runtimes deno --remote-components ejs:github \
  https://youtu.be/dQw4w9WgXcQ | head -c 200'
```

A JSON blob means the jar works. These two mean it doesn't:

- `The provided YouTube account cookies are no longer valid. They have likely
  been rotated` — the session was reused after export (or expired). Redo the
  export, including closing the window.
- `Sign in to confirm you're not a bot` — no YouTube cookies reached yt-dlp.
  Check `COOKIES_FILE` in `.env` and that the file has `.youtube.com` rows.

In the bot logs a working jar shows as `media_extracted platform=youtube`;
a dead one as `youtube_probe_failed` with one of the messages above.

## Keeping other platforms' cookies in the same file

The jar is shared with Instagram and Facebook. To refresh only the YouTube rows,
export from a window logged into all three, or concatenate: the Netscape format
is line-based, so appending a `youtube.com`-only export to the existing file
works as long as there are no duplicate names for the same domain.
