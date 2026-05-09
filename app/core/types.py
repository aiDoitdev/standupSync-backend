from typing import Annotated, Any
import re

from pydantic import BeforeValidator
from email_validator import validate_email, EmailNotValidError


def _validate_email_lenient(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("string required")
    try:
        return validate_email(v, check_deliverability=False).normalized
    except EmailNotValidError:
        # Allow reserved/special-use TLDs (e.g. .test, .localhost) in dev environments
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            return v.lower()
        raise ValueError(f"value is not a valid email address: {v}")


LenientEmailStr = Annotated[str, BeforeValidator(_validate_email_lenient)]
