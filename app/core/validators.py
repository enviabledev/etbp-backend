import re


def validate_international_phone(phone: str | None) -> str | None:
    """Validate and normalize phone number to international format.
    Returns None if input is None/empty. Raises ValueError if invalid."""
    if not phone:
        return phone
    # Strip spaces, dashes, parentheses
    phone = re.sub(r"[\s\-\(\)]", "", phone)
    if not re.match(r"^\+[1-9]\d{6,14}$", phone):
        raise ValueError(
            "Phone number must be in international format (e.g., +2348012345678)"
        )
    return phone
