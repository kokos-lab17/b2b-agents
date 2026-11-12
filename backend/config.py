"""Configuration management for autonomous B2B procurement system."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Groq AI Settings
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip()
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()

    # PayPal Sandbox Settings
    PAYPAL_CLIENT_ID: str = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    PAYPAL_CLIENT_SECRET: str = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    PAYPAL_BASE_URL: str = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").strip().rstrip("/")

    # Database Settings
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "procurement_memory.db").strip()

    # Token and Call Budgeting Limits (Feature 9)
    MAX_SESSION_TOKENS: int = int(os.getenv("MAX_SESSION_TOKENS", "15000"))
    MAX_MODEL_CALLS: int = int(os.getenv("MAX_MODEL_CALLS", "12"))
    MAX_AGENT_CALLS: int = int(os.getenv("MAX_AGENT_CALLS", "4"))
    MAX_NEGOTIATION_ROUNDS: int = int(os.getenv("MAX_NEGOTIATION_ROUNDS", "3"))
    MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "300"))

    # Reliability and Network Timeouts
    REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20.0"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BASE_DELAY_SECONDS: float = float(os.getenv("RETRY_BASE_DELAY_SECONDS", "1.5"))

    # CORS and Host
    FRONTEND_ALLOWED_ORIGIN: str = os.getenv("FRONTEND_ALLOWED_ORIGIN", "*").strip()
    FRONTEND_URL: str = (os.getenv("FRONTEND_URL") or "https://b2b-agents.onrender.com").strip().rstrip("/")


settings = Settings()
