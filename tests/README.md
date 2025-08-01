# Jupyter Kernel MCP Tests

Test suite for internal utility functions and helpers in the Jupyter Kernel MCP server.

## Test Structure

```
tests/
├── conftest.py                     # Shared fixtures and test configuration
├── test_kernel_management.py       # Tests for kernel spec utilities and HTTP functions
├── test_utilities.py              # Tests for debug utilities and kernel mappings
└── test_websocket_connection.py   # Tests for WebSocket retry logic and connection handling
```

## Running Tests

```bash
# Run all tests
uv run pytest -v

# Run specific test file
uv run pytest tests/test_kernel_management.py -v

# Run with coverage
uv run pytest -v --cov=jupyter_kernel_mcp --cov-report=term-missing
```

## Test Scope

**NOTE**: Due to FastMCP's tool decoration, tests are limited to internal utility functions that don't depend on MCP tools. The following are tested:

### Kernel Management
- `_get_kernel_spec()` - Kernel specification retrieval
- `_get_kernel_type_from_spec()` - Kernel type mapping
- `_get_kernel_spec_name()` - Kernel name resolution
- `_get_available_kernels()` - HTTP client for kernel listing
- Kernel locking mechanisms

### Utilities
- `debug_print()` - Debug output handling
- Kernel type mappings and aliases

### WebSocket Connection
- `_connect_with_backoff()` - Connection retry with exponential backoff
- `_ensure_kernel_ready()` - Kernel readiness checks
- Exception handling in connection logic

## Coverage

Current test coverage focuses on utility functions and internal helpers. MCP tool functions (decorated with `@mcp.tool()`) cannot be tested directly due to FastMCP's function wrapping.

## Adding Tests

When adding new internal utility functions:
1. Add tests to the appropriate test file
2. Use existing fixtures from `conftest.py`
3. Mock external dependencies (WebSocket, HTTP)
4. Follow existing test patterns