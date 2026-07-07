import re
from dataclasses import asdict, dataclass

INSTAGRAM_TEMPORARILY_UNAVAILABLE = (
    "Instagram downloads are temporarily unavailable. Please try again later."
)
INSTAGRAM_TEMPORARILY_BUSY = (
    "Instagram downloads are temporarily busy. Please try again later."
)


@dataclass(frozen=True)
class InstagramErrorClassification:
    category: str
    is_session_problem: bool
    is_temporary_restriction: bool
    is_rate_limit: bool
    is_cookie_problem: bool
    safe_user_message: str
    technical_reason: str

    def to_dict(self) -> dict[str, bool | str]:
        return asdict(self)


def classify_instagram_error(error: object) -> InstagramErrorClassification:
    text = _sanitize(error)
    lower = text.lower()
    category = _category_for(lower)
    cookie_problem = category in {
        "cookies_missing",
        "cookies_empty",
        "cookies_expired",
        "cookies_invalid",
    }
    rate_limit = category == "instagram_rate_limited"
    temporary = category in {
        "instagram_restricted",
        "instagram_rate_limited",
        "instagram_challenge_required",
        "instagram_unavailable",
    }
    session_problem = cookie_problem or temporary or category == "instagram_login_required"
    safe_user_message = INSTAGRAM_TEMPORARILY_UNAVAILABLE
    if category == "media_unavailable":
        safe_user_message = _safe_media_unavailable_message(text)
    return InstagramErrorClassification(
        category=category,
        is_session_problem=session_problem,
        is_temporary_restriction=temporary,
        is_rate_limit=rate_limit,
        is_cookie_problem=cookie_problem,
        safe_user_message=safe_user_message,
        technical_reason=text,
    )


def _category_for(lower: str) -> str:
    if any(
        marker in lower
        for marker in (
            "instagram photo posts are not available",
            "try a reel/video link",
            "could not find a downloadable image",
            "photo download is unavailable",
        )
    ):
        return "media_unavailable"
    if any(marker in lower for marker in ("cookie file is missing", "cookies missing")):
        return "cookies_missing"
    if "cookie file is empty" in lower or "cookies empty" in lower:
        return "cookies_empty"
    if any(marker in lower for marker in ("cookies expired", "cookie expired", "invalidated")):
        return "cookies_expired"
    if any(marker in lower for marker in ("cookie file does not", "cookies invalid")):
        return "cookies_invalid"
    if any(marker in lower for marker in ("checkpoint", "challenge", "suspicious")):
        return "instagram_challenge_required"
    if any(marker in lower for marker in ("rate limit", "rate-limit", "too many requests", "429")):
        return "instagram_rate_limited"
    if any(marker in lower for marker in ("login required", "please log in", "log in to")):
        return "instagram_login_required"
    if any(
        marker in lower
        for marker in (
            "temporarily blocked",
            "try again later",
            "restricted",
            "requested content is not available",
            "not available to everyone",
            "empty media response",
            "api is not granting access",
        )
    ):
        return "instagram_restricted"
    if any(marker in lower for marker in ("content is unavailable", "this content is unavailable")):
        return "media_unavailable"
    if "not available" in lower or "unavailable" in lower:
        return "instagram_unavailable"
    if "cookies" in lower or "cookie" in lower:
        return "cookies_invalid"
    return "unknown_instagram_error"


def _sanitize(error: object) -> str:
    value = str(error)
    value = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", value)
    value = " ".join(value.split())
    return value[:500] or "Unknown Instagram error"


def _safe_media_unavailable_message(message: str) -> str:
    if "Instagram photo posts are not available" in message:
        return "Instagram photo posts are not available for this link. Try a Reel/video link."
    return "Instagram media is not available for this link."
