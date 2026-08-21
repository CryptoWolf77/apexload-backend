from urllib.parse import urlparse


def _is_host(host: str, *domains: str) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def detect_platform(url: str) -> str:
    value = url.strip().lower()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    if _is_host(host, "tiktok.com", "tiktokv.com"):
        return "TikTok"
    if _is_host(host, "instagram.com", "instagr.am"):
        return "Instagram"
    if _is_host(host, "facebook.com", "fb.watch"):
        return "Facebook"
    if _is_host(host, "twitter.com", "x.com"):
        return "X/Twitter"
    if _is_host(host, "pinterest.com", "pin.it"):
        return "Pinterest"
    if _is_host(host, "reddit.com"):
        return "Reddit"
    if _is_host(host, "snapchat.com", "snap.com"):
        return "Snapchat"
    return "Unknown"


def detect_media_type(url: str) -> str:
    value = url.lower()

    video_signals = [
        "instagram.com/reel/",
        "/reel/",
        "/reels/",
        "video",
        "reel",
        "reels",
        "tiktok",
    ]
    image_signals = [
        "instagram.com/p/",
        "/p/",
        "pinterest",
        "pin",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "photo",
        "image",
    ]

    if any(signal in value for signal in video_signals):
        return "video"
    if any(signal in value for signal in image_signals):
        return "image"

    # TODO: Continue improving media type detection from real platform samples.
    # Default to video for compatibility when the URL is ambiguous.
    return "video"
