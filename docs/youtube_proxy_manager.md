# YouTube proxy and PO-token operations

## Why this exists

YouTube currently challenges ApexLoad's datacenter IP with `LOGIN_REQUIRED` / "Sign in to confirm you're not a bot." Changing yt-dlp player clients, Deno, PO tokens, or WARP does not change a challenged source IP. ApexLoad therefore uses validated anonymous SOCKS5 routes for YouTube and YouTube Shorts only.

Instagram, Facebook, TikTok, X/Twitter, Snapchat, Pinterest, Reddit, and all other platforms continue to use their existing direct path. The application does not set `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY`.

## Request flow

1. After the supervised BgUtils readiness check, FastAPI starts a non-blocking background pool maintainer. It validates the configured pre-warm URL until the target pool is ready.
2. A normal YouTube or YouTube Shorts analyze/download asks the same in-process manager for a route.
3. A recently successful, non-cooled-down proxy is selected using recency, latency, and failure history. Selection rotates modestly among the best three routes.
4. If no cached route exists, one caller performs discovery while concurrent callers wait (single-flight).
5. Proxy entries are fetched only from configured, allow-listed HTTPS source hosts, normalized, deduplicated, and rejected unless they are public IPv4 SOCKS5 endpoints.
6. Bounded workers test basic egress through `api.iplocate.io`, then a smaller bounded pool runs yt-dlp metadata extraction for the requested/pre-warm YouTube URL.
7. A proxy is healthy only when yt-dlp returns usable media formats with the `mweb` client and BgUtils PO-token provider.
8. The selected proxy is locked to the complete yt-dlp download invocation, including extraction, streams, fragments, and FFmpeg merge.
9. A proxy/network/anti-bot failure degrades the route, deletes files from that failed attempt, selects a different route, and restarts with the original format selection. Attempts are bounded.

The maintainer wakes when health falls below `YOUTUBE_PROXY_POOL_TARGET`, otherwise checks only at the configured maintenance interval. It revalidates shortly before cache TTL expiry so a user request is not responsible for an expired pool refresh. Failed refreshes use exponential backoff up to the configured maximum. Proxy-source responses retain their separate TTL cache. FastAPI startup waits only for the configured bounded readiness window, then continues even if public proxies are unavailable; shutdown signals and joins the maintainer.

The cache is intentionally in memory because the current ApexLoad job queue is also in memory. Each backend replica has its own single-flight lock and health pool. Move both job and proxy state to Redis together before horizontally scaling heavily.

## Sources and security model

Built-in sources:

- IPLocate SOCKS5 list: `raw.githubusercontent.com/iplocate/free-proxy-list`
- Proxifly SOCKS5 list via jsDelivr: `cdn.jsdelivr.net/gh/proxifly/free-proxy-list`

Only HTTPS source URLs whose hostname appears in `YOUTUBE_PROXY_SOURCE_ALLOWED_HOSTS` are fetched. Proxy-list data is never supplied by API users. Entries with credentials, hostnames, IPv6, invalid ports, loopback, private, link-local, reserved, or other non-global addresses are rejected.

Public proxies are untrusted. ApexLoad removes `cookiefile` and `cookiesfrombrowser` from every proxied yt-dlp invocation. Never route authenticated YouTube content through this subsystem. TLS verification remains enabled, and no proxy certificate is installed. Internal headers, API keys, user credentials, database traffic, and non-YouTube requests never enter this path. Logs use an opaque correlation ID instead of the full proxy address.

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `YOUTUBE_PROXY_ENABLED` | `true` | Enables the YouTube-only manager. Set `false` for the legacy direct path. |
| `YOUTUBE_PROXY_DIRECT_FIRST` | `false` | Tries direct YouTube egress once before proxies. Keep false on the challenged server. |
| `YOUTUBE_PROXY_MAX_JOB_ATTEMPTS` | `4` | Maximum complete routes/download restarts. |
| `YOUTUBE_PROXY_HEALTH_TIMEOUT` | `5` | SOCKS connectivity timeout in seconds. |
| `YOUTUBE_PROXY_VALIDATION_TIMEOUT` | `15` | yt-dlp socket timeout used during proxy validation. |
| `YOUTUBE_PROXY_HEALTH_CONCURRENCY` | `30` | Bounded connectivity worker count (maximum 40). |
| `YOUTUBE_PROXY_VALIDATION_CONCURRENCY` | `3` | Bounded concurrent yt-dlp validators (maximum 5). |
| `YOUTUBE_PROXY_CACHE_TTL_SECONDS` | `900` | Healthy route revalidation TTL. |
| `YOUTUBE_PROXY_FAILURE_COOLDOWN_SECONDS` | `600` | Cooldown after repeated failures. |
| `YOUTUBE_PROXY_MAX_CONSECUTIVE_FAILURES` | `2` | Failures before cooldown. |
| `YOUTUBE_PROXY_MAX_CANDIDATES` | `200` | Maximum candidates tested per refresh (maximum 500). |
| `YOUTUBE_PROXY_POOL_TARGET` | `4` | Healthy routes sought during discovery. |
| `YOUTUBE_PROXY_PREWARM_ENABLED` | `true` | Starts the non-blocking pool maintainer with FastAPI. |
| `YOUTUBE_PROXY_PREWARM_URL` | Valid ApexLoad test Short | Server-controlled URL used for startup validation. Change it if the video becomes unavailable. |
| `YOUTUBE_PROXY_STARTUP_WAIT_SECONDS` | `30` | Bounded startup readiness window; never an indefinite wait. |
| `YOUTUBE_PROXY_REVALIDATE_AHEAD_SECONDS` | `120` | Refreshes healthy routes this many seconds before TTL expiry. |
| `YOUTUBE_PROXY_MAINTENANCE_INTERVAL_SECONDS` | `120` | Healthy-pool maintenance interval. |
| `YOUTUBE_PROXY_REFRESH_BACKOFF_SECONDS` | `60` | Initial failed-refresh backoff. |
| `YOUTUBE_PROXY_REFRESH_MAX_BACKOFF_SECONDS` | `900` | Maximum exponential refresh backoff. |
| `YOUTUBE_PROXY_SHUTDOWN_TIMEOUT_SECONDS` | `25` | Maximum background-thread join time during shutdown. |
| `YOUTUBE_PROXY_SOURCE_CACHE_TTL_SECONDS` | `900` | Proxy-list fetch cache TTL. |
| `YOUTUBE_PROXY_CONNECTIVITY_URL` | `http://api.iplocate.io/ip` | Server-controlled public endpoint used only for the fast SOCKS egress check. |
| `YOUTUBE_PLAYER_CLIENT` | `mweb` | YouTube client used with PO tokens. |
| `YOUTUBE_PROXY_SOURCE_URLS` | IPLocate, Proxifly | Server-controlled comma-separated sources. Hosts must also be allow-listed. |
| `YOUTUBE_PROXY_SOURCE_ALLOWED_HOSTS` | GitHub raw, jsDelivr | Explicit source hostname allow-list. |
| `BGUTIL_ENABLED` | `true` | Starts the bundled persistent provider before FastAPI. |
| `BGUTIL_BASE_URL` | `http://127.0.0.1:4416` | Internal provider URL used by the plugin. |
| `BGUTIL_READY_TIMEOUT_SECONDS` | `45` | Startup readiness deadline. |
| `SERVICE_SHUTDOWN_TIMEOUT_SECONDS` | `35` | Supervisor grace period, allowing FastAPI to join the proxy maintainer cleanly. |

## Docker and PO-token lifecycle

The image pins the proven compatible set at build time:

- yt-dlp `2026.07.04`
- bgutil-ytdlp-pot-provider `1.3.1`
- Deno `2.9.5`
- yt-dlp-ejs `0.8.0`

The Docker build installs the BgUtils server source and Deno dependencies under `/opt/bgutil/server`. BgUtils 1.3.1 defaults to wildcard binding, so the pinned source is patched during the Docker build to bind both listen paths to `127.0.0.1`; the build fails if wildcard bind literals remain. `start_services.py` acts as PID 1: it starts BgUtils, waits for `http://127.0.0.1:4416/ping`, starts Uvicorn, forwards termination, reaps both children, and terminates the container if either required process exits. Only port 8000 is exposed; do not publish 4416 in Coolify.

Update these pins deliberately after a staging build and real download test. Do not perform runtime package upgrades inside the production container.

## Verification and troubleshooting

After deployment:

```bash
curl https://api.apexload.org/api/health
docker exec <container> curl -fsS http://127.0.0.1:4416/ping
docker exec <container> deno --version
docker exec <container> yt-dlp -v --skip-download --extractor-args "youtube:player_client=mweb" "https://www.youtube.com/shorts/SybaN0KNRhY"
```

The health response should contain `"potProviderReady": true`. Verbose yt-dlp output should show `bgutil:http-1.3.1 (external)` and `Solving JS challenges using deno`. Server logs should contain `pot_provider_ready`, `proxy_pool_refresh_started`, `proxy_youtube_validation_passed`, and `youtube_proxy_selected` as traffic arrives.

To test a real public-proxy discovery manually (never in CI):

```bash
RUN_YOUTUBE_PROXY_INTEGRATION=1 pytest -q tests/test_youtube_proxy_manager.py -k manual
```

If `LOGIN_REQUIRED` persists, confirm proxy list sources are reachable, check for `proxy_youtube_validation_failed` codes, verify the PO provider ping, and confirm the current yt-dlp/plugin versions are still compatible. Do not add cookies to proxy mode, disable certificate verification, hard-code a temporary proxy, or enable WARP as a dependency.

## Coolify deployment

1. Keep the application root at `apexload-backend` and build the repository Dockerfile.
2. Publish only container port `8000`; do not add `4416` as a port, service, or public route.
3. Set `YOUTUBE_PROXY_ENABLED=true`, `YOUTUBE_PROXY_DIRECT_FIRST=false`, `YOUTUBE_PLAYER_CLIENT=mweb`, `BGUTIL_ENABLED=true`, and `BGUTIL_BASE_URL=http://127.0.0.1:4416`.
4. Start with the other defaults in `.env.example`. Tune concurrency downward if CPU/process pressure appears.
5. Set `YOUTUBE_AUTH_MODE=none` for anonymous proxy-first production. YouTube account cookies are intentionally incompatible with public proxy routes.
6. Redeploy from a clean image build and check the startup logs for `pot_provider_ready` before testing analyze and 1080p download/merge.
7. Validate a normal YouTube URL, the valid Short, and the removed Short; then smoke-test Instagram, Facebook, TikTok, X/Twitter, and Snapchat.

The diagnostic `apexload-warp` service and any BgUtils installation or server started manually inside an old container can be removed after the new image is validated. They are not used by this implementation.
