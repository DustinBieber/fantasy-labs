# Fantasy Labs sandbox (Mac mini)

The Mac mini runs your side-hustle projects during the day while you're at work. It's one isolated
stack you start and stop with a single command, and everything it writes lives in `~/sandbox-data`.

The first project is **Clipper**, an automated clipping service for fantasy football creators:

```
 find creators ──► pull new episodes ──► transcript ──► pick hot takes ──► cut 9:16 clips ──► you approve ──► post on schedule
 (YouTube search)   (their channel)      (free captions)  (Claude Haiku or    (subtitles, hook,   (dashboard on    (their YouTube Shorts,
                                                          free heuristics)    CTA, teaser reel)   your phone)       TikTok, Instagram Reels)
```

Two guardrails are built into the code:

- **Clips only post to accounts the creator connected themselves.** A creator sign-in (OAuth) is the only way an account gets
  added, and creators can disconnect from their own page at any time. Prospects (creators who haven't said yes) only get sample
  clips you can show them. Nothing posts for a prospect.
- **Every clip goes through your review queue.** Nothing posts until you tap Approve.

---

## 1. One-time setup (about 15 minutes)

On the Mac mini, paste this into Terminal (device-by-device steps are in [`SETUP.md`](SETUP.md)):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/DustinBieber/fantasy-labs/main/sandbox/bootstrap.sh)"
```

The installer sets up native mode, which runs under launchd with Apple's hardware encoder and MLX Whisper. It also turns on
hourly self-updates and starts the first pass. To set things up by hand, clone the repo to `~/fantasy-labs` and run
`sandbox/sandbox.sh setup native`, or `sandbox.sh setup` for the Docker sandbox.

**Docker mode** needs a Docker runtime: Docker Desktop (free for personal use and small businesses) or colima (free, open source).
OrbStack works too, but it needs a paid license for commercial use.

Setup creates `~/sandbox-data/clipper/` with:

| File | What it is |
|---|---|
| `config.yaml` | All settings. The Fantasy Footballers are already in it. |
| `.env` | Secrets: API keys and a generated dashboard password. Never commit it. |
| `clipper.db` | SQLite: leads, episodes, clips, posts, connected accounts (tokens encrypted). |
| `clips/`, `videos/`, `transcripts/`, `logs/` | Output. Source episodes are deleted after 3 days, clips are kept. |

If you set up by hand instead of using the installer, finish with:

```bash
open -e ~/sandbox-data/clipper/.env     # optional: ANTHROPIC_API_KEY (leave blank for $0 mode)
./sandbox.sh restart                    # Docker mode: ./sandbox.sh up
./sandbox.sh doctor                     # checks ffmpeg, fonts, keys, config
./sandbox.sh autoupdate on              # hourly self-update from GitHub
./sandbox.sh open                       # dashboard at http://localhost:8787
```

Keep the Mac mini awake on workdays (the installer offers this; by hand it's one time and asks for your password):

```bash
sudo pmset -c sleep 0 disksleep 0
sudo pmset repeat wakeorpoweron MTWRF 07:45:00
```

## 2. Reach the dashboard from your phone (free)

The dashboard only listens on the Mac mini itself (`127.0.0.1:8787`). To reach it from your phone, install
[Tailscale](https://tailscale.com) (free plan) on the Mac mini and your phone, then on the Mac mini:

```bash
tailscale serve --bg 8787               # private: https://<mac-mini>.<tailnet>.ts.net, only your devices can open it
```

Log in with `admin` and the `DASHBOARD_PASSWORD` from `.env`.

When you're ready for a creator to connect accounts, they need to reach the connect page, and the platforms need a public
HTTPS address for the OAuth redirect. Switch the same port to Funnel (also free):

```bash
tailscale funnel --bg 8787              # public: same https://<mac-mini>.<tailnet>.ts.net address
```

Put that URL in `.env` as `PUBLIC_BASE_URL=` and run `./sandbox.sh restart`. Funnel makes the dashboard reachable from the
internet behind the password, so keep the long generated one. Every dashboard form also carries a signed token, so other
websites can't submit actions for you. A Cloudflare named tunnel works too if you have a domain on Cloudflare.

## 3. What the worker does all day

During `worker.active_hours` (08:00–17:00 Mon–Fri, Hawaii time by default; change `worker.timezone` in `config.yaml`):

- **Every hour**, it checks each creator's channel for new uploads. Each episode is routed to a show by its title.
- **For each new episode**, it pulls YouTube's own captions, which are free and include word timing. It falls back to local Whisper
  if there are none. Then it picks 4 clips plus a teaser montage, downloads the episode, and renders 1080×1920 clips with
  karaoke-style captions, a hook headline and a "full episode" call to action.
- **Once a day**, it searches YouTube for new leads (see Leads below).
- **Approved clips** post in each account's next free slot (12:15 and 19:30 Eastern by default). Posting happens outside work
  hours too, so evening slots still go out.

Tap **Run now** on the Overview page to run a pass immediately, whatever the time.

## 4. The Fantasy Footballers (first creator)

Their DFS, betting and dynasty shows all post on the main channel, so the config splits episodes into four shows by title:

| Show | Routed when the title matches |
|---|---|
| `dfs` | DFS, DraftKings, FanDuel, Showdown, GPP, Cash Game |
| `betting` | Bet/Bets/Betting, Props, Parlays, Against the Spread, ATS, Odds |
| `dynasty` | Dynasty, Rookie(s), Devy, Keeper |
| `main` | everything else |

Each show can get its **own** TikTok, Instagram and YouTube accounts. That's the pitch: they have three podcasts without a
short-form home. Claude can also send an individual clip from the main show to the betting or dynasty account when that's
where it fits.

These patterns are a first guess. After the first sync, open **Episodes**, check the routing, fix any mistakes from the
dropdown, and tune `match:` in `config.yaml`.

The suggested flow:

1. They start as `status: prospect`. The worker processes their 2 latest episodes into sample clips. Review them, then download
   the best ones from the Review page to put in your pitch.
2. Once they agree, go to **Creators**, tap **Create connect link** and send it to them. They create or choose the accounts for
   each show and connect them by signing in. You never handle their passwords.
3. Set `status: client` in `config.yaml` and run `./sandbox.sh restart`. From then on every new episode is clipped, and whatever you approve gets posted.

## 5. Posting apps (one time, all free)

Creators connect through your own developer apps on each platform. Put the keys in `.env`. Each redirect URI is
`PUBLIC_BASE_URL` + the path shown.

**YouTube.** In the [Google Cloud console](https://console.cloud.google.com): create a project, then enable *YouTube Data API v3*.
Under *OAuth consent screen*, choose External and add the scopes `youtube.upload` and `youtube.readonly`. Under *Credentials*,
create an *OAuth client ID* of type Web application with the redirect `/oauth/youtube/callback`. That gives you
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
- While the app is in *Testing*, add the creator's Google account as a test user. Google expires testing refresh tokens after 7 days,
  so publish the app (it goes through Google verification) before relying on it.
- YouTube keeps uploads from unaudited API projects **private** until the project passes YouTube's API compliance audit. Request
  the audit in the Cloud console once you have a client. Until then, approved Shorts land as private videos the creator can flip to public.
- The default API quota allows only a handful of uploads a day per project. Check *Quotas* in the console and request more as you grow.

**TikTok.** At [developers.tiktok.com](https://developers.tiktok.com), create an app and add *Login Kit* and *Content Posting API*
(with Direct Post). Add the redirect `/oauth/tiktok/callback` and the scopes `user.info.basic`, `video.publish` and `video.upload`.
That gives you `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`.
- Until TikTok audits the app, direct posts are private (only visible to the creator). In the meantime, set `publish.tiktok_mode: inbox`
  so clips land in the creator's TikTok inbox and they publish each one with a tap.

**Instagram.** At [developers.facebook.com](https://developers.facebook.com), create an app and add *Instagram → API setup with
Instagram login*. Add the redirect `/oauth/instagram/callback` and the permissions `instagram_business_basic` and
`instagram_business_content_publish`. That gives you `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`.
- The creator's Instagram must be a professional (Creator or Business) account. In development mode, add them as an Instagram
  tester. Submit for App Review before onboarding creators at scale.

## 6. What it costs

| Piece | Starting setup | Cost |
|---|---|---|
| Transcripts | YouTube captions, local Whisper fallback | $0 |
| Clip picking | Claude Haiku 5.5 (`ai.tier: budget`) | roughly half a cent per hour-long episode |
| Rendering, scheduling, dashboard | the Mac mini | $0 |
| Remote access | Tailscale + Funnel | $0 |
| Posting APIs | YouTube / TikTok / Instagram developer apps | $0 |

`ai.monthly_budget_usd` (default $5) is a hard stop. Past it, the picker drops to the free heuristics until next month.
`ai.tier: free` never calls an API. `clipper spend` (or the Overview page) shows what you've used.

**Upgrade path** for when there's revenue:

| Upgrade | Change |
|---|---|
| Better clip picks | `ai.tier: standard` (Sonnet 5.5, ~$0.08/episode) or `premium` (Opus 5.5, ~$0.15/episode) |
| More accurate transcripts | `transcribe.engine: whisper`, `whisper_model: medium` (free, slower) |
| Faster rendering | native mode on Apple Silicon (hardware encoder + MLX Whisper) |
| Speaker-tracking reframe, paid transcription, scheduling at scale | not built yet; the code is laid out so each slots in as its own module |

## 7. Everyday commands

```bash
./sandbox.sh status          # is it running?
./sandbox.sh restart         # after editing config.yaml or .env
./sandbox.sh logs            # follow the log
./sandbox.sh cli sync        # check creators for new uploads now
./sandbox.sh cli process --limit 2
./sandbox.sh cli add-video "https://www.youtube.com/watch?v=..." --client fantasy-footballers --now
./sandbox.sh cli review      # list clips waiting for review
./sandbox.sh cli approve 12 14
./sandbox.sh cli discover    # find leads now
./sandbox.sh cli pitch UCxxxx
./sandbox.sh cli spend
./sandbox.sh update          # pull new code + latest yt-dlp, restart (also runs hourly on its own)
./sandbox.sh run-now         # start a pass now, even outside work hours
./sandbox.sh autoupdate off  # stop the hourly self-update
```

## 8. Leads and outreach

Discovery searches YouTube for long-form fantasy football videos using the `discovery.queries` list. For each channel it checks:
long-form video count, whether it has any Shorts, which TikTok and Instagram accounts it links (and how active they are), subscriber
count, and when it last uploaded. Leads scoring above `discovery.min_score` show up as **qualified**.

**Draft pitch** writes a short outreach email (Claude-personalized if a key is set, otherwise a template). It's only a draft:
nothing is ever sent automatically. When a creator says yes, add them under `clients:` in `config.yaml` with `status: prospect`
to cut samples, then `client` once they've connected.

## 9. Troubleshooting

- **`doctor` says ffmpeg can't burn in captions.** Since early 2026 Homebrew's plain `ffmpeg` is a slim build without libass.
  `./sandbox.sh setup native` installs `ffmpeg-full` for you; to fix by hand run `brew install ffmpeg-full`, then `./sandbox.sh native-install`.
- **`doctor` says the font is missing.** Captions fall back to a default font. For native mode run `brew install --cask font-montserrat`.
  For Docker, drop `.ttf` files in `~/sandbox-data/clipper/fonts/`.
- **Downloads or captions fail with "Sign in to confirm you're not a bot" or format errors.** YouTube changes often. Run
  `./sandbox.sh update` to get the newest yt-dlp. Keep the request rate polite: the defaults inspect at most 40 channels a day.
- **An episode failed.** The Episodes page shows the error. Tap Redo after fixing it.
- **A post failed.** The Posts page shows the platform's message. Expired tokens mean the creator should reconnect from their connect link.
- **A post shows "interrupted mid-upload".** The process stopped during an upload. Check the account first, then tap Retry if
  it didn't go through. It's never retried automatically, to avoid double posts.
