"""Uniform error envelope: {"error": {"code", "message", "details"}}."""
import logging

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"


def _envelope(code: str, message: str, details=None, http_status=400) -> Response:
    return Response(
        {"error": {"code": code, "message": message, "details": details}},
        status=http_status,
    )


def api_exception_handler(exc, context):
    if isinstance(exc, ProtectedError):
        return _envelope(
            "protected_resource",
            "This record is referenced by other records and cannot be deleted.",
            http_status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, IntegrityError):
        logger.warning("Integrity error: %s", exc)
        return _envelope(
            "integrity_error",
            "The operation violates a database constraint "
            "(duplicate value or invalid reference).",
            http_status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=exc.messages)

    response = exception_handler(exc, context)
    if response is None:
        return None  # unexpected error -> Django's 500 handling

    if isinstance(exc, ValidationError):
        return _envelope(
            "validation_error", "Invalid input.", response.data, response.status_code
        )

    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    if isinstance(exc, Http404):
        code = "not_found"
    elif isinstance(exc, PermissionDenied):
        code = "permission_denied"
    else:
        code = getattr(exc, "default_code", "error")
    return _envelope(code, str(detail or exc), None, response.status_code)
