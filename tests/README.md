# Jupyter Kernel MCP Tests

Comprehensive test suite for the Jupyter Kernel MCP server with 100% code coverage target.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and test configuration
├── test_websocket_connection.py    # WebSocket connection and retry logic tests
├── test_kernel_management.py       # Kernel creation and management tests
├── test_mcp_tools.py              # MCP tool functions tests
├── test_notebook_operations.py     # Notebook CRUD operations tests
└── test_utilities.py              # Utility functions and edge cases
```

## Running Tests

### Quick Run
```bash
./run_tests.sh
```

### Manual Run
```bash
# Install test dependencies
uv pip install -e ".[test]"

# Run all tests with coverage
uv run pytest -v --cov=jupyter_kernel_mcp --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_websocket_connection.py -v

# Run with specific markers
uv run pytest -m "not slow" -v
```

## Coverage Reports

- **Terminal**: Shows missing lines directly in terminal output
- **HTML**: Open `htmlcov/index.html` for interactive coverage report
- **JSON**: `coverage.json` for programmatic access

## Test Categories

### 1. WebSocket Connection Tests
- Connection retry with exponential backoff
- Authentication header handling
- Kernel readiness checks
- Connection failure scenarios

### 2. Kernel Management Tests
- Kernel creation and caching
- Kernel state persistence with locking
- Kernel recreation on failure
- Multi-kernel type support

### 3. MCP Tool Tests
- Code execution (compute, execute, q)
- Variable inspection (vars, workspace)
- Kernel state management (reset, kernel_state)
- AI assistance (suggest_next)
- Streaming execution for long-running tasks

### 4. Notebook Operations Tests
- Create, read, update, delete notebooks
- Cell execution and management
- Markdown cell support
- Notebook search and statistics
- Batch operations (run all cells, clear outputs)

### 5. Utility Tests
- Debug printing
- Progress indicators
- Environment variable handling
- Edge cases and error conditions

## Key Testing Patterns

### Mocking WebSockets
```python
mock_websocket = AsyncMock()
mock_websocket.send = AsyncMock()
mock_websocket.recv = AsyncMock()
mock_websocket.__aenter__ = AsyncMock(return_value=mock_websocket)
mock_websocket.__aexit__ = AsyncMock()
```

### Mocking HTTP Clients
```python
mock_httpx_client = AsyncMock()
mock_response = AsyncMock()
mock_response.json = AsyncMock(return_value={"data": "value"})
mock_httpx_client.get.return_value = mock_response
```

### Testing Async Functions
```python
@pytest.mark.asyncio
async def test_async_function():
    result = await function_under_test()
    assert result == expected
```

## Coverage Goals

- **Target**: 100% code coverage
- **Current**: Run `./run_tests.sh` to see current coverage
- **Excluded**: Only truly unreachable code (e.g., `if __name__ == "__main__"` in imports)

## Adding New Tests

1. Identify untested code paths using coverage report
2. Add test cases to appropriate test file
3. Use existing fixtures from `conftest.py`
4. Follow naming convention: `test_<feature>_<scenario>`
5. Ensure async tests use `@pytest.mark.asyncio`
6. Mock external dependencies (WebSocket, HTTP, etc.)

## Continuous Integration

Tests are configured to:
- Fail if coverage drops below 100%
- Generate coverage reports in multiple formats
- Run in parallel for faster execution
- Timeout long-running tests

## Debugging Tests

```bash
# Run with debugging output
uv run pytest -v -s

# Run specific test with pdb
uv run pytest -k "test_name" --pdb

# Show test durations
uv run pytest --durations=10
```