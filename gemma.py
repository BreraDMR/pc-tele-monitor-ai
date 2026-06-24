import os
import aiohttp
import logging
from database import add_chat_message, get_chat_history, add_token_usage

logger = logging.getLogger("system_monitor.gemma")


def _estimate_tokens(text: str) -> int:
    """Грубая оценка: ~4 символа на токен. Используется только если Ollama не вернула usage."""
    return max(1, len(text) // 4)


async def ask_gemma(telegram_id: int, user_message: str, ollama_url: str, session: aiohttp.ClientSession) -> str:
    """
    Communicates with Ollama running Gemma 2 9B using an OpenAI-compatible API format.
    Saves message history and token usage in SQLite.
    """
    # 1. Retrieve history
    history = get_chat_history(telegram_id)

    # 2. Build payload messages
    messages = [
        {"role": "system", "content": "You are Gemma 2 9B, a helpful and precise assistant. Answer briefly and professionally."}
    ]
    for role, msg in history:
        messages.append({"role": role, "content": msg})

    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": os.getenv("CHAT_MODEL", "qwen2.5:3b"),
        "messages": messages,
        "stream": False
    }

    endpoint = f"{ollama_url.rstrip('/')}/v1/chat/completions"
    logger.info(f"Sending prompt to Gemma at {endpoint}...")

    def record_usage(result: dict, reply: str):
        usage = result.get("usage")
        if usage and usage.get("prompt_tokens") is not None:
            add_token_usage(telegram_id, usage["prompt_tokens"], usage.get("completion_tokens", 0), is_estimated=False)
        else:
            prompt_tokens = _estimate_tokens(user_message)
            completion_tokens = _estimate_tokens(reply)
            add_token_usage(telegram_id, prompt_tokens, completion_tokens, is_estimated=True)

    try:
        async with session.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status == 200:
                result = await resp.json()
                reply = result["choices"][0]["message"]["content"]

                add_chat_message(telegram_id, "user", user_message)
                add_chat_message(telegram_id, "assistant", reply)
                record_usage(result, reply)
                return reply
            else:
                err_text = await resp.text()
                logger.error(f"Ollama returned {resp.status}: {err_text}")
                # If model gemma2:9b is not found, let's try falling back to gemma2
                if "model not found" in err_text.lower() or "404" in str(resp.status):
                    logger.warning("gemma2:9b model not found, trying fallback to 'gemma2' or 'gemma'")
                    payload["model"] = "gemma2"
                    async with session.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as fallback_resp:
                        if fallback_resp.status == 200:
                            result = await fallback_resp.json()
                            reply = result["choices"][0]["message"]["content"]
                            add_chat_message(telegram_id, "user", user_message)
                            add_chat_message(telegram_id, "assistant", reply)
                            record_usage(result, reply)
                            return reply
                return f"❌ Error from Ollama (Status {resp.status}): {err_text[:200]}"
    except Exception as e:
        logger.exception("Failed to connect to Gemma 2 9B")
        return f"❌ Connection Error: Could not connect to Ollama at {ollama_url}.\nDetails: {str(e)}"
