from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ApiErrorKind(str, Enum):
    SUCCESS = "success"
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    BAD_REQUEST = "bad_request"
    AUTHENTICATION = "authentication"
    PAYMENT_REQUIRED = "payment_required"
    PERMISSION_OR_SUBSCRIPTION = "permission_or_subscription"
    NOT_FOUND = "not_found"
    CONFLICT_OR_LOCKED = "conflict_or_locked"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    VALIDATION = "validation"
    RATE_LIMITED = "rate_limited"
    MODERATION_REJECTED = "moderation_rejected"
    TRANSIENT_SERVER = "transient_server"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorDecision:
    kind: ApiErrorKind
    retryable: bool
    manual_action: bool
    message: str
    suggested_delay_seconds: float | None = None


def classify_http_status(
    status: int | None, retry_after_seconds: float | None = None
) -> ErrorDecision:
    if status is None:
        return ErrorDecision(
            ApiErrorKind.NETWORK,
            retryable=True,
            manual_action=False,
            message="Network error; retry with backoff after reconciliation for writes",
        )
    if 200 <= status < 300:
        if status == 202:
            return ErrorDecision(
                ApiErrorKind.ACCEPTED,
                retryable=True,
                manual_action=False,
                message="Result is pending; continue workflow polling",
            )
        if status == 208:
            return ErrorDecision(
                ApiErrorKind.DUPLICATE,
                retryable=False,
                manual_action=False,
                message="The same operation was already submitted",
            )
        return ErrorDecision(
            ApiErrorKind.SUCCESS, False, False, "Request completed successfully"
        )

    decisions = {
        400: ErrorDecision(
            ApiErrorKind.BAD_REQUEST, False, True, "Validate parameters and payload"
        ),
        401: ErrorDecision(
            ApiErrorKind.AUTHENTICATION,
            False,
            True,
            "Token is missing, malformed, expired, or for a different environment",
        ),
        402: ErrorDecision(
            ApiErrorKind.PAYMENT_REQUIRED,
            False,
            True,
            "The API method, tariff option, or subscription requires payment",
        ),
        403: ErrorDecision(
            ApiErrorKind.PERMISSION_OR_SUBSCRIPTION,
            False,
            True,
            "Token category, seller permission, subscription, or method access is missing",
        ),
        404: ErrorDecision(
            ApiErrorKind.NOT_FOUND,
            False,
            True,
            "Endpoint or requested entity was not found",
        ),
        409: ErrorDecision(
            ApiErrorKind.CONFLICT_OR_LOCKED,
            False,
            True,
            "Entity state conflicts with the operation or the resource is locked",
        ),
        423: ErrorDecision(
            ApiErrorKind.CONFLICT_OR_LOCKED,
            False,
            True,
            "The resource is locked; inspect state and retry only after reconciliation",
        ),
        413: ErrorDecision(
            ApiErrorKind.PAYLOAD_TOO_LARGE,
            False,
            True,
            "Split the request into smaller batches",
        ),
        422: ErrorDecision(
            ApiErrorKind.VALIDATION,
            False,
            True,
            "Payload is syntactically valid but violates method constraints",
        ),
        429: ErrorDecision(
            ApiErrorKind.RATE_LIMITED,
            True,
            False,
            "Rate limit exceeded; use Retry-After or method-specific backoff",
            suggested_delay_seconds=retry_after_seconds,
        ),
        451: ErrorDecision(
            ApiErrorKind.MODERATION_REJECTED,
            False,
            True,
            "The requested artifact was rejected by moderation",
        ),
    }
    if status in decisions:
        return decisions[status]
    if status in {408, 425}:
        return ErrorDecision(
            ApiErrorKind.TRANSIENT_SERVER,
            True,
            False,
            "Temporary request state; retry a read with bounded backoff",
        )
    if status >= 500:
        return ErrorDecision(
            ApiErrorKind.TRANSIENT_SERVER,
            True,
            False,
            "WB service error; retry with bounded exponential backoff",
        )
    return ErrorDecision(
        ApiErrorKind.UNKNOWN,
        False,
        True,
        f"Unhandled HTTP status {status}; inspect the archived response",
    )
