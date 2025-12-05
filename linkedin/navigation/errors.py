class ProfileNotFoundInSearchError(Exception):
    """Custom exception raised when a profile cannot be found via search."""
    pass


class AuthenticationError(Exception):
    """Custom exception for 401 Unauthorized errors."""
    pass
