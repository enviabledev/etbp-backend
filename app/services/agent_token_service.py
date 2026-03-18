import json
import secrets
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import settings


TOKEN_TTL = 600  # 10 minutes


async def generate_agent_token(agent_user_id: str) -> dict:
    """Generate a 6-digit auth token for the agent."""
    code = "".join([str(secrets.randbelow(10)) for _ in range(6)])

    r = aioredis.from_url(settings.redis_url)
    data = json.dumps({"code": code, "created_at": datetime.now(timezone.utc).isoformat()})
    await r.setex(f"agent_token:{agent_user_id}", TOKEN_TTL, data)
    await r.aclose()

    return {"code": code, "expires_in": TOKEN_TTL}


async def verify_agent_token(agent_user_id: str, code: str) -> bool:
    """Verify a 6-digit agent token. Returns True if valid, deletes on success."""
    r = aioredis.from_url(settings.redis_url)
    raw = await r.get(f"agent_token:{agent_user_id}")
    if not raw:
        await r.aclose()
        return False

    data = json.loads(raw.decode())
    if data["code"] != code:
        await r.aclose()
        return False

    await r.delete(f"agent_token:{agent_user_id}")
    await r.aclose()
    return True
