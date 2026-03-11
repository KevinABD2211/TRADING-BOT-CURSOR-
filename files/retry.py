"""
utils/retry.py
--------------
Retry decorator with exponential backoff for transient failures.

Usage:
    # As a decorator factory:
    @with_retry(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    async def call_external_api():
        ...

    # Inline:
    result = await retry_async(my_coro(), max_attempts=3)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, Coroutine, Tuple, Type

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """
    Decorator factory for async functions.

    Args:
        max_attempts:   Total number of attempts (including the first).
        base_delay:     Initial delay between attempts in seconds.
        max_delay:      Maximum delay cap in seconds.
        backoff_factor: Multiplier applied to delay after each failure.
        jitter:         Add random jitter to prevent thundering herd.
        exceptions:     Tuple of exception types that trigger a retry.
                        Other exceptions are re-raised immediately.
    """

    def decorator(func: Callable[..., Coroutine]) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exception: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)

                except exceptions as exc:
                    last_exception = exc

                    if attempt == max_attempts:
                        logger.error(
                            "All %d attempts exhausted for %s: %s",
                            max_attempts,
                            func.__qualname__,
                            exc,
                        )
                        raise

                    # Calculate sleep with optional jitter
                    sleep_time = min(delay, max_delay)
                    if jitter:
                        sleep_time *= 1 + random.uniform(0, 0.1)

                    logger.warning(
                        "Attempt %d/%d failed for %s (%s) — retrying in %.1fs",
                        attempt,
                        max_attempts,
                        func.__qualname__,
                        type(exc).__name__,
                        sleep_time,
                    )

                    await asyncio.sleep(sleep_time)
                    delay = min(delay * backoff_factor, max_delay)

            # Unreachable, but satisfies type checker
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
