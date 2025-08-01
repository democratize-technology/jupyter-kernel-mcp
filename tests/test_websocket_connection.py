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
    async def test_connect_with_backoff_success(self, mock_websocket):
        """Test successful WebSocket connection on first try."""
        with patch("websockets.connect", return_value=mock_websocket):
            result = await jupyter_kernel_mcp._connect_with_backoff(
                "ws://localhost:8888/api/kernels/123/channels",
                subprotocols=["kernel.v5.2"]
            )
            assert result == mock_websocket

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
    async def test_connect_with_backoff_exponential_delay(self):
        """Test exponential backoff delay calculation."""
        delays = []
        
        async def mock_sleep(delay):
            delays.append(delay)
        
        with patch("websockets.connect", side_effect=Exception("Connection failed")):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                with pytest.raises(Exception):
                    await jupyter_kernel_mcp._connect_with_backoff(
                        "ws://localhost:8888/api/kernels/123/channels",
                        subprotocols=["kernel.v5.2"],
                        max_retries=4
                    )
        
        # Check exponential backoff: 1, 2, 4, 8 seconds
        assert len(delays) == 4
        assert delays[0] == 1.0
        assert delays[1] == 2.0
        assert delays[2] == 4.0
        assert delays[3] == 8.0

    @pytest.mark.asyncio
    async def test_connect_with_auth_headers(self, mock_websocket, mock_env):
        """Test WebSocket connection includes auth headers."""
        expected_headers = {"Authorization": "token test-token-12345"}
        
        with patch("websockets.connect", return_value=mock_websocket) as mock_connect:
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token-12345'):
                result = await jupyter_kernel_mcp._connect_with_backoff(
                    "ws://localhost:8888/api/kernels/123/channels",
                    subprotocols=["kernel.v5.2"]
                )
                
                # Verify headers were passed
                mock_connect.assert_called_once()
                call_kwargs = mock_connect.call_args[1]
                assert "extra_headers" in call_kwargs
                assert call_kwargs["extra_headers"] == expected_headers

    @pytest.mark.asyncio
    async def test_ensure_kernel_ready_success(self, mock_websocket, sample_kernel_response):
        """Test kernel readiness check succeeds."""
        kernel_info_reply = {
            "header": {"msg_type": "kernel_info_reply", "msg_id": "test-id"},
            "parent_header": {"msg_id": "test-id"},
            "content": {"status": "ok"}
        }
        
        mock_websocket.recv.return_value = json.dumps(kernel_info_reply)
        
        with patch("jupyter_kernel_mcp._connect_with_backoff", return_value=mock_websocket):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp._ensure_kernel_ready(
                    "kernel-123",
                    "session-456",
                    "ws://localhost:8888",
                    {"Authorization": "token test-token"}
                )
                assert result is True
                mock_websocket.send.assert_called_once()

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

    @pytest.mark.asyncio
    async def test_websocket_headers_without_token(self, mock_websocket):
        """Test WebSocket connection without token doesn't include auth headers."""
        with patch("websockets.connect", return_value=mock_websocket) as mock_connect:
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', None):
                result = await jupyter_kernel_mcp._connect_with_backoff(
                    "ws://localhost:8888/api/kernels/123/channels",
                    subprotocols=["kernel.v5.2"]
                )
                
                # Verify no auth headers when no token
                call_kwargs = mock_connect.call_args[1]
                assert call_kwargs["extra_headers"] == {}