"""Tests for MCP tool functions."""

import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import uuid

# Import the module to test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jupyter_kernel_mcp


class TestMCPTools:
    """Test MCP tool functions."""

    @pytest.mark.asyncio
    async def test_compute_auto_mode(self):
        """Test compute function in auto mode."""
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "Result: 42", "error": False}
            
            result = await jupyter_kernel_mcp.compute("1 + 1", mode="auto")
            
            assert result["output"] == "Result: 42"
            assert result["error"] is False
            mock_execute.assert_called_once_with("1 + 1", "python3")

    @pytest.mark.asyncio
    async def test_compute_stream_mode(self):
        """Test compute function in stream mode."""
        with patch("jupyter_kernel_mcp.stream_execute") as mock_stream:
            mock_stream.return_value = {"output": "Streaming output...", "error": False}
            
            result = await jupyter_kernel_mcp.compute("long_running_task()", mode="stream")
            
            assert result["output"] == "Streaming output..."
            assert result["error"] is False
            mock_stream.assert_called_once_with("long_running_task()", "python3")

    @pytest.mark.asyncio
    async def test_compute_detects_streaming_patterns(self):
        """Test compute function detects patterns that require streaming."""
        streaming_patterns = [
            "for i in range(1000000):",
            "while True:",
            "model.fit(",
            "train(",
            "download(",
            "tqdm(",
            "progress",
            ".cuda()",
            "torch.nn",
            "time.sleep(10)"
        ]
        
        with patch("jupyter_kernel_mcp.stream_execute") as mock_stream:
            mock_stream.return_value = {"output": "Streaming...", "error": False}
            
            for pattern in streaming_patterns:
                code = f"# Test\n{pattern}\n    print(i)"
                await jupyter_kernel_mcp.compute(code, mode="auto")
                
            assert mock_stream.call_count == len(streaming_patterns)

    @pytest.mark.asyncio
    async def test_q_quick_execute(self):
        """Test q function for quick execution."""
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "42\n", "error": False}
            
            result = await jupyter_kernel_mcp.q("21 * 2")
            
            assert result == "42"
            mock_execute.assert_called_once_with("21 * 2", "python3")

    @pytest.mark.asyncio
    async def test_q_with_error(self):
        """Test q function with execution error."""
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "NameError: x not defined", "error": True}
            
            result = await jupyter_kernel_mcp.q("x * 2")
            
            assert result == "NameError: x not defined"

    @pytest.mark.asyncio
    async def test_execute_function(self):
        """Test execute function."""
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "Hello World", "error": False}
            
            result = await jupyter_kernel_mcp.execute("print('Hello World')", kernel="python3")
            
            assert result == {"output": "Hello World", "error": False}
            mock_execute.assert_called_once_with("print('Hello World')", "python3")

    @pytest.mark.asyncio
    async def test_list_available_kernels(self, mock_httpx_client, sample_kernelspecs):
        """Test listing available kernels."""
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=sample_kernelspecs)
        mock_httpx_client.get.return_value = mock_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.list_available_kernels()
                
                assert "kernels" in result
                assert "python3" in result["kernels"]
                assert result["kernels"]["python3"]["display_name"] == "Python 3"

    @pytest.mark.asyncio
    async def test_vars_function(self):
        """Test vars function to list variables."""
        # Mock execution result with variable listing
        vars_output = """x: int = 42
y: str = 'hello'
df: DataFrame (5, 3)"""
        
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": vars_output, "error": False}
            
            result = await jupyter_kernel_mcp.vars(kernel="python3", detailed=False)
            
            assert "variables" in result
            assert result["output"] == vars_output

    @pytest.mark.asyncio
    async def test_workspace_function(self):
        """Test workspace function."""
        # Set up mock kernel states
        mock_kernels = {
            "python3": {"id": "py-123", "session": "py-session"},
            "ir": {"id": "r-123", "session": "r-session"}
        }
        
        # Mock vars output for each kernel
        py_vars = "x: int = 42"
        r_vars = "df <- data.frame()"
        
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', mock_kernels):
            with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
                mock_execute.side_effect = [
                    {"output": py_vars, "error": False},
                    {"output": r_vars, "error": False}
                ]
                
                result = await jupyter_kernel_mcp.workspace()
                
                assert "summary" in result
                assert "Python" in result["summary"]
                assert "R" in result["summary"]
                assert len(result["kernels"]) == 2

    @pytest.mark.asyncio
    async def test_kernel_state(self):
        """Test kernel_state function."""
        mock_kernels = {
            "python3": {"id": "py-123", "session": "py-session"},
            "julia-1.10": {"id": "jl-123", "session": "jl-session"}
        }
        
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', mock_kernels):
            # Test show active kernels only
            result = await jupyter_kernel_mcp.kernel_state(show_all=False)
            
            assert "active_kernels" in result
            assert len(result["active_kernels"]) == 2
            assert any(k["type"] == "python3" for k in result["active_kernels"])
            
            # Test show all kernels
            result_all = await jupyter_kernel_mcp.kernel_state(show_all=True)
            
            assert "active_kernels" in result_all
            assert "available_kernels" in result_all
            assert len(result_all["available_kernels"]) > 2  # Should include inactive ones

    @pytest.mark.asyncio
    async def test_suggest_next(self):
        """Test suggest_next function."""
        # Mock kernel with some variables
        mock_kernels = {"python3": {"id": "py-123", "session": "py-session"}}
        
        vars_output = """df: DataFrame (100, 5)
model: LinearRegression
results: dict"""
        
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', mock_kernels):
            with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
                mock_execute.return_value = {"output": vars_output, "error": False}
                
                result = await jupyter_kernel_mcp.suggest_next(kernel="python3")
                
                assert "context" in result
                assert "suggestions" in result
                assert len(result["suggestions"]) > 0
                assert "df" in result["context"]["current_state"]

    @pytest.mark.asyncio
    async def test_reset_specific_kernel(self, mock_httpx_client):
        """Test reset function for specific kernel."""
        mock_kernels = {"python3": {"id": "py-123", "session": "py-session"}}
        
        delete_response = AsyncMock()
        delete_response.raise_for_status = AsyncMock()
        mock_httpx_client.delete.return_value = delete_response
        
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', mock_kernels.copy()):
            with patch("httpx.AsyncClient", return_value=mock_httpx_client):
                with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                    result = await jupyter_kernel_mcp.reset(kernel="python3")
                    
                    assert "Deleted kernels: python3" in result["message"]
                    assert "python3" not in jupyter_kernel_mcp.KERNEL_IDS

    @pytest.mark.asyncio
    async def test_reset_all_kernels(self, mock_httpx_client):
        """Test reset function for all kernels."""
        mock_kernels = {
            "python3": {"id": "py-123", "session": "py-session"},
            "ir": {"id": "r-123", "session": "r-session"}
        }
        
        delete_response = AsyncMock()
        delete_response.raise_for_status = AsyncMock()
        mock_httpx_client.delete.return_value = delete_response
        
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', mock_kernels.copy()):
            with patch("httpx.AsyncClient", return_value=mock_httpx_client):
                with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                    result = await jupyter_kernel_mcp.reset()
                    
                    assert "python3" in result["message"]
                    assert "ir" in result["message"]
                    assert len(jupyter_kernel_mcp.KERNEL_IDS) == 0

    @pytest.mark.asyncio
    async def test_reset_nonexistent_kernel(self):
        """Test reset function for kernel that doesn't exist."""
        with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', {}):
            result = await jupyter_kernel_mcp.reset(kernel="python3")
            
            assert "No python3 kernel to reset" in result["message"]

    @pytest.mark.asyncio
    async def test_stream_execute(self, mock_httpx_client, mock_websocket):
        """Test stream_execute function."""
        # Set up existing kernel
        kernel_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        existing_kernels = {"python3": {"id": kernel_id, "session": session_id}}
        
        # Mock kernel existence check
        check_response = AsyncMock()
        check_response.raise_for_status = AsyncMock()
        mock_httpx_client.get.return_value = check_response
        
        # Mock streaming output
        stream_messages = [
            {"header": {"msg_type": "stream"}, "parent_header": {"msg_id": "test"}, 
             "content": {"name": "stdout", "text": "[10:30:00] Starting...\n"}},
            {"header": {"msg_type": "stream"}, "parent_header": {"msg_id": "test"},
             "content": {"name": "stdout", "text": "[10:30:01] Processing...\n"}},
            {"header": {"msg_type": "execute_reply"}, "parent_header": {"msg_id": "test"},
             "content": {"status": "ok"}},
            {"header": {"msg_type": "status"}, "content": {"execution_state": "idle"}}
        ]
        
        mock_websocket.recv.side_effect = [json.dumps(msg) for msg in stream_messages]
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch("jupyter_kernel_mcp._connect_with_backoff", return_value=mock_websocket):
                with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', existing_kernels):
                    with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                        result = await jupyter_kernel_mcp.stream_execute("long_task()")
                        
                        assert "[10:30:00]" in result["output"]
                        assert "[10:30:01]" in result["output"]
                        assert result["stream_info"]["message_count"] == 2

    def test_get_size_mb(self):
        """Test get_size_mb helper function."""
        # Test with simple object
        assert jupyter_kernel_mcp.get_size_mb(42) > 0
        
        # Test with larger object
        large_list = list(range(10000))
        size = jupyter_kernel_mcp.get_size_mb(large_list)
        assert size > 0.01  # Should be at least 0.01 MB
        
        # Test with string
        text = "Hello" * 1000
        size = jupyter_kernel_mcp.get_size_mb(text)
        assert size > 0