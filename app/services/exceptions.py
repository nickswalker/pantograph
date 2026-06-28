"""Domain exceptions for the service layer.

Services raise these instead of returning HTTP responses. Route handlers catch
them and translate to JSON: the message is safe to surface to the end user, and
``status`` carries the intended HTTP status code.
"""


class ServiceError(Exception):
    """A business-rule violation raised by a service.

    Args:
        message: User-facing error message.
        status: HTTP status the caller should respond with (default 400).
    """

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status
