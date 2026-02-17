from enum import Enum


class ProfileState(str, Enum):
    NEW = "new"
    PENDING = "pending"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"
    IGNORED = "ignored"
