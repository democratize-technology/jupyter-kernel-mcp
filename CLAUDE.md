# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a Model Context Protocol (MCP) server that provides AI assistants with persistent Jupyter kernel execution capabilities. The server maintains state across conversations, enabling true iterative development where variables, imports, and running processes persist between messages.

## Core Architecture

### Main Components

1. **jupyter_kernel_mcp.py** (4,405 lines) - The main MCP server implementation
   - Monolithic design containing all server functionality
   - Uses FastMCP framework for MCP protocol handling
   - Manages WebSocket connections to Jupyter kernels
   - Maintains global state in `KERNEL_IDS` dictionary
   - Implements 25 MCP tools for kernel execution and notebook management
   - Supports 8 different kernel types (Python, R, Julia, Go, Rust, TypeScript/Deno, Bash, Ruby)

2. **Configuration System**
   - Environment-based configuration via `.env` file
   - Key variables: `JUPYTER_HOST`, `JUPYTER_PORT`, `JUPYTER_TOKEN`
   - Optional HTTPS/WSS support via `JUPYTER_PROTOCOL` and `JUPYTER_WS_PROTOCOL`
   - Token retrieval helpers: `auto_token_retrieval.py` and `get_jupyter_token.sh`

3. **Persistent State Management**
   - Kernels persist across MCP tool calls and conversations
   - Each kernel type maintains its own independent state
   - State includes variables, imports, and execution history
   - Global state stored in: `KERNEL_IDS = {kernel_type: {"id": kernel_id, "session": session_id}}`

## Development Commands

### Setup and Running

```bash
# Install dependencies (uses uv package manager - NOT pip)
uv sync

# Configure environment
cp .env.example .env
# Edit .env to add your Jupyter token (required)

# Start the MCP server
./run_server.sh
# or directly:
uv run python jupyter_kernel_mcp.py

# Get Jupyter token (if running local Jupyter)
jupyter server list
# or use helper script:
./get_jupyter_token.sh
```

### Testing
**WARNING**: No test suite exists. When adding new features or fixing bugs, manually test all affected MCP tools.

### Code Quality
**WARNING**: No linting, formatting, or type checking tools are configured. Maintain consistency with existing code style when making changes.

## Key Implementation Details

### WebSocket Communication Pattern
- Each execution creates a temporary WebSocket connection
- Automatic reconnection with exponential backoff (up to 5 retries)
- Handles both immediate outputs and delayed results
- Connection pattern in `execute_code()` function (lines ~500-800)

### Tool Categories and Key Functions
- **Core Execution**: `compute()`, `execute()`, `stream_execute()`, `q()` (quick execute)
- **State Management**: `vars()`, `workspace()`, `kernel_state()`, `reset()`
- **AI Discovery**: `suggest_next()` - provides context-aware code suggestions
- **Kernel Ops**: `list_available_kernels()`, `get_kernel_info()`
- **Notebooks**: 15 tools including create, add, read, update, delete, search

### Error Handling Strategy
- All MCP tools wrapped in try-except blocks
- User-friendly error messages with specific guidance
- Kernel creation failures handled with retry logic
- WebSocket disconnections managed with proper cleanup
- Authentication errors provide clear feedback

### Critical Functions Reference
- Kernel creation: `create_kernel()` at line ~300
- WebSocket execution: `execute_code()` at line ~500
- State persistence: Global `KERNEL_IDS` dictionary
- Tool registration: FastMCP decorators throughout

## Important Architectural Decisions

1. **Monolithic Design**: All functionality in single file - consider this when adding features
2. **Stateful by Design**: The primary feature is persistence - be extremely careful with `reset()`
3. **No Database**: All state is in-memory - server restart loses all kernel state
4. **Synchronous Tool Interface**: Despite async internals, MCP tools present synchronous interface
5. **One Kernel Per Type**: Only one instance of each kernel type can exist simultaneously

## Common Development Patterns

### Adding New MCP Tools
```python
@mcp.tool()
async def new_tool(param: str) -> str:
    """Tool description for MCP discovery."""
    try:
        # Implementation
        return result
    except Exception as e:
        logger.error(f"Error in new_tool: {e}")
        return f"Error: {str(e)}"
```

### Working with Kernel State
```python
# Always check if kernel exists
if kernel_type in KERNEL_IDS:
    kernel_id = KERNEL_IDS[kernel_type]["id"]
else:
    # Create new kernel
    kernel_info = await create_kernel(kernel_type)
```

### WebSocket Communication
```python
# Standard pattern for WebSocket communication
ws_url = f"{ws_protocol}://{host}:{port}/api/kernels/{kernel_id}/channels"
async with websockets.connect(ws_url, ...) as websocket:
    # Send execute request
    # Handle responses
```

## Known Limitations and Gotchas

1. **No Test Coverage**: Any changes require manual testing of affected tools
2. **Memory Leaks Risk**: Long-running kernels may accumulate memory
3. **Single Threading**: One WebSocket connection at a time per kernel
4. **No Metrics**: No performance monitoring or usage statistics
5. **Token Required**: Will not function without valid Jupyter token

## Future Refactoring Targets

When making significant changes, consider:
1. Breaking monolithic file into modules (kernel_manager, notebook_manager, mcp_tools)
2. Adding comprehensive test suite with mocked WebSocket connections
3. Implementing proper logging levels and structured logging
4. Adding type hints throughout the codebase
5. Creating abstract base classes for kernel operations

## Debugging Tips

1. Enable debug logging by checking logger configuration
2. WebSocket issues: Check `JUPYTER_WS_PROTOCOL` matches your Jupyter setup
3. Authentication failures: Verify token with `curl` to Jupyter API
4. Kernel state issues: Check `KERNEL_IDS` global variable
5. Use `kernel_state()` tool to inspect current state

## Security Considerations

1. Jupyter token is sensitive - never log or expose it
2. All code execution happens on the Jupyter server (not locally)
3. No input sanitization - assumes trusted environment
4. WebSocket connections use token-based authentication