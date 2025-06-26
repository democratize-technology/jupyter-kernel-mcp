# Jupyter Kernel MCP Server

A Model Context Protocol (MCP) server that provides AI with a stateful, persistent workspace through Jupyter kernels.

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/democratize-technology/jupyter-kernel-mcp.git
cd jupyter-kernel-mcp
```

### 2. Install Dependencies

This project uses `uv` for dependency management. If you don't have `uv` installed:

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Configure Environment

The server requires configuration to connect to your Jupyter instance. Copy the example configuration file:

```bash
cp .env.example .env
```

Then edit `.env` with your specific settings:

```bash
# For local Jupyter (most common)
JUPYTER_HOST=localhost
JUPYTER_PORT=8888
JUPYTER_TOKEN=your-jupyter-token-here
USE_DOCKER_TOKEN=false

# Or for Docker-based Jupyter
JUPYTER_HOST=your-docker-host-ip
JUPYTER_PORT=8888
DOCKER_CONTAINER_NAME=your-jupyter-container
USE_DOCKER_TOKEN=true
```

### 4. Finding Your Jupyter Token

If you're running Jupyter locally:

```bash
jupyter server list
```

This will show output like:
```
http://localhost:8888/?token=abc123... :: /path/to/notebooks
```

Copy the token value and add it to your `.env` file.

### Configuration Options

#### Jupyter Server Settings

- `JUPYTER_HOST` - The hostname or IP address of your Jupyter server (default: `localhost`)
- `JUPYTER_PORT` - The port your Jupyter server is listening on (default: `8888`)
- `JUPYTER_PROTOCOL` - Protocol for HTTP connections: `http` or `https` (default: `http`)
- `JUPYTER_WS_PROTOCOL` - Protocol for WebSocket connections: `ws` or `wss` (default: `ws`)
- `JUPYTER_TOKEN` - Authentication token for Jupyter (optional)

#### Docker Settings

- `DOCKER_CONTEXT` - Docker context to use, leave empty for default (default: empty)
- `DOCKER_CONTAINER_NAME` - Name of the Docker container running Jupyter (default: `jupyter`)
- `USE_DOCKER_TOKEN` - Whether to get Jupyter token from Docker container (default: `false`)

### Example Configurations

#### Local Jupyter Server (No Docker)

```bash
JUPYTER_HOST=localhost
JUPYTER_PORT=8888
JUPYTER_PROTOCOL=http
JUPYTER_WS_PROTOCOL=ws
JUPYTER_TOKEN=your-token-here
USE_DOCKER_TOKEN=false
```

#### Remote Jupyter Server with HTTPS

```bash
JUPYTER_HOST=jupyter.example.com
JUPYTER_PORT=443
JUPYTER_PROTOCOL=https
JUPYTER_WS_PROTOCOL=wss
JUPYTER_TOKEN=your-secure-token
USE_DOCKER_TOKEN=false
```

#### Docker-based Jupyter

```bash
JUPYTER_HOST=192.168.1.250
JUPYTER_PORT=8888
DOCKER_CONTEXT=remote-docker
DOCKER_CONTAINER_NAME=jupyter-notebook
USE_DOCKER_TOKEN=true
```

## Running the Server

### Prerequisites

1. **Jupyter Server**: You need a running Jupyter server. If you don't have one:

   ```bash
   # Install Jupyter
   pip install jupyter
   
   # Start Jupyter (in a separate terminal)
   jupyter notebook --no-browser
   ```

2. **Environment Configuration**: Make sure you've created and configured your `.env` file (see Setup section above)

### Starting the MCP Server

Once Jupyter is running and configured:

```bash
./run_server.sh
```

Or directly with Python:

```bash
uv run python jupyter_kernel_mcp.py
```

The server will attempt to connect to your Jupyter instance using the configuration in your `.env` file.

## MCP Client Configuration

To use this server with an MCP client (like Claude Desktop), add this to your MCP settings:

```json
{
  "jupyter-kernel": {
    "command": "/path/to/jupyter-kernel-mcp/run_server.sh"
  }
}
```

Replace `/path/to/jupyter-kernel-mcp` with the actual path where you cloned this repository.

## Troubleshooting

### Connection Refused

If you get connection errors:
- Verify Jupyter is running: `jupyter server list`
- Check your `JUPYTER_HOST` and `JUPYTER_PORT` settings
- Ensure your firewall allows connections to the Jupyter port

### Authentication Failed

If you get authentication errors:
- Verify your `JUPYTER_TOKEN` is correct
- Try regenerating the token by restarting Jupyter
- If using Docker, ensure `USE_DOCKER_TOKEN=true` and the container name is correct

### Docker Issues

If using Docker-based Jupyter:
- Ensure Docker is running: `docker ps`
- Verify the container name matches `DOCKER_CONTAINER_NAME`
- Check if you need to specify a `DOCKER_CONTEXT`