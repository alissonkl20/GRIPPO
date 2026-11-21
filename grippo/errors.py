"""Exception hierarchy shared by all layers."""


class GrippoError(Exception):
    """Base class for errors shown to the user with a clean message."""


class ConfigError(GrippoError):
    pass


class RepositoryError(GrippoError):
    pass


class ValidationError(GrippoError):
    pass


class AuthorizationError(GrippoError):
    """Raised when an operation lacks a valid authorization."""


class StateChangedError(AuthorizationError):
    """The repository changed after the user confirmed; a new confirmation is required."""


class LockTimeoutError(GrippoError):
    pass


class LLMError(GrippoError):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMOutOfMemoryError(LLMError):
    pass


class LLMResponseError(LLMError):
    pass
