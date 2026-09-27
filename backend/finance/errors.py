class FinanceApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        status: int = 400,
        payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.payload = payload or {}
