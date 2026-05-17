# ApexLoad Backend

Version 1.2A backend skeleton for **ApexLoad: Social Downloader**.

This project is intentionally mock-only. It provides clean FastAPI endpoints so
the Flutter app can connect to a real VPS API before downloader processing is
implemented.

## Stack

- Python
- FastAPI
- Uvicorn
- Docker

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

Returns mock video or image media data.

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

### Download Status

```http
GET /api/download/status/{jobId}
```

### Demo File Endpoint

```http
GET /api/file/{fileId}
```

## Version 1.2A Notes

- No real `yt-dlp` integration yet.
- No real media processing yet.
- No Redis queue yet.
- No database yet.
- `API_KEY` exists in config but is not enforced yet.
- File endpoint returns JSON only.

## TODO for Version 1.2B

- Add `yt-dlp` link analysis.
- Add platform-specific metadata parsing.
- Add real available format extraction.
- Add safer URL validation and platform allowlist.
- Add download job structure ready for Redis in a later version.
- Add API key enforcement or a production auth strategy.

