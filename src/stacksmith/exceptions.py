class StacksmithError(Exception):
    """Base exception for expected Stacksmith domain failures."""


class StacksmithConfigError(ValueError, StacksmithError):
    """Configuration or input-contract error that users can fix."""


class StacksmithValidationError(ValueError, StacksmithError):
    """Validation rule or validation-contract failure."""


class StacksmithValidationExecutionError(StacksmithError):
    """A policy could not execute or returned an invalid outcome."""


class StacksmithPolicyRejectionError(StacksmithValidationError):
    """A property validation policy intentionally rejected its input."""


class StacksmithTransformError(StacksmithValidationError):
    """Transform loading or execution failure."""


class StacksmithRemoteError(ValueError, StacksmithError):
    """Remote fetch, parsing, or authentication failure."""


class StacksmithNotFoundError(FileNotFoundError, StacksmithError):
    """Expected component was not found."""
