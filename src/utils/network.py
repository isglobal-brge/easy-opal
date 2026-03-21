import socket


def is_port_in_use(port: int) -> bool:
    """Check if a TCP port is in use using a hybrid connect + bind approach."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True


def find_free_port(start: int, reserved: list[int] | None = None) -> int:
    """Find the next available port starting from `start`."""
    reserved = reserved or []
    port = start
    for _ in range(100):
        if port not in reserved and not is_port_in_use(port):
            return port
        port += 1
    return start


def validate_port(port: int) -> str | None:
    """Returns error message if port is invalid, None if OK."""
    if not isinstance(port, int) or port < 1 or port > 65535:
        return f"Port must be between 1 and 65535, got {port}."
    return None


def get_local_ip() -> str:
    """Detect this machine's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
