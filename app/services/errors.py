"""Only deliberate, non-sensitive messages may cross the HTTP boundary."""


class MediaError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str = "media_error",
        status: int = 422,
        *,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retry_after = retry_after
