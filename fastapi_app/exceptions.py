"""Application errors and the uniform error envelope
{"error": {"code", "message", "details"}} (same shape as the Django service)."""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class FileValidationError(AppError):
    status_code = 400
    code = "invalid_file"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


class ProcessingError(AppError):
    status_code = 500
    code = "processing_failed"


def _envelope(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        details = jsonable_encoder(
            [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
             for e in exc.errors()]
        )
        return _envelope(422, "validation_error", "Invalid request.", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException):
        return _envelope(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(_: Request, exc: IntegrityError):
        logger.warning("Integrity error: %s", exc.orig)
        return _envelope(
            409, "integrity_error", "The operation violates a database constraint."
        )

    @app.exception_handler(OperationalError)
    async def handle_operational_error(_: Request, exc: OperationalError):
        logger.error("Database unavailable: %s", exc.orig)
        return _envelope(503, "database_unavailable", "The database is not reachable.")

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(_: Request, exc: SQLAlchemyError):
        logger.exception("Database error", exc_info=exc)
        return _envelope(500, "database_error", "A database error occurred.")
