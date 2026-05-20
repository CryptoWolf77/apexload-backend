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

### Instagram Cookies Support

Instagram may block public metadata extraction without authentication, sometimes
returning empty media, login-required, or cookie-required responses. ApexLoad
supports optional server-side Instagram cookies for public content analysis only.

Do not ask app users for Instagram login, do not collect credentials, and do not
commit cookies to GitHub.

Local/server configuration:

```env
ENABLE_INSTAGRAM_COOKIES=true
INSTAGRAM_COOKIES_FILE=/app/secrets/instagram_cookies.txt
```

Put the Netscape-format cookie file at the configured path on the server. The
Docker image creates `/app/secrets`, but it does not copy real cookies into the
image. In Coolify, mount or create the cookie file as a secret/volume and set the
two environment variables above.

If Instagram analysis fails without cookies, the backend logs:

```text
Instagram analyze failed without cookies. Retrying with cookies if enabled.
```

Successful no-cookie responses use `"source": "yt_dlp"`. Successful cookie retry
responses use `"source": "yt_dlp_cookies"`. Fallback responses use
`"source": "mock_fallback"`.

For Instagram, the backend tries several safe `yt-dlp` analyze configurations:
no cookies, cookies, cookies with the default Instagram web `app_id`, and cookies
with `app_id` plus browser-like headers. Instagram may still reject `yt-dlp` even
with valid cookies, so reliability cannot be guaranteed.

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
- Instagram video + MP3: include both `480p` video and `mp3` audio items.
- TikTok video: `{"formatId":"480p","type":"video"}`
- TikTok MP3: `{"formatId":"mp3","type":"audio"}`
- X/Twitter video: `{"formatId":"480p","type":"video"}`
- X/Twitter image: `{"formatId":"original","type":"image"}`
- Snapchat video: `{"formatId":"480p","type":"video"}`

If YouTube returns sign-in or bot verification, configure optional YouTube
cookies:

```env
ENABLE_YOUTUBE_COOKIES=true
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
