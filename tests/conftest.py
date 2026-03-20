"""Shared test fixtures."""

import os
import tempfile
from pathlib import Path

import pytest

from src.models.instance import InstanceContext
from src.models.config import OpalConfig


@pytest.fixture
def tmp_instance():
    """Create a temporary instance context for unit tests."""
    with tempfile.TemporaryDirectory() as tmp:
        ctx = InstanceContext(name="test", root=Path(tmp))
        ctx.ensure_dirs()
        yield ctx


@pytest.fixture
def sample_config():
    """A fully populated config for testing."""
    from src.models.config import DatabaseConfig, ProfileConfig, WatchtowerConfig

    return OpalConfig(
        stack_name="test-opal",
        hosts=["opal.dev", "127.0.0.1"],
        opal_external_port=8443,
        databases=[
            DatabaseConfig(type="postgres", name="analytics", port=5432),
        ],
        profiles=[
            ProfileConfig(name="rock"),
            ProfileConfig(name="rock-omics", image="datashield/rock-omics"),
        ],
        watchtower=WatchtowerConfig(enabled=True, poll_interval_hours=12),
    )
