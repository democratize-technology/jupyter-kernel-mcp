"""Tests for utility functions and edge cases."""

import io
import sys
from unittest.mock import patch, MagicMock
import pytest

# Import the module to test
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jupyter_kernel_mcp


class TestUtilityFunctions:
    """Test utility functions and edge cases."""

    def test_debug_print_enabled(self):
        """Test debug_print when DEBUG is True."""
        with patch.object(jupyter_kernel_mcp, 'DEBUG', True):
            with patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
                jupyter_kernel_mcp.debug_print("Test message", "arg2")
                output = mock_stderr.getvalue()
                assert "[DEBUG]" in output
                assert "Test message arg2" in output

    def test_debug_print_disabled(self):
        """Test debug_print when DEBUG is False."""
        with patch.object(jupyter_kernel_mcp, 'DEBUG', False):
            with patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
                jupyter_kernel_mcp.debug_print("Test message")
                output = mock_stderr.getvalue()
                assert output == ""  # No output when DEBUG is False

    def test_kernel_startup_progress(self):
        """Test KernelStartupProgress class."""
        # Test with mock stderr
        with patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            progress = jupyter_kernel_mcp.KernelStartupProgress("python3")
            
            # Test adding steps
            progress.add_step("Step 1", "🚀")
            progress.add_step("Step 2", "✅")
            
            # Test completion
            progress.complete(success=True)
            
            output = mock_stderr.getvalue()
            assert "Starting Python kernel" in output
            assert "Step 1" in output
            assert "🚀" in output
            assert "✅ Python kernel ready!" in output

    def test_kernel_startup_progress_failure(self):
        """Test KernelStartupProgress with failure."""
        with patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            progress = jupyter_kernel_mcp.KernelStartupProgress("ir")
            progress.add_step("Failed step", "❌")
            progress.complete(success=False)
            
            output = mock_stderr.getvalue()
            assert "❌ R kernel failed to start" in output

    def test_get_kernel_suggestions(self):
        """Test _get_kernel_suggestions function."""
        suggestions = jupyter_kernel_mcp._get_kernel_suggestions()
        
        # Should return dict with kernel types as keys
        assert isinstance(suggestions, dict)
        assert "python3" in suggestions
        assert "ir" in suggestions
        assert "julia-1.10" in suggestions
        
        # Each kernel should have suggestions list
        for kernel, suggs in suggestions.items():
            assert isinstance(suggs, list)
            assert len(suggs) > 0

    def test_environment_variables_missing(self, monkeypatch):
        """Test behavior when environment variables are missing."""
        # Remove all Jupyter env vars
        monkeypatch.delenv("JUPYTER_HOST", raising=False)
        monkeypatch.delenv("JUPYTER_PORT", raising=False)
        monkeypatch.delenv("JUPYTER_TOKEN", raising=False)
        
        # Reload module to pick up missing env vars
        import importlib
        importlib.reload(jupyter_kernel_mcp)
        
        # Should use defaults
        assert jupyter_kernel_mcp.JUPYTER_HOST == "localhost"
        assert jupyter_kernel_mcp.JUPYTER_PORT == "8888"
        assert jupyter_kernel_mcp.JUPYTER_TOKEN is None

    def test_clear_kernel_state_with_confirm(self):
        """Test clear_kernel_state function with confirmation."""
        # This function is defined but never used in the main code
        # Testing for completeness
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "cleared", "error": False}
            
            # Should not execute without confirm=True
            result = asyncio.run(jupyter_kernel_mcp.clear_kernel_state("python3", confirm=False))
            assert "confirm=True" in result["message"]
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_kernel_specific_behaviors(self):
        """Test kernel-specific execution behaviors."""
        # Test Rust kernel special handling
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            with patch.object(jupyter_kernel_mcp, 'KERNEL_IDS', {"rust": {"id": "rust-123", "session": "rust-session"}}):
                # Simulate Rust kernel behavior with delayed output
                await jupyter_kernel_mcp._execute_code("println!(\"Hello\");", "rust")
                # The function has special handling for Rust kernels

    @pytest.mark.asyncio  
    async def test_notebook_edge_cases(self, mock_httpx_client):
        """Test notebook operation edge cases."""
        # Test notebook name without extension
        create_response = AsyncMock()
        create_response.raise_for_status = AsyncMock()
        create_response.json = AsyncMock(return_value={"name": "test.ipynb"})
        mock_httpx_client.put.return_value = create_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.create_notebook("test")  # No .ipynb
                assert result["name"] == "test.ipynb"  # Should add extension

    @pytest.mark.asyncio
    async def test_special_kernel_types(self):
        """Test special handling for different kernel types."""
        kernel_tests = [
            ("python3", "python3"),
            ("ir", "ir"),
            ("julia", "julia-1.10"),  # Alias handling
            ("go", "gophernotes"),
            ("rust", "evcxr_jupyter"),
            ("typescript", "deno"),
            ("deno", "deno"),
            ("bash", "bash"),
            ("ruby", "ruby"),
        ]
        
        for input_kernel, expected_spec in kernel_tests:
            spec_name = jupyter_kernel_mcp._get_kernel_spec_name(input_kernel)
            assert spec_name == expected_spec

    def test_main_guard(self):
        """Test the if __name__ == '__main__' block."""
        # This tests the main execution block
        with patch("jupyter_kernel_mcp.mcp.run") as mock_run:
            with patch.object(jupyter_kernel_mcp, '__name__', '__main__'):
                # Execute the module's main block
                exec(compile(open(jupyter_kernel_mcp.__file__).read(), 
                           jupyter_kernel_mcp.__file__, 'exec'))
                mock_run.assert_called_once()

# Import asyncio for async tests
import asyncio