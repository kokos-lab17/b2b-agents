"""Token-efficient Groq client with session budgeting and bounded retry (Feature 9)."""
import json
import logging
import random
import time
from typing import Any
import httpx
from backend.config import settings
from backend.models import TokenUsageMetadata

logger = logging.getLogger(__name__)


class BudgetExhaustedError(RuntimeError):
    """Raised when session call budget or token budget is reached."""


class GroqClient:
    """Manages Groq LLM calls within strict session token and call envelopes."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.base_url = settings.GROQ_BASE_URL
        self.timeout = settings.REQUEST_TIMEOUT_SECONDS
        self.max_retries = settings.MAX_RETRIES

        # Session tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.total_calls = 0
        self.agent_calls: dict[str, int] = {}

    def get_usage_metadata(self, is_demo: bool = False) -> TokenUsageMetadata:
        return TokenUsageMetadata(
            prompt_tokens=self.total_prompt_tokens,
            completion_tokens=self.total_completion_tokens,
            total_tokens=self.total_tokens,
            total_calls=self.total_calls,
            is_demo=is_demo,
        )

    def check_budget(self, agent_name: str) -> None:
        """Enforce session-level and per-agent call/token budgets."""
        if self.total_calls >= settings.MAX_MODEL_CALLS:
            raise BudgetExhaustedError(
                f"Session call limit ({settings.MAX_MODEL_CALLS}) reached."
            )
        if self.total_tokens >= settings.MAX_SESSION_TOKENS:
            raise BudgetExhaustedError(
                f"Session token budget ({settings.MAX_SESSION_TOKENS}) exhausted."
            )
        agent_current = self.agent_calls.get(agent_name, 0)
        if agent_current >= settings.MAX_AGENT_CALLS:
            raise BudgetExhaustedError(
                f"Agent '{agent_name}' call limit ({settings.MAX_AGENT_CALLS}) reached."
            )

    def call_agent(
        self,
        agent_name: str,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
        response_json: bool = True,
    ) -> dict[str, Any] | str:
        """Execute a compact, token-efficient Groq LLM call with retry and backoff."""
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is not configured.")

        self.check_budget(agent_name)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": settings.MAX_OUTPUT_TOKENS,
            "temperature": temperature,
        }

        if response_json:
            payload["response_format"] = {"type": "json_object"}

        delay = settings.RETRY_BASE_DELAY_SECONDS
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            self.check_budget(agent_name)
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                    if resp.status_code == 429:
                        # Rate limit: bounded exponential backoff with jitter
                        jitter = random.uniform(0.1, 0.5)
                        retry_after = resp.headers.get("retry-after")
                        wait_time = float(retry_after) if retry_after else (delay * (2 ** (attempt - 1)) + jitter)
                        logger.warning(
                            "Groq 429 rate limit hit for agent %s on attempt %d. Waiting %.2fs",
                            agent_name, attempt, wait_time
                        )
                        time.sleep(wait_time)
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                    # Extract usage metadata
                    usage = data.get("usage", {})
                    p_tokens = usage.get("prompt_tokens", 0)
                    c_tokens = usage.get("completion_tokens", 0)
                    t_tokens = usage.get("total_tokens", p_tokens + c_tokens)

                    self.total_prompt_tokens += p_tokens
                    self.total_completion_tokens += c_tokens
                    self.total_tokens += t_tokens
                    self.total_calls += 1
                    self.agent_calls[agent_name] = self.agent_calls.get(agent_name, 0) + 1

                    content = data["choices"][0]["message"]["content"]
                    if response_json:
                        try:
                            return json.loads(content)
                        except json.JSONDecodeError:
                            # Clean code fence if any
                            clean = content.strip().lstrip("```json").rstrip("```").strip()
                            return json.loads(clean)
                    return content

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                logger.warning(
                    "Groq call failed for %s (attempt %d/%d): %s",
                    agent_name, attempt, self.max_retries, exc
                )
                time.sleep(delay * attempt)

        raise RuntimeError(
            f"Failed Groq LLM call for {agent_name} after {self.max_retries} attempts: {last_error}"
        )
