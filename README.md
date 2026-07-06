# ApexLoad Backend

Version 1.2C backend skeleton for **ApexLoad: Social Downloader**.

This version uses `yt-dlp` for real metadata analysis in `POST /api/analyze`,
real local download jobs in `POST /api/download`, and local file serving through
`GET /api/file/{fileId}`. Jobs are kept in memory for now.

## Stack

- Python
- FastAPI
- Uvicorn
- Docker
- yt-dlp

## Run Locally

```bash
cd apexload-backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

## Docker

Build:

```bash
docker build -t apexload-backend .
```

Run:

```bash
docker run -p 8000:8000 --env-file .env.example apexload-backend
```

## Coolify Deployment

1. Push `apexload-backend` to your Git repository.
2. In Coolify, create a new Docker-based app from the repository.
3. Set the app root/path to `apexload-backend` if this backend lives inside a larger repo.
4. Expose port `8000`.
5. Add environment variables from `.env.example`.
6. Deploy. Coolify will build the Dockerfile and run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Endpoints

### Health

```http
GET /api/health
```

### Analyze Link

```http
POST /api/analyze
Content-Type: application/json

{
  "url": "https://www.instagram.com/reel/example"
}
```

Returns real normalized metadata when `yt-dlp` can analyze the public link.
If `USE_MOCK_ANALYZE_FALLBACK=true`, supported public links that `yt-dlp` cannot
analyze fall back to demo data with `"source": "mock_fallback"`. Unsupported or
unsafe URLs return a clean error.

### Instagram Auth Support

Instagram may block public metadata extraction without authentication, sometimes
returning empty media, login-required, or cookie-required responses. ApexLoad
supports a production cookie file mode and a local browser-cookie mode for
public content analysis only.

Do not ask app users for Instagram login, do not collect credentials, and do not
commit cookies to GitHub.

For the production cookie health checker, admin upload API, persistent
`/data/cookies` volume, and email alerts, see
[`docs/instagram_cookies_health.md`](docs/instagram_cookies_health.md).
Recommended production deployments should set
`INSTAGRAM_COOKIES_PATH=/data/cookies/instagram_cookies.txt`.

Production Coolify configuration:

```env
INSTAGRAM_AUTH_MODE=cookiefile
INSTAGRAM_COOKIE_FILE=/app/secrets/instagram_cookies.txt
ADMIN_API_KEY=<strong-secret-key>
```

Local development configuration:

```env
INSTAGRAM_AUTH_MODE=browser
YTDLP_COOKIES_FROM_BROWSER_ENABLE=true
YTDLP_COOKIES_BROWSER=chrome
```

Put the Netscape-format cookie file in Coolify persistent storage at
`/app/secrets/instagram_cookies.txt`, or upload it through the internal admin
API/page. The Docker image creates `/app/secrets`, but it does not copy real
cookies into the image.

Successful cookiefile responses use `"source": "yt_dlp_cookies"`. Browser-cookie
development responses use `"source": "yt_dlp_browser"`. Instagram may still
reject `yt-dlp` even with valid cookies, so reliability cannot be guaranteed.

Using Instagram cookies may risk account restrictions. Use responsibly and only
for public content you are allowed to access.

Analyze an Instagram Reel:

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.instagram.com/reel/example\"}"
```

Analyze an Instagram Post:

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.instagram.com/p/example\"}"
```

Test YouTube:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.youtube.com/watch?v=dQw4w9WgXcQ\"}"
```

Test Instagram clean URL:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.instagram.com/reel/DYCsJWbNZU7/\"}"
```

Expected Instagram behavior: if cookies are configured and valid, blocked public
metadata requests may return `"source": "yt_dlp_cookies"`. If cookies are not
configured, the API returns a graceful `instagram_requires_auth` error or mock
fallback, depending on `USE_MOCK_ANALYZE_FALLBACK`.

### Start Download

```http
POST /api/download
Content-Type: application/json

{
  "url": "https://www.instagram.com/reel/example",
  "selectedItems": [
    { "formatId": "1080p", "type": "video" },
    { "formatId": "mp3", "type": "audio" },
    { "formatId": "thumbnail", "type": "image" }
  ],
  "premium": true,
  "noWatermark": true
}
```

Creates a real background download job and stores files under
`storage/downloads/{jobId}/`.

Test start download:

```bash
curl -X POST http://127.0.0.1:8000/api/download \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.youtube.com/watch?v=dQw4w9WgXcQ\",\"selectedItems\":[{\"formatId\":\"720p\",\"type\":\"video\"}],\"premium\":false,\"noWatermark\":false}"
```

### Download Status

```http
GET /api/download/status/{jobId}
```

Test status:

```bash
curl http://127.0.0.1:8000/api/download/status/JOB_ID
```

### File Endpoint

```http
GET /api/file/{fileId}
```

Open a completed file:

```text
http://127.0.0.1:8000/api/file/FILE_ID
```

## Quick Test Commands

Health:

```bash
curl http://127.0.0.1:8000/api/health
```

Analyze YouTube:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.youtube.com/watch?v=dQw4w9WgXcQ\"}"
```

Start download:

```bash
curl -X POST http://127.0.0.1:8000/api/download \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.youtube.com/watch?v=dQw4w9WgXcQ\",\"selectedItems\":[{\"formatId\":\"720p\",\"type\":\"video\"}],\"premium\":false,\"noWatermark\":false}"
```

Status:

```bash
curl http://127.0.0.1:8000/api/download/status/JOB_ID
```

File:

```text
Open http://127.0.0.1:8000/api/file/FILE_ID
```

## Version 1.3.1 Manual Download Tests

Use the same start-download shape and replace the `url` / `selectedItems` values
for each case:

- YouTube video: `{"formatId":"480p","type":"video"}`
- YouTube MP3: `{"formatId":"mp3","type":"audio"}`
- Instagram Reel video: `{"formatId":"480p","type":"video"}`
- Instagram image/photo: `{"formatId":"original","type":"image"}`
- Instagram video and Instagram MP3 should be requested separately. The API now
  accepts one selected item per request.
- TikTok video: `{"formatId":"480p","type":"video"}`
- TikTok MP3: `{"formatId":"mp3","type":"audio"}`
- X/Twitter video: `{"formatId":"480p","type":"video"}`
- X/Twitter image: `{"formatId":"original","type":"image"}`
- Snapchat video: `{"formatId":"480p","type":"video"}`

If YouTube returns sign-in or bot verification, configure optional YouTube
cookies:

```env
YOUTUBE_AUTH_MODE=cookiefile
YOUTUBE_COOKIES_FILE=/app/secrets/youtube_cookies.txt
```

Audio notes: if `ffmpeg` is installed, MP3/M4A extraction uses yt-dlp audio
post-processing with `bestaudio/best`, so platforms that only expose muxed
media can still produce audio files. Without `ffmpeg`, audio jobs fail with a
clear message: `Audio extraction requires ffmpeg on the server.` The production
Docker image installs `ffmpeg`; local Windows testing requires `ffmpeg` and
`ffprobe` to be installed and available in `PATH`.

TikTok MP3 local test:

```bash
curl -X POST http://127.0.0.1:8000/api/download -H "Content-Type: application/json" -d "{\"url\":\"https://www.tiktok.com/@mdnazmulhossain20/video/7610596505984011527\",\"selectedItems\":[{\"formatId\":\"mp3\",\"type\":\"audio\"}],\"premium\":false,\"noWatermark\":false}"
```

## Version 1.2C Notes

- `POST /api/analyze` uses `yt-dlp` with `download=False`.
- `POST /api/download` creates real local background download jobs.
- `GET /api/download/status/{jobId}` reports in-memory job status and files.
- `GET /api/file/{fileId}` serves completed local files.
- Real platform links may fail when a platform blocks metadata extraction,
  requires login, or restricts public access. The API returns a clean error or
  mock fallback depending on `USE_MOCK_ANALYZE_FALLBACK`.
- Optional Instagram cookies can improve public Instagram metadata analysis, but
  no real cookies are stored in this repository or copied into the Docker image.
- If `ffmpeg` is unavailable, video downloads use a single-file fallback where
  possible instead of forcing merged video/audio formats.
- No Redis queue yet.
- No database yet.
- `API_KEY` exists in config but is not enforced yet.
- Jobs and file registry are in memory, so they reset when the server restarts.

## TODO for Future Versions

- Add platform-specific metadata parsing.
- Improve available format and media type detection from real platform samples.
- Move jobs/file registry to Redis/database.
- Add cleanup for old files.
- Add API key enforcement or a production auth strategy.

## Version 1.3.2 Instagram Auth Operations

ApexLoad now uses a central yt-dlp options helper for Instagram auth. Do not
commit cookies to GitHub and do not copy real cookies into the Docker image.

Production Coolify env:

```env
INSTAGRAM_AUTH_MODE=cookiefile
INSTAGRAM_COOKIE_FILE=/app/secrets/instagram_cookies.txt
ADMIN_API_KEY=<strong-secret-key>
YTDLP_UPDATE_POLICY=manual
```

Coolify setup:

1. Add persistent storage mounted at `/app/secrets`.
2. Set the env vars above.
3. Redeploy the backend image.
4. Open `/admin/instagram`, enter the admin key, upload a Netscape cookie file,
   and validate it.
5. Test Instagram analyze and download again.

Local development can use browser cookies instead of a server cookie file:

```env
INSTAGRAM_AUTH_MODE=browser
YTDLP_COOKIES_FROM_BROWSER_ENABLE=true
YTDLP_COOKIES_BROWSER=chrome
YTDLP_COOKIES_BROWSER_PROFILE=
YTDLP_COOKIES_BROWSER_KEYRING=
```

Internal admin API:

```bash
curl -H "X-Admin-Key: YOUR_KEY" http://127.0.0.1:8000/api/admin/instagram/auth-status

curl -X POST http://127.0.0.1:8000/api/admin/instagram/upload-cookies \
  -H "X-Admin-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"cookiesText\":\"# Netscape HTTP Cookie File...\"}"

curl -X POST http://127.0.0.1:8000/api/admin/instagram/validate-cookies \
  -H "X-Admin-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"testUrl\":\"https://www.instagram.com/reel/DYcGKp2hwA1/\"}"

curl -X DELETE -H "X-Admin-Key: YOUR_KEY" http://127.0.0.1:8000/api/admin/instagram/cookies
```

Debug auth status:

```bash
curl http://127.0.0.1:8000/api/debug/ytdlp-auth
```

## yt-dlp version management

`yt-dlp` is pinned to a stable version for production reliability:

```text
yt-dlp==2026.07.04
```

Platforms such as Instagram, YouTube, TikTok, and Facebook change often, so
`yt-dlp` should be updated regularly, but only after local testing. Do not use
automatic production updates or runtime `pip install -U yt-dlp` commands. To
update `yt-dlp`, change the pinned version in `requirements.txt`, test locally,
then deploy.

TODO: Add a weekly yt-dlp update checker email alert in a future task.

## YouTube Cookiefile Operations

Some YouTube and YouTube Shorts links may require sign-in verification. ApexLoad
supports a separate YouTube Netscape cookie file so this can be fixed without
affecting Instagram auth.

Production Coolify env:

```env
YOUTUBE_AUTH_MODE=cookiefile
YOUTUBE_COOKIES_FILE=/app/secrets/youtube_cookies.txt
ADMIN_API_KEY=<strong-secret-key>
```

Local development default:

```env
YOUTUBE_AUTH_MODE=none
YOUTUBE_COOKIES_FILE=secrets/youtube_cookies.txt
```

Upload and validate cookies through the admin API:

```bash
curl -H "X-Admin-Key: YOUR_KEY" http://127.0.0.1:8000/api/admin/youtube/auth-status

curl -X POST http://127.0.0.1:8000/api/admin/youtube/upload-cookies \
  -H "X-Admin-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"cookiesText\":\"# Netscape HTTP Cookie File...\"}"

curl -X POST http://127.0.0.1:8000/api/admin/youtube/validate-cookies \
  -H "X-Admin-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"testUrl\":\"https://www.youtube.com/shorts/dQw4w9WgXcQ\"}"
```

`/api/debug/ytdlp-auth` includes `youtubeAuthMode`,
`youtubeCookieFileExists`, and `youtubeCookieFileLooksValid`. Never commit real
YouTube cookies to GitHub or bake them into the Docker image.

Download API behavior changed in Version 1.3.2: only one selected item is
accepted per request. Requests with multiple `selectedItems` return:
`Only one download option can be selected per request.`

If Instagram breaks:

1. Refresh Instagram cookies from the admin panel.
2. Update `yt-dlp`.
3. Redeploy the backend.
4. Validate with `/api/admin/instagram/validate-cookies`.

Instagram may still reject yt-dlp even with valid cookies. The backend returns a
safe message asking for a refreshed server-side session rather than exposing raw
yt-dlp output.
