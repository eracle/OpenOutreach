class AuthenticationError(Exception):
    """Custom exception for 401 Unauthorized errors."""
    pass


class TerminalStateError(Exception):
    """Profile is already done or dead — caller must skip it"""
    pass


class SkipProfile(Exception):
    """Profile must be skipped."""
    pass


class ProfileInaccessibleError(Exception):
    """Profile is private, deleted, or restricted (HTTP 403/404)."""
    pass


class ReachedConnectionLimit(Exception):
    """ Weekly connection limit reached. """
    pass

