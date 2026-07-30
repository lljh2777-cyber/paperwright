"""Stable user-facing exception hierarchy."""


class Paper2MDError(Exception):
    """Base exception for expected Paper2MD failures."""


class ConfigurationError(Paper2MDError):
    """Configuration is invalid."""


class PathSafetyError(Paper2MDError):
    """Input/output path violates the configured safety policy."""


class OutputConflictError(PathSafetyError):
    """An output path already exists or conflicts with an input."""


class UnsupportedInputError(Paper2MDError):
    """Input type is outside the Alpha product boundary."""


class CorruptInputError(Paper2MDError):
    """The selected backend cannot open the supplied PDF."""


class ContractValidationError(Paper2MDError):
    """PhysicalDocument or manifest violates the stable contract."""


class BackendUnavailableError(Paper2MDError):
    """Requested backend is known but unavailable in this runtime."""


class BackendExecutionError(Paper2MDError):
    """Backend failed while processing a document."""
