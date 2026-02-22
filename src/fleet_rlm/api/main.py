import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_env_vars() -> None:
    required_vars = [
        "DATABASE_URL",
        "MODAL_TOKEN_ID",
        "LITELLM_API_KEY",
        "POSTHOG_API_KEY",
    ]
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        logger.warning(f"Missing environment variables: {', '.join(missing)}")
    else:
        logger.info("All required environment variables are set.")


check_env_vars()

app = FastAPI(title="Fleet RLM API", version="0.1.0")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
