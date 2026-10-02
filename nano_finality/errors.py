"""The error vocabulary, one place, so a status and a code never drift apart.

Every message says what failed and what the caller should do next; the reader
here is another agent, not a person with a support address.
"""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def body(self) -> dict:
        out = {"error": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


def invalid_window(got, allowed):
    return ApiError(
        400,
        "invalid_window",
        f"window={got!r} is not a window this service measures. "
        f"Use one of {list(allowed)}.",
        field="window",
    )


def invalid_limit(lo, hi):
    return ApiError(
        400,
        "invalid_limit",
        f"limit must be an integer between {lo} and {hi}.",
        field="limit",
    )


def invalid_cursor():
    return ApiError(
        400,
        "invalid_cursor",
        "cursor is not one this service issued. Drop it and start from the "
        "first page; every page carries the next cursor to use.",
        field="cursor",
    )


def not_configured():
    return ApiError(
        404,
        "not_configured",
        "No second node is configured, so there is no second column to show. "
        "Set FINALITY_COMPARE_NODE_URL to an independently operated node. "
        "This endpoint will not invent a comparison.",
    )


def no_samples_yet():
    return ApiError(
        503,
        "no_samples_yet",
        "The sampler has never recorded a sample, so there is nothing "
        "measured to serve. This is a broken service, not an empty window: "
        "check GET /v1/health for sampler_running and node reachability.",
    )


def not_found(what="route"):
    return ApiError(404, "not_found", f"No such {what}.")
