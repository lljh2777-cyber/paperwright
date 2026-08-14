"""Stable user-facing exception hierarchy."""


class PaperWrightError(Exception):
    """Base exception for expected PaperWright failures."""


class ConfigurationError(PaperWrightError):
    """Configuration is invalid."""


class PathSafetyError(PaperWrightError):
    """Input/output path violates the configured safety policy."""


class OutputConflictError(PathSafetyError):
    """An output path already exists or conflicts with an input."""


class UnsupportedInputError(PaperWrightError):
    """Input type is outside the Alpha product boundary."""


class CorruptInputError(PaperWrightError):
    """The selected backend cannot open the supplied PDF."""


class ContractValidationError(PaperWrightError):
    """PhysicalDocument or manifest violates the stable contract."""


class BackendUnavailableError(PaperWrightError):
    """Requested backend is known but unavailable in this runtime."""


class BackendExecutionError(PaperWrightError):
    """Backend failed while processing a document."""
