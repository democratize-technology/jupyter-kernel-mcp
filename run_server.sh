#!/bin/bash
cd "$(dirname "$0")"
exec uv run python jupyter_kernel_mcp.py
