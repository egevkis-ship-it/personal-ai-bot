import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def trigger_deploy() -> str:
    """Trigger a Coolify redeploy. Returns status message."""
    # Prefer webhook (simpler, already configured in Coolify)
    webhook = getattr(settings, "coolify_deploy_webhook", None)
    if webhook:
        async with httpx.AsyncClient() as client:
            r = await client.get(webhook, timeout=10)
            r.raise_for_status()
        logger.info("Coolify deploy triggered via webhook")
        return "Деплой запущен."

    # Fall back to API
    if not settings.coolify_url or not settings.coolify_api_token:
        return "Coolify не настроен — деплой не запущен."

    url = f"{settings.coolify_url.rstrip('/')}/api/v1/applications/{settings.coolify_service_id}/restart"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.coolify_api_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        r.raise_for_status()

    logger.info("Coolify deploy triggered via API")
    return "Деплой запущен."


async def get_deploy_status() -> str:
    if not settings.coolify_url or not settings.coolify_api_token or not settings.coolify_service_id:
        return "Coolify не настроен."

    url = f"{settings.coolify_url.rstrip('/')}/api/v1/applications/{settings.coolify_service_id}"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {settings.coolify_api_token}"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

    status = data.get("status", "unknown")
    return f"Статус: {status}"
