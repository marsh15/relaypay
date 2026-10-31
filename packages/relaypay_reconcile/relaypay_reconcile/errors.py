"""Input errors: a file, column, value or option that cannot be reconciled exactly."""


class ReconcileInputError(ValueError):
    """Raised for malformed input or options. The CLI reports it and exits with status 2."""
