def detect_platform(url: str) -> str:
    value = url.lower()
    if "tiktok" in value:
        return "TikTok"
    if "instagram" in value or "instagr.am" in value:
        return "Instagram"
    if "facebook" in value or "fb.watch" in value:
        return "Facebook"
    if "twitter" in value or "x.com" in value:
        return "X/Twitter"
    if "youtube" in value or "youtu.be" in value:
        return "YouTube Shorts"
    if "pinterest" in value:
        return "Pinterest"
    if "reddit" in value:
        return "Reddit"
    if "snapchat.com" in value or "snap.com" in value:
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
        "youtube",
        "shorts",
        "watch",
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
