"""Tests for kernel management functions."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

# Import the module to test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jupyter_kernel_mcp


class TestKernelManagement:
    """Test kernel creation and management functions."""

    def test_get_kernel_spec(self):
        """Test kernel spec retrieval for different kernel types."""
        # Test Python kernel
        python_spec = jupyter_kernel_mcp._get_kernel_spec("python3")
        assert python_spec["name"] == "python3"
        assert python_spec["display_name"] == "Python 3"
        assert python_spec["language"] == "python"
        
        # Test R kernel
        r_spec = jupyter_kernel_mcp._get_kernel_spec("r")
        assert r_spec["name"] == "ir"
        assert r_spec["display_name"] == "R"
        assert r_spec["language"] == "r"
        
        # Test unknown kernel - should fallback to python3
        unknown_spec = jupyter_kernel_mcp._get_kernel_spec("unknown")
        assert unknown_spec["name"] == "python3"
        assert unknown_spec["display_name"] == "Python 3"
        assert unknown_spec["language"] == "python"

    def test_get_kernel_type_from_spec(self):
        """Test kernel type extraction from spec name."""
        assert jupyter_kernel_mcp._get_kernel_type_from_spec("python3") == "python3"
        assert jupyter_kernel_mcp._get_kernel_type_from_spec("ir") == "r"
        assert jupyter_kernel_mcp._get_kernel_type_from_spec("julia-1.10") == "julia"
        assert jupyter_kernel_mcp._get_kernel_type_from_spec("gophernotes") == "go"
        assert jupyter_kernel_mcp._get_kernel_type_from_spec("evcxr_jupyter") == "rust"
        assert jupyter_kernel_mcp._get_kernel_type_from_spec("unknown") == "python3"  # fallback

    def test_get_kernel_spec_name(self):
        """Test kernel spec name retrieval."""
        assert jupyter_kernel_mcp._get_kernel_spec_name("python3") == "python3"
        assert jupyter_kernel_mcp._get_kernel_spec_name("ir") == "ir"
        assert jupyter_kernel_mcp._get_kernel_spec_name("julia") == "julia-1.10"
        assert jupyter_kernel_mcp._get_kernel_spec_name("go") == "gophernotes"
        assert jupyter_kernel_mcp._get_kernel_spec_name("rust") == "evcxr_jupyter"
        assert jupyter_kernel_mcp._get_kernel_spec_name("unknown") == "python3"  # fallback

    @pytest.mark.asyncio
    async def test_get_available_kernels(self, mock_httpx_client, sample_kernelspecs):
        """Test getting available kernels from Jupyter."""
        mock_response = AsyncMock()
        mock_response.json = MagicMock(return_value=sample_kernelspecs)
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get.return_value = mock_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            result = await jupyter_kernel_mcp._get_available_kernels(
                "http://localhost:8888",
                {"Authorization": "token test-token"}
            )
            assert result == sample_kernelspecs
            mock_httpx_client.get.assert_called_once_with(
                "http://localhost:8888/api/kernelspecs",
                headers={"Authorization": "token test-token"}
            )

    @pytest.mark.asyncio
    async def test_get_available_kernels_error(self):
        """Test getting available kernels handles errors."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = Exception("Connection error")
            mock_client_class.return_value = mock_client
            
            result = await jupyter_kernel_mcp._get_available_kernels(
                "http://localhost:8888",
                {"Authorization": "token test-token"}
            )
            assert result == {"kernelspecs": {}}

    @pytest.mark.asyncio
    async def test_kernel_locking(self, mock_httpx_client, mock_websocket):
        """Test kernel ID operations use locks properly."""
        # Create a controlled lock that we can inspect
        lock_acquired_count = 0
        original_lock = jupyter_kernel_mcp.KERNEL_IDS_LOCK
        
        class TestLock:
            async def __aenter__(self):
                nonlocal lock_acquired_count
                lock_acquired_count += 1
                return self
            
            async def __aexit__(self, *args):
                pass
        
        test_lock = TestLock()
        
        # Mock kernel creation
        create_response = AsyncMock()
        create_response.json = MagicMock(return_value={"id": "test-kernel-id"})
        create_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = create_response
        mock_httpx_client.get.return_value = AsyncMock(json=MagicMock(return_value={"kernelspecs": {}}))
        
        # Mock simple WebSocket response
        mock_websocket.recv.side_effect = [
            json.dumps({"header": {"msg_type": "execute_reply"}, "parent_header": {"msg_id": "test"}, "content": {"status": "ok"}}),
            json.dumps({"header": {"msg_type": "status"}, "content": {"execution_state": "idle"}})
        ]
        
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS_LOCK', test_lock):
            with patch("httpx.AsyncClient", return_value=mock_httpx_client):
                with patch("jupyter_kernel_mcp._connect_with_backoff", return_value=mock_websocket):
                    with patch("jupyter_kernel_mcp._ensure_kernel_ready", return_value=True):
                        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', {}):
                            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                                await jupyter_kernel_mcp._execute_code("1+1")
                                
                                # Should acquire lock multiple times for checking and setting
                                assert lock_acquired_count >= 2  # At least check + set