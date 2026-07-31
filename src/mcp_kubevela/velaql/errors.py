"""Typed exceptions for the velaql tool.

Distinguishes view-level errors (caller wrote the wrong view) from param-level
errors (caller wrote a wrong/missing param). The tool catches these and turns
them into structured markdown for the LLM.
"""


class VelaQLError(Exception):
    """Base class for all velaql tool errors."""


class VelaQLParamError(VelaQLError):
    """Raised when params fail validation for a known view.

    Attributes:
        missing: param names that were required but absent.
        bad: param name -> human-readable reason for each invalid value.
    """

    def __init__(
        self,
        message: str,
        missing: list[str] | None = None,
        bad: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing: list[str] = list(missing) if missing else []
        self.bad: dict[str, str] = dict(bad) if bad else {}
