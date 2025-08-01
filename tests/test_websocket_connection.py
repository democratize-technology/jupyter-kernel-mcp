"""Tests for WebSocket connection functions."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets

# Import the module to test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jupyter_kernel_mcp


class TestWebSocketConnection:
    """Test WebSocket connection and retry logic."""

    @pytest.mark.asyncio
    async def test_connect_with_backoff_retry_success(self, mock_websocket):
        """Test WebSocket connection succeeds after retries."""
        connect_calls = 0
        
        async def mock_connect(*args, **kwargs):
            nonlocal connect_calls
            connect_calls += 1
            if connect_calls < 3:
                raise Exception("Connection failed")
            return mock_websocket
        
        with patch("websockets.connect", side_effect=mock_connect):
            with patch("asyncio.sleep"):  # Skip delays
                result = await jupyter_kernel_mcp._connect_with_backoff(
                    "ws://localhost:8888/api/kernels/123/channels",
                    subprotocols=["kernel.v5.2"]
                )
                assert result == mock_websocket
                assert connect_calls == 3

    @pytest.mark.asyncio
    async def test_connect_with_backoff_max_retries_exceeded(self):
        """Test WebSocket connection fails after max retries."""
        with patch("websockets.connect", side_effect=Exception("Connection failed")):
            with patch("asyncio.sleep"):  # Skip delays
                with pytest.raises(Exception, match="Connection troubles after 5 attempts"):
                    await jupyter_kernel_mcp._connect_with_backoff(
                        "ws://localhost:8888/api/kernels/123/channels",
                        subprotocols=["kernel.v5.2"],
                        max_retries=5
                    )

    @pytest.mark.asyncio
    async def test_ensure_kernel_ready_timeout(self, mock_websocket):
        """Test kernel readiness check times out."""
        # Mock recv to always timeout
        mock_websocket.recv.side_effect = asyncio.TimeoutError()
        
        with patch("jupyter_kernel_mcp._connect_with_backoff", return_value=mock_websocket):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp._ensure_kernel_ready(
                    "kernel-123",
                    "session-456",
                    "ws://localhost:8888",
                    {"Authorization": "token test-token"}
                )
                assert result is False

    @pytest.mark.asyncio
    async def test_ensure_kernel_ready_exception(self, mock_websocket):
        """Test kernel readiness check handles exceptions."""
        with patch("jupyter_kernel_mcp._connect_with_backoff", side_effect=Exception("Connection error")):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp._ensure_kernel_ready(
                    "kernel-123",
                    "session-456",
                    "ws://localhost:8888",
                    {"Authorization": "token test-token"}
                )
                assert result is False