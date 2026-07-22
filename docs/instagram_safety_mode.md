# ApexLoad Instagram Safety Mode

Instagram can temporarily restrict a server-side session even when the cookie
file has not changed. Safety Mode protects the Instagram account/session by
stopping aggressive retries, pausing Instagram-only work during cooldown, and
sending admin alerts.

Safety Mode does not bypass Instagram security systems. It does not run login
bots, does not rotate proxies, and does not store Instagram credentials.

## What Safety Mode Does

Before Instagram analyze/download work, the backend:

1. Checks whether Instagram is paused.
2. Checks Instagram-only rate limits.
3. Acquires an Instagram-only concurrency guard.
4. Runs the existing extraction/download logic.
5. Records success or classifies failure.
6. Records cookie-health warnings separately from real user-request failures.
7. Activates cooldown only after repeated real Instagram analyze/download
   failures reach `INSTAGRAM_FAILURE_THRESHOLD`.

TikTok, YouTube, Facebook, Snapchat, X/Twitter, and other platforms are not
limited by Instagram Safety Mode.

## Error Categories

The classifier maps technical errors into safe categories:

- `cookies_missing`
- `cookies_empty`
- `cookies_expired`
- `cookies_invalid`
- `cookies_warning`
- `cookies_invalid_real_request`
- `health_warning`
- `rate_limited`
- `restricted`
- `login_required`
- `unknown_error`
- `instagram_restricted`
- `instagram_rate_limited`
- `instagram_challenge_required`
- `instagram_login_required`
- `instagram_unavailable`
- `media_unavailable`
- `unknown_instagram_error`

Raw yt-dlp messages stay in backend logs/state as short sanitized technical
reasons. Mobile users receive friendly temporary-unavailable messages.

Cookie health checks are advisory. A failed scheduled/manual health check can
mark Instagram as `degraded` with `cookies_warning` or `health_warning`, but it
does not globally pause Instagram downloads by itself. Real successful
Instagram analyze/download requests clear stale cookie-health warnings and can
clear a stale cookie-only pause when the request proves the server session still
works.

Real Instagram analyze/download failures still count toward
`INSTAGRAM_FAILURE_THRESHOLD`. Repeated login-required, rate-limit, restricted,
challenge, or cookie/session failures then activate a full Instagram-only pause.

## Coolify Environment

Recommended production values:

```env
INSTAGRAM_SAFETY_MODE_ENABLED=true
INSTAGRAM_SAFETY_STATE_PATH=/app/secrets/instagram_safety_state.json
INSTAGRAM_MAX_CONCURRENT_JOBS=1
INSTAGRAM_MAX_REQUESTS_PER_MINUTE=3
INSTAGRAM_MAX_REQUESTS_PER_HOUR=60
INSTAGRAM_FAILURE_THRESHOLD=3
INSTAGRAM_RESTRICTION_COOLDOWN_HOURS=72
INSTAGRAM_RATE_LIMIT_COOLDOWN_HOURS=24
INSTAGRAM_UNKNOWN_ERROR_COOLDOWN_MINUTES=30
INSTAGRAM_RECOVERY_SUCCESS_THRESHOLD=2

INSTAGRAM_COOKIES_PATH=/app/secrets/instagram_cookies.txt
INSTAGRAM_COOKIE_HEALTH_ENABLED=true
INSTAGRAM_COOKIE_CHECK_INTERVAL_MINUTES=180
INSTAGRAM_COOKIE_ALERT_COOLDOWN_HOURS=12
ADMIN_ALERT_EMAIL=yhadrami2003@gmail.com
ADMIN_API_TOKEN=change_this_to_a_secure_token
ADMIN_PANEL_URL=https://api.apexload.org/admin/instagram
```

Mount `/app/secrets` as Coolify persistent storage so the cookie file and safety
state survive redeploys.

## Admin Endpoints

All endpoints require:

```http
Authorization: Bearer <ADMIN_API_TOKEN>
```

Status:

```bash
curl -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  https://api.apexload.org/admin/instagram/safety/status
```

Manual check:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  https://api.apexload.org/admin/instagram/safety/check
```

Manual resume:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  https://api.apexload.org/admin/instagram/safety/resume
```

Manual clear:

```bash
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" \
  https://api.apexload.org/api/admin/instagram/safety/clear
```

Manual resume/clear resets Safety Mode pause state and counters after an admin
confirms Instagram works. It does not delete cookies and does not disable Safety
Mode.

## Email Alerts

Safety Mode uses the existing SMTP notification service.

Alert examples:

- `ApexLoad Alert: Instagram Safety Mode activated`
- `ApexLoad Alert: Instagram rate limit detected`

Recovery email:

- `ApexLoad Recovery: Instagram downloads are active again`

Emails never include cookies or secrets.

Activation emails include whether the pause was triggered by a real user
request or a health-check path, the normalized reason category, pause expiry,
failure count, and the admin clear endpoint to use after successful manual
testing. Health-check-only warnings should be handled as degraded status rather
than a 72-hour global pause.

## Recommended Action When Restriction Happens

1. Stop retries.
2. Open the Instagram account manually.
3. Clear any warning/checkpoint if shown.
4. Wait for cooldown.
5. Run the Safety Mode check endpoint.
6. Upload cookies only if cookies are expired or invalid.

Uploading new cookies repeatedly may not fix a temporary Instagram restriction
and can make the session look more suspicious.
