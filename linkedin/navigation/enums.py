from enum import Enum


class ConnectionStatus(Enum):
    CONNECTED = "connected"
    PENDING = "pending"
    NOT_CONNECTED = "not_connected"


class MessageStatus(Enum):
    SENT = "sent"
    SKIPPED = "skipped"


class ProfileState(str, Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"