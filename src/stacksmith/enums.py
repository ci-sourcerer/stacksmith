from enum import StrEnum, auto


class TerragruntAction(StrEnum):
    """Supported Terragrunt actions used by stacksmith commands."""

    INIT = auto()
    PLAN = auto()
    APPLY = auto()
    DESTROY = auto()


class ValidationRowType(StrEnum):
    """CSV validation report row types."""

    REPORT = auto()
    RESULT = auto()


class ValidationReportFormat(StrEnum):
    """Supported machine-readable validation report output formats."""

    JSON = auto()
    CSV = auto()


class InspectOutputFormat(StrEnum):
    """Supported output formats for `stacksmith info` and `stacksmith ci`."""

    TABLE = auto()
    JSON = auto()


class MergeMode(StrEnum):
    """Supported merge strategies for layered stacksmith inputs."""

    DEEP = auto()
    OVERRIDE = auto()
