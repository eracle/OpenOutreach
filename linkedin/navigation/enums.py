from enum import Enum


class ProfileState(str, Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    PENDING = "pending"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"
    IGNORED = "ignored"
