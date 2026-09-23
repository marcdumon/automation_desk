"""Frozen plans: what was previewed is exactly what gets executed.

A plan stores the resolved payload (concrete dates, Google ids, etags) under a random id. Executing looks the plan up and
never re-resolves the sentence, so 'tomorrow' cannot drift between preview and confirm.
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from llm_automation.jobs import Job

TTL_SECONDS = 15 * 60


@dataclass
class Plan:
    """A resolved standard task waiting for confirmation."""

    group_id: str
    task_id: str
    payload: Any
    row_ids: set[str]
    job: Job
    created: float = field(default_factory=time.monotonic)


class PlanStore:
    """In-memory plans with expiry; a plan can be executed once."""

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self._lock = threading.Lock()

    def put(self, plan: Plan) -> str:
        """Store a plan and return its id."""
        plan_id = secrets.token_urlsafe(12)
        with self._lock:
            self._expire()
            self._plans[plan_id] = plan
        return plan_id

    def get(self, plan_id: str) -> Plan | None:
        """A plan without removing it, or None when unknown or expired."""
        with self._lock:
            self._expire()
            return self._plans.get(plan_id)

    def take(self, plan_id: str) -> Plan | None:
        """Remove and return a plan, or None when unknown or expired."""
        with self._lock:
            self._expire()
            return self._plans.pop(plan_id, None)

    def _expire(self) -> None:
        """Drop plans older than the TTL."""
        cutoff = time.monotonic() - TTL_SECONDS
        for plan_id in [k for k, p in self._plans.items() if p.created < cutoff]:
            del self._plans[plan_id]
