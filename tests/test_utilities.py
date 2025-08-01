"""Tests for utility functions and edge cases."""

import io
import sys
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

# Import the module to test
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jupyter_kernel_mcp


class TestUtilityFunctions:
    """Test utility functions and edge cases."""

    def test_debug_print_disabled(self):
        """Test debug_print when DEBUG is False."""
        with patch.object(jupyter_kernel_mcp, 'DEBUG', False):
            with patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
                jupyter_kernel_mcp.debug_print("Test message")
                output = mock_stderr.getvalue()
                assert output == ""  # No output when DEBUG is False


    def test_kernel_specific_behaviors(self):
        """Test kernel-specific metadata."""
        kernel_tests = [
            ("python3", "python3"),
            ("ir", "ir"),
            ("julia", "julia-1.10"),  # Alias handling
            ("go", "gophernotes"),
            ("rust", "evcxr_jupyter"),
            ("deno", "deno"),
            ("bash", "bash"),
            ("ruby", "ruby3"),
        ]
        
        for input_kernel, expected_spec in kernel_tests:
            spec_name = jupyter_kernel_mcp._get_kernel_spec_name(input_kernel)
            assert spec_name == expected_spec