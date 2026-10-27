from __future__ import annotations

import hmac

from fastapi import HTTPException

from scholarmotion.config import Settings


def require_admin(settings: Settings, supplied_key: str | None) -> None:
    """Require the separate admin credential for global skill/correction writes."""
    if settings.admin_api_key:
        if not supplied_key or not hmac.compare_digest(supplied_key, settings.admin_api_key):
            raise HTTPException(403, "administrator credential required")
        return
    if settings.environment.casefold() in {"production", "prod"}:
        raise HTTPException(503, "ADMIN_API_KEY must be configured for global administration")
