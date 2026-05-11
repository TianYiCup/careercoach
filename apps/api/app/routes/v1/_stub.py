"""Helper used by every v0.1 stub route to fail with the standard error envelope."""

from fastapi import HTTPException, status

from app.schemas.common import ErrorResponse


def not_implemented(endpoint: str) -> HTTPException:
    """Return a 501 HTTPException whose body matches ErrorResponse.

    Raise (don't return) the result, so FastAPI's exception handler runs.
    """
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "NOT_IMPLEMENTED",
            "message": f"{endpoint} is locked in OpenAPI v0.1 but not implemented yet",
        },
    )


STUB_RESPONSES: dict[int | str, dict[str, object]] = {
    501: {
        "model": ErrorResponse,
        "description": "Endpoint exists in v0.1 schema but the handler is not implemented.",
    },
}
