import os

import aiohttp
import logging
from database import add_chat_message, get_chat_history, add_token_usage

logger = logging.getLogger("system_monitor.gemma")

# A big model on a CPU-only host can take minutes, and a cold start adds the
# reload on top. Raise it if the "very_strong" tier keeps timing out.
REQUEST_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))

SYSTEM_PROMPT = (
    "You are a helpful and precise assistant. Answer briefly and "
    "professionally, in the same language the user writes in."
)


def _estimate_tokens(text: str) -> int:
    """Rough guess at ~4 characters per token, used only when Ollama sends no counters."""
    return max(1, len(text) // 4)


async def ask_gemma(
    telegram_id: int,
    user_message: str,
    ollama_url: str,
    session: aiohttp.ClientSession,
    model: str = "qwen2.5:3b",
    keep_alive: str = "5m",
) -> str:
    """
    Chat with an Ollama model via the native /api/chat endpoint (not the
    OpenAI-compatible one) so we can pass keep_alive -- how long to keep the
    model resident after the request. Saves message history and token usage
    in SQLite.
    """
    # 1. Retrieve history
    history = get_chat_history(telegram_id)

    # 2. Build payload messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, msg in history:
        messages.append({"role": role, "content": msg})
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": keep_alive,
    }

    endpoint = f"{ollama_url.rstrip('/')}/api/chat"
    logger.info(f"Sending prompt to {model} at {endpoint} (keep_alive={keep_alive})...")

    def record_usage(result: dict, reply: str):
        prompt_tokens = result.get("prompt_eval_count")
        completion_tokens = result.get("eval_count")
        if prompt_tokens is not None and completion_tokens is not None:
            add_token_usage(telegram_id, prompt_tokens, completion_tokens, is_estimated=False)
        else:
            add_token_usage(telegram_id, _estimate_tokens(user_message), _estimate_tokens(reply), is_estimated=True)

    try:
        async with session.post(endpoint, json=payload,
                                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
            if resp.status == 200:
                result = await resp.json()
                reply = (result.get("message") or {}).get("content")
                if not reply:
                    # Happens when the model is killed mid-answer: status 200,
                    # body without a message. A KeyError here used to surface
                    # as a silent non-reply in the chat.
                    logger.error("Ollama answered 200 with no content: %s", str(result)[:200])
                    return "❌ The model returned an empty answer. Try again."

                add_chat_message(telegram_id, "user", user_message)
                add_chat_message(telegram_id, "assistant", reply)
                record_usage(result, reply)
                return reply

            err_text = await resp.text()
            logger.error(f"Ollama returned {resp.status}: {err_text}")
            return f"❌ Error from Ollama (Status {resp.status}): {err_text[:200]}"
    except Exception as e:
        logger.exception("Failed to connect to Ollama")
        return f"❌ Connection Error: Could not connect to Ollama at {ollama_url}.\nDetails: {str(e)}"
