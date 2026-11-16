"""Logging filter that masks tokens, secrets and card-like numbers."""
import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1[REDACTED]"),
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]+"), "[REDACTED_KEY]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]+"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)"
                r"(\"?\s*[:=]\s*\"?)[^\s\",}&]+"), r"\1\2[REDACTED]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_NUMBER]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"), "[REDACTED_JWT]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def install_log_redaction() -> None:
    flt = RedactionFilter()
    root = logging.getLogger()
    root.addFilter(flt)
    for handler in root.handlers:
        handler.addFilter(flt)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "shopsphere"):
        logging.getLogger(name).addFilter(flt)
