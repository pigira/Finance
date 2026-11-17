"""ISIN (ISO 6166) validation."""

import re

# 2-letter country prefix, 9 alphanumeric characters, 1 numeric check digit.
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def normalize_isin(value: str) -> str:
    """Strip spaces and upper-case, so "ie00 b4l5 y983" becomes "IE00B4L5Y983"."""
    return "".join(value.split()).upper()


def is_valid_isin(value: str) -> bool:
    """Check the format and the ISO 6166 check digit of an (already normalized) ISIN."""
    if not _ISIN_PATTERN.match(value):
        return False
    return _luhn_checksum_ok(_expand_letters(value))


def _expand_letters(isin: str) -> str:
    """Replace letters by two digits (A=10 ... Z=35); digits are kept as they are."""
    return "".join(str(int(char, 36)) for char in isin)


def _luhn_checksum_ok(digits: str) -> bool:
    """Luhn mod-10: from the right, double every second digit and sum all digits."""
    total = 0
    for position, char in enumerate(reversed(digits)):
        digit = int(char)
        if position % 2 == 1:  # every second digit, starting left of the check digit
            digit *= 2
            if digit > 9:
                digit -= 9  # same as adding the two digits of the product
        total += digit
    return total % 10 == 0
