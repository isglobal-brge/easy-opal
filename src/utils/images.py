"""Container image reference helpers."""


def qualify_image(image: str) -> str:
    """Make Docker Hub short names deterministic for Docker and Podman."""
    if "/" not in image:
        return f"docker.io/library/{image}"

    registry = image.split("/", 1)[0]
    if registry == "localhost" or "." in registry or ":" in registry:
        return image
    return f"docker.io/{image}"
