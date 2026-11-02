"""In-memory thread-safe rate limiter for authentication (spec Section 12, NFR-1).

Prevents credential brute-forcing with sliding window tracking per IP/identifier.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from app.core.errors import ApiError


class SlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._history: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        """Check if `key` has exceeded the allowed rate limit."""
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # Filter timestamps within sliding window
            timestamps = [t for t in self._history[key] if t > cutoff]
            self._history[key] = timestamps

            if len(timestamps) >= self.max_requests:
                # Calculate remaining wait time
                earliest = min(timestamps)
                retry_after = max(1, int(self.window_seconds - (now - earliest)))
                raise ApiError(
                    status_code=429,
                    code="RATE_LIMITED",
                    message_en=(
                        f"Too many failed login attempts. Please try again in "
                        f"{retry_after} seconds."
                    ),
                    message_key="errors.auth_rate_limited",
                    headers={"Retry-After": str(retry_after)},
                )

    def record_failure(self, key: str) -> None:
        """Record a failed attempt for `key`."""
        now = time.time()
        with self._lock:
            self._history[key].append(now)

    def record_success(self, key: str) -> None:
        """Clear failed attempts upon successful authentication."""
        with self._lock:
            self._history.pop(key, None)

    def reset(self) -> None:
        """Reset all rate limiter state (useful for test fixtures)."""
        with self._lock:
            self._history.clear()


# Default login rate limiter: max 5 failed attempts per 60 seconds
login_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
