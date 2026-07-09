# ApexLoad Instagram Cookie Health

This backend keeps Instagram cookies in persistent server storage, validates
them, and notifies the admin when they need to be refreshed. Uploading fresh
cookies does not require a GitHub push or a Coolify redeploy.

## Coolify Setup

Create a persistent volume mounted at:

```text
/data/cookies
```

Recommended environment variables:

```env
INSTAGRAM_COOKIES_PATH=/data/cookies/instagram_cookies.txt
INSTAGRAM_AUTH_MODE=cookiefile
INSTAGRAM_COOKIE_HEALTH_ENABLED=true
INSTAGRAM_COOKIE_CHECK_INTERVAL_MINUTES=180
INSTAGRAM_COOKIE_ALERT_COOLDOWN_HOURS=12
ADMIN_ALERT_EMAIL=yhadrami2003@gmail.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your_smtp_username
SMTP_PASSWORD=your_smtp_password
SMTP_FROM_EMAIL=admin@yahyazlab.com
SMTP_FROM_NAME=ApexLoad Backend
SMTP_USE_TLS=true
SMTP_USE_SSL=false
ADMIN_API_TOKEN=change_this_to_a_secure_token
ADMIN_PANEL_URL=https://api.apexload.org/admin/instagram
INSTAGRAM_HEALTHCHECK_URL=https://www.instagram.com/reel/your_public_test_reel/
```

Keep SMTP credentials and admin tokens in Coolify environment variables only.
Never commit cookies, SMTP passwords, or tokens to GitHub.

### Cloudflare Email Sending SMTP

Cloudflare Email Sending uses implicit TLS/SMTPS on port 465. It does not use
STARTTLS on port 587 for outbound SMTP.

Example configuration:

```env
SMTP_HOST=smtp.mx.cloudflare.net
SMTP_PORT=465
SMTP_USERNAME=api_token
SMTP_PASSWORD=YOUR_CLOUDFLARE_EMAIL_SENDING_API_TOKEN
SMTP_FROM_EMAIL=alerts@apexload.org
SMTP_FROM_NAME=ApexLoad Backend
SMTP_USE_SSL=true
SMTP_USE_TLS=false
ADMIN_ALERT_EMAIL=yhadrami2003@gmail.com
```

`SMTP_USE_SSL=true` takes priority over `SMTP_USE_TLS=true`, so the backend
will not run implicit SSL and STARTTLS together. Never commit the Cloudflare API
token to source control.

## How It Works

The backend checks:

- cookie file exists
- cookie file is not empty
- cookie file looks like Netscape `cookies.txt`
- cookie file contains Instagram cookie rows
- optional yt-dlp metadata validation against `INSTAGRAM_HEALTHCHECK_URL`

Latest health metadata is stored at:

```text
/data/cookies/instagram_cookie_health.json
```

This JSON never contains cookie contents.

## Admin API

All endpoints require:

```http
Authorization: Bearer <ADMIN_API_TOKEN>
```

Status:

```bash
curl -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  https://api.apexload.org/admin/instagram/cookies/status
```

Manual check:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  https://api.apexload.org/admin/instagram/cookies/check
```

Upload fresh cookies:

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  -F "file=@instagram_cookies.txt;type=text/plain" \
  https://api.apexload.org/admin/instagram/cookies/upload
```

Safe config:

```bash
curl -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  https://api.apexload.org/admin/instagram/cookies/config
```

The safe config response includes whether SMTP is configured and which
transport mode is active, but it never returns SMTP passwords or API tokens.

## Exporting Cookies

1. Log in to Instagram in a browser account that is allowed to access the
   public content you support.
2. Export cookies in Netscape `cookies.txt` format using a trusted browser
   extension or local browser cookie export workflow.
3. Upload `instagram_cookies.txt` from the admin page or API.
4. Run a manual check.

Do not upload Instagram username/password. ApexLoad does not run login bots and
does not store Instagram credentials.

## Email Alerts

When cookies are missing, empty, invalid, expired, or validation fails, ApexLoad
sends:

```text
ApexLoad Alert: Instagram cookies need refresh
```

to `ADMIN_ALERT_EMAIL`. The recommended value is:

```env
ADMIN_ALERT_EMAIL=yhadrami2003@gmail.com
```

When cookies become valid again, ApexLoad sends:

```text
ApexLoad Recovery: Instagram cookies are valid again
```

If SMTP is not configured, the backend logs a safe warning and continues.

## Troubleshooting

- If SMTP does not send, check `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
  `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, and network access from the container.
- If upload fails, confirm the file is a text `.txt` file smaller than 2 MB.
- If validation is `not_configured`, set `INSTAGRAM_HEALTHCHECK_URL` to a
  public Instagram Reel URL used only for lightweight metadata checks.
- If Instagram still fails after valid cookies, update yt-dlp and refresh
  browser cookies.

Cookies are backed up before replacement as:

```text
instagram_cookies.backup.YYYYMMDD_HHMMSS.txt
```

Failed uploads are deleted and never replace the active cookie file.
