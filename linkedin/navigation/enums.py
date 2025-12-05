from enum import Enum


class ConnectionStatus(Enum):
    CONNECTED = "connected"
    PENDING = "pending"
    NOT_CONNECTED = "not_connected"
    UNKNOWN = "unknown"


class MessageStatus(Enum):
    SENT = "sent"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
