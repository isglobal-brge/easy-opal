import secrets


def generate_password(length: int = 24) -> str:
    """Generate a cryptographically random URL-safe password."""
    return secrets.token_urlsafe(length)
