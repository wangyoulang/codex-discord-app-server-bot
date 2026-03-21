from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ApprovalEnvelope:
    request_id: str
    request_type: str
    title: str
    body: str
