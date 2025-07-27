# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a Model Context Protocol (MCP) server that provides AI assistants with persistent Jupyter kernel execution capabilities. The server maintains state across conversations, enabling true iterative development where variables, imports, and running processes persist between messages.

## Core Architecture

### Main Components

1. **jupyter_kernel_mcp.py** - The main MCP server implementation
   - Uses FastMCP framework for MCP protocol handling
   - Manages WebSocket connections to Jupyter kernels
   - Maintains global state in `KERNEL_IDS` dictionary
   - Supports 8 different kernel types (Python, R, Julia, Go, Rust, TypeScript/Deno, Bash, Ruby)

2. **Configuration System**
   - Environment-based configuration via `.env` file
   - Key variables: `JUPYTER_HOST`, `JUPYTER_PORT`, `JUPYTER_TOKEN`
   - Optional HTTPS/WSS support via `JUPYTER_PROTOCOL` and `JUPYTER_WS_PROTOCOL`

3. **Persistent State Management**
   - Kernels persist across MCP tool calls and conversations
   - Each kernel type maintains its own independent state
   - State includes variables, imports, and execution history

## Development Commands

### Setup and Running

```bash
# Install dependencies (uses uv package manager)
uv sync

# Configure environment
cp .env.example .env
# Edit .env to add your Jupyter token

# Start the server
./run_server.sh
# or
uv run python jupyter_kernel_mcp.py
```

### Getting Jupyter Token

```bash
# If running local Jupyter
jupyter server list
# Copy the token from the output URL
```

## Key Implementation Details

### WebSocket Communication
- The server communicates with Jupyter kernels via WebSocket connections
- Each execution creates a temporary WebSocket connection to stream results
- Handles both immediate outputs and delayed results (e.g., from background processes)

### Error Handling
- Kernel creation failures are handled gracefully with retry logic
- WebSocket disconnections are managed with proper cleanup
- Authentication errors provide clear feedback

### Tool Organization
- **Kernel Management**: `execute`, `stream_execute`, `reset`, `list_available_kernels`
- **Notebook Operations**: `create_notebook`, `add_to_notebook`, `read_notebook`, `list_notebooks`, etc.
- **Search and Analysis**: `search_notebooks`, `get_notebook_stats`

## Important Considerations

1. **State Persistence**: The primary feature is that kernel state persists. Be mindful when using `reset()` as it clears this valuable state.

2. **Authentication**: The server requires a valid Jupyter token. Without it, all operations will fail with authentication errors.

3. **Kernel Readiness**: The server includes logic to ensure kernels are ready before executing code, with up to 30-second timeout for kernel initialization.

4. **Output Streaming**: Two execution modes exist:
   - `execute()`: Returns complete output after execution
   - `stream_execute()`: Provides real-time timestamped output

5. **Multi-Language Support**: Each language has its own kernel specification and maintains separate state.

## Common Patterns

### Working with Persistent State
```python
# First call
execute("import pandas as pd\ndf = pd.read_csv('data.csv')")

# Later call (even in different conversation)
execute("df.head()")  # df is still available
```

### Creating Reproducible Notebooks
```python
# Execute exploratory code
execute("result = complex_analysis()")

# Save successful code to notebook
add_to_notebook("analysis", "result = complex_analysis()")
```

### Managing Multiple Kernels
```python
# Python analysis
execute("result = analyze_data()", kernel="python3")

# R statistics
execute("summary(model)", kernel="r")
```