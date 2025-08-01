"""Pytest configuration and shared fixtures for jupyter-kernel-mcp tests."""

import asyncio
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastmcp import FastMCP


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables for testing."""
    test_env = {
        "JUPYTER_HOST": "localhost",
        "JUPYTER_PORT": "8888",
        "JUPYTER_PROTOCOL": "http",
        "JUPYTER_WS_PROTOCOL": "ws",
        "JUPYTER_TOKEN": "test-token-12345",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    return test_env


@pytest.fixture
def mock_kernel_ids():
    """Mock kernel IDs dictionary."""
    return {}


@pytest.fixture
def mock_websocket():
    """Mock websocket connection."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock()
    ws.close = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock()
    return ws


@pytest.fixture
def mock_httpx_client():
    """Mock httpx async client."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    return client


@pytest.fixture
def sample_kernel_response():
    """Sample kernel creation response."""
    return {
        "id": str(uuid.uuid4()),
        "name": "python3",
        "last_activity": "2024-01-01T00:00:00Z",
        "execution_state": "idle",
        "connections": 0,
    }


@pytest.fixture
def sample_execute_reply():
    """Sample execute reply message."""
    msg_id = str(uuid.uuid4())
    return {
        "header": {
            "msg_id": str(uuid.uuid4()),
            "msg_type": "execute_reply",
            "session": str(uuid.uuid4()),
            "username": "mcp",
            "version": "5.2",
        },
        "parent_header": {"msg_id": msg_id},
        "metadata": {},
        "content": {"status": "ok", "execution_count": 1},
    }


@pytest.fixture
def sample_stream_output():
    """Sample stream output message."""
    msg_id = str(uuid.uuid4())
    return {
        "header": {
            "msg_id": str(uuid.uuid4()),
            "msg_type": "stream",
            "session": str(uuid.uuid4()),
            "username": "mcp",
            "version": "5.2",
        },
        "parent_header": {"msg_id": msg_id},
        "metadata": {},
        "content": {"name": "stdout", "text": "Hello, World!\n"},
    }


@pytest.fixture
def sample_error_output():
    """Sample error output message."""
    msg_id = str(uuid.uuid4())
    return {
        "header": {
            "msg_id": str(uuid.uuid4()),
            "msg_type": "error",
            "session": str(uuid.uuid4()),
            "username": "mcp",
            "version": "5.2",
        },
        "parent_header": {"msg_id": msg_id},
        "metadata": {},
        "content": {
            "ename": "NameError",
            "evalue": "name 'undefined_var' is not defined",
            "traceback": ["Traceback...", "NameError: name 'undefined_var' is not defined"],
        },
    }


@pytest.fixture
def sample_notebook():
    """Sample notebook structure."""
    return {
        "name": "test_notebook.ipynb",
        "path": "/notebooks/test_notebook.ipynb",
        "type": "notebook",
        "created": "2024-01-01T00:00:00Z",
        "last_modified": "2024-01-01T00:00:00Z",
        "content": {
            "cells": [
                {
                    "cell_type": "code",
                    "source": "print('Hello')",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": 1,
                },
                {
                    "cell_type": "markdown",
                    "source": "# Test Notebook",
                    "metadata": {},
                },
            ],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                }
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        },
    }


@pytest.fixture
def sample_kernelspecs():
    """Sample kernel specifications."""
    return {
        "default": "python3",
        "kernelspecs": {
            "python3": {
                "name": "python3",
                "spec": {
                    "argv": ["python", "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                    "display_name": "Python 3",
                    "language": "python",
                },
                "resources": {},
            },
            "ir": {
                "name": "ir",
                "spec": {
                    "argv": ["R", "--slave", "-e", "IRkernel::main()", "--args", "{connection_file}"],
                    "display_name": "R",
                    "language": "R",
                },
                "resources": {},
            },
        },
    }


@pytest_asyncio.fixture
async def mcp_server():
    """Create a FastMCP server instance for testing."""
    server = FastMCP()
    yield server
    # Cleanup if needed
    await asyncio.sleep(0)  # Allow pending tasks to complete


@pytest.fixture
def mock_debug_print(monkeypatch):
    """Mock the debug_print function."""
    mock_print = MagicMock()
    monkeypatch.setattr("builtins.print", mock_print)
    return mock_print