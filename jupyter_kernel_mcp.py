#!/usr/bin/env python3
"""
Jupyter Kernel MCP Server - AI's Persistent Computational Workspace

This MCP server provides AI with a stateful, persistent workspace that survives
across conversations, enabling true long-term collaboration and iterative development.

Key Advantages:
1. **Persistent State Management**: Variables and imports persist between calls,
   enabling iterative development without restarting from scratch
2. **Multi-Language Support**: Switch between 8 languages while maintaining
   separate kernel states for each
3. **Cross-Conversation Continuity**: Work started in one conversation continues
   seamlessly in the next - models stay loaded, data remains in memory
4. **Real-Time Monitoring**: Stream output with timestamps for long-running
   operations like model training or data processing
5. **Programmatic Notebook Building**: Create reproducible notebooks with actual
   execution results, not just code

This fundamentally changes AI assistance from stateless Q&A to ongoing collaborative
projects with true working memory.
"""

from fastmcp import FastMCP
import httpx
import subprocess
import os
import websockets
import json
import uuid
import asyncio
import sys
from datetime import datetime

# Helper function to print debug messages to stderr
def debug_print(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr)

# Create MCP server
mcp = FastMCP("jupyter-kernel")

# Configuration from environment variables
CONFIG = {
    # Jupyter server configuration
    "JUPYTER_HOST": os.environ.get("JUPYTER_HOST", "localhost"),
    "JUPYTER_PORT": os.environ.get("JUPYTER_PORT", "8888"),
    "JUPYTER_PROTOCOL": os.environ.get("JUPYTER_PROTOCOL", "http"),
    "JUPYTER_WS_PROTOCOL": os.environ.get("JUPYTER_WS_PROTOCOL", "ws"),
}

# Build Jupyter URLs from config
JUPYTER_URL = f"{CONFIG['JUPYTER_PROTOCOL']}://{CONFIG['JUPYTER_HOST']}:{CONFIG['JUPYTER_PORT']}"
JUPYTER_WS_URL = f"{CONFIG['JUPYTER_WS_PROTOCOL']}://{CONFIG['JUPYTER_HOST']}:{CONFIG['JUPYTER_PORT']}"

# Get Jupyter token from environment
JUPYTER_TOKEN = os.environ.get("JUPYTER_TOKEN")
if not JUPYTER_TOKEN:
    debug_print("WARNING: No JUPYTER_TOKEN found in environment. Authentication will fail.")
else:
    debug_print(f"Got Jupyter token: {JUPYTER_TOKEN[:8]}...")

# Global dictionary to store kernel IDs and session IDs by kernel type
# Structure: {kernel_type: {"id": kernel_id, "session": session_id}}
KERNEL_IDS = {}

def _get_kernel_spec(kernel_type: str) -> dict:
    """Get kernel specification based on kernel type"""
    kernel_specs = {
        "python3": {
            "name": "python3",
            "display_name": "Python 3",
            "language": "python"
        },
        "deno": {
            "name": "deno",
            "display_name": "Deno",
            "language": "typescript"
        },
        "julia": {
            "name": "julia-1.10",
            "display_name": "Julia 1.10",
            "language": "julia"
        },
        "r": {
            "name": "ir",
            "display_name": "R",
            "language": "r"
        },
        "go": {
            "name": "gophernotes",
            "display_name": "Go",
            "language": "go"
        },
        "rust": {
            "name": "rust",
            "display_name": "Rust",
            "language": "rust"
        },
        "bash": {
            "name": "bash",
            "display_name": "Bash",
            "language": "bash"
        },
        "ruby": {
            "name": "ruby3",
            "display_name": "Ruby 3",
            "language": "ruby"
        }
    }

    # Return requested kernel spec or fallback to a sensible default
    if kernel_type in kernel_specs:
        return kernel_specs[kernel_type]

    # If requested kernel not found, return python3 as fallback
    # In production, you might want to query Jupyter for available kernels
    debug_print(f"Warning: Unknown kernel type '{kernel_type}', falling back to python3")
    return kernel_specs["python3"]

def _get_kernel_type_from_spec(kernel_spec_name: str) -> str:
    """Map kernel spec name back to our kernel type"""
    spec_to_type = {
        "python3": "python3",
        "deno": "deno",
        "julia-1.10": "julia",
        "ir": "r",
        "gophernotes": "go",
        "rust": "rust",
        "bash": "bash",
        "ruby3": "ruby"
    }
    return spec_to_type.get(kernel_spec_name, "python3")

def _get_kernel_spec_name(kernel_type: str) -> str:
    """Map our kernel type to the actual kernel spec name used by Jupyter"""
    type_to_spec = {
        "python3": "python3",
        "deno": "deno",
        "julia": "julia-1.10",
        "julia-1.10": "julia-1.10",  # Direct mapping
        "r": "ir",
        "ir": "ir",  # Direct mapping
        "go": "gophernotes",
        "gophernotes": "gophernotes",  # Direct mapping
        "rust": "rust",
        "bash": "bash",
        "ruby": "ruby3",
        "ruby3": "ruby3"  # Direct mapping
    }
    return type_to_spec.get(kernel_type, "python3")

async def _get_available_kernels(jupyter_url: str, headers: dict) -> dict:
    """Get list of available kernels from Jupyter"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{jupyter_url}/api/kernelspecs",
                headers=headers
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        debug_print(f"Warning: Could not fetch available kernels: {e}")
        return {"kernelspecs": {}}

async def _ensure_kernel_ready(kernel_id: str, session_id: str, ws_url: str, headers: dict) -> bool:
    """Ensure kernel is ready by sending kernel_info_request"""
    ws_endpoint = f"{ws_url}/api/kernels/{kernel_id}/channels"
    if JUPYTER_TOKEN:
        ws_endpoint += f"?token={JUPYTER_TOKEN}"

    try:
        async with websockets.connect(ws_endpoint, subprotocols=['kernel.v5.2']) as ws:
            # Send kernel_info_request
            msg_id = str(uuid.uuid4())
            kernel_info_msg = {
                "header": {
                    "msg_id": msg_id,
                    "msg_type": "kernel_info_request",
                    "session": session_id,
                    "username": "mcp",
                    "version": "5.2",
                    "date": datetime.utcnow().isoformat() + "Z"
                },
                "parent_header": {},
                "metadata": {},
                "content": {},
                "channel": "shell"
            }

            await ws.send(json.dumps(kernel_info_msg))

            # Wait for kernel_info_reply (with timeout)
            try:
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < 5.0:
                    message = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    msg_data = json.loads(message)
                    if (msg_data.get("header", {}).get("msg_type") == "kernel_info_reply" and
                        msg_data.get("parent_header", {}).get("msg_id") == msg_id):
                        debug_print(f"Kernel {kernel_id} is ready")
                        return True
            except asyncio.TimeoutError:
                pass

        return False
    except Exception as e:
        debug_print(f"Error checking kernel readiness: {e}")
        return False

async def _execute_code(code: str, kernel: str = "python3") -> dict:
    """Internal function to execute code in a Jupyter kernel"""
    global KERNEL_IDS

    # Map user-friendly kernel type to actual kernel spec name
    kernel_spec_name = _get_kernel_spec_name(kernel)
    debug_print(f"Mapping kernel '{kernel}' to spec name '{kernel_spec_name}'")

    # Use configured Jupyter URLs
    jupyter_url = JUPYTER_URL
    ws_url = JUPYTER_WS_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Check if we need to create a new kernel
    if kernel not in KERNEL_IDS:
        async with httpx.AsyncClient() as client:
            # Check if kernel is available before attempting to create
            kernels_data = await _get_available_kernels(jupyter_url, headers)
            available_kernels = list(kernels_data.get("kernelspecs", {}).keys())

            if kernel_spec_name not in available_kernels:
                debug_print(f"Warning: Kernel spec '{kernel_spec_name}' not found in available kernels: {available_kernels}")
                debug_print(f"Attempting to create anyway...")

            try:
                # Create kernel with specific kernel spec
                kernel_data = {"name": kernel_spec_name}
                debug_print(f"Attempting to create {kernel} kernel (spec: {kernel_spec_name})...")
                response = await client.post(
                    f"{jupyter_url}/api/kernels",
                    headers=headers,
                    json=kernel_data
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                debug_print(f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}")
                if e.response.status_code == 500:
                    debug_print(f"Kernel spec '{kernel_spec_name}' may not be available in Jupyter.")
                    debug_print(f"Available kernels: {available_kernels}")
                    debug_print(f"Response text: {e.response.text}")
                raise Exception(f"Cannot create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}. Available kernels: {available_kernels}")
            except Exception as e:
                debug_print(f"Error creating {kernel} kernel (spec: {kernel_spec_name}): {e}")
                raise Exception(f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {str(e)}")

            kernel_info = response.json()
            kernel_session = str(uuid.uuid4())
            KERNEL_IDS[kernel] = {
                "id": kernel_info["id"],
                "session": kernel_session
            }

            debug_print(f"Created {kernel} kernel: {KERNEL_IDS[kernel]['id']} with session: {KERNEL_IDS[kernel]['session'][:8]}...")

            # Add kernel initialization check for Deno and Rust
            if kernel in ["deno", "rust"]:
                debug_print(f"Waiting for {kernel} kernel to initialize...")
                ready = await _ensure_kernel_ready(KERNEL_IDS[kernel]["id"], KERNEL_IDS[kernel]["session"], ws_url, headers)
                if not ready:
                    debug_print(f"Warning: {kernel} kernel may not be fully ready")
                # Give it a bit more time for runtime initialization
                if kernel == "rust":
                    await asyncio.sleep(2.0)  # Rust needs more time for compilation environment
                else:
                    await asyncio.sleep(1.0)
    else:
        debug_print(f"Using existing {kernel} kernel: {KERNEL_IDS[kernel]['id']}")

        # Verify the kernel still exists before trying to connect
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{jupyter_url}/api/kernels/{KERNEL_IDS[kernel]['id']}",
                    headers=headers
                )
                if response.status_code == 404:
                    debug_print(f"Kernel {KERNEL_IDS[kernel]['id']} no longer exists, removing from cache")
                    del KERNEL_IDS[kernel]
                    # Recursively call to create a new kernel
                    return await _execute_code(code, kernel)
                response.raise_for_status()
                debug_print(f"Confirmed kernel {KERNEL_IDS[kernel]['id']} still exists")
        except Exception as e:
            debug_print(f"Error checking kernel existence: {e}")
            debug_print(f"Removing cached kernel and creating new one")
            del KERNEL_IDS[kernel]
            return await _execute_code(code, kernel)

    # Connect via WebSocket
    ws_endpoint = f"{ws_url}/api/kernels/{KERNEL_IDS[kernel]['id']}/channels"
    if JUPYTER_TOKEN:
        ws_endpoint += f"?token={JUPYTER_TOKEN}"

    messages = []

    async with websockets.connect(ws_endpoint, subprotocols=['kernel.v5.2']) as websocket:
        # Create execute_request message
        msg_id = str(uuid.uuid4())
        execute_msg = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "session": KERNEL_IDS[kernel]["session"],
                "username": "mcp",
                "version": "5.2",
                "date": datetime.utcnow().isoformat() + "Z"
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False
            },
            "channel": "shell"
        }

        # Send the execute request
        await websocket.send(json.dumps(execute_msg))
        debug_print(f"Sent execute_request for: {code}")

        # Receive messages until we get execute_reply AND idle state
        output_text = []
        error_text = None
        reply_received = False
        execution_state_idle = False

        # For Deno, we need to wait for both execute_reply AND execution_state: idle
        timeout = 30.0
        start_time = asyncio.get_event_loop().time()

        try:
            while not (reply_received and execution_state_idle):
                if (asyncio.get_event_loop().time() - start_time) > timeout:
                    debug_print(f"Timeout waiting for {kernel} kernel")
                    error_text = f"Timeout: No complete response from {kernel} kernel after {timeout} seconds"
                    break

                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                msg_data = json.loads(message)
                msg_type = msg_data.get("header", {}).get("msg_type", "")
                parent_msg_id = msg_data.get("parent_header", {}).get("msg_id", "")

                debug_print(f"Received: {msg_type} (parent: {parent_msg_id})")

                # For status messages, check execution state
                if msg_type == "status":
                    exec_state = msg_data.get("content", {}).get("execution_state", "")
                    debug_print(f"  Execution state: {exec_state}")
                    if exec_state == "idle" and reply_received:
                        # For Rust, wait a bit more to catch additional stream messages
                        if kernel == "rust":
                            debug_print(f"  Rust idle detected, waiting for additional messages...")
                            # Continue collecting messages for a short time
                            idle_time = asyncio.get_event_loop().time()
                            while (asyncio.get_event_loop().time() - idle_time) < 1.0:
                                try:
                                    additional_msg = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                                    additional_data = json.loads(additional_msg)
                                    additional_type = additional_data.get("header", {}).get("msg_type", "")
                                    additional_parent = additional_data.get("parent_header", {}).get("msg_id", "")

                                    debug_print(f"  Additional message: {additional_type} (parent: {additional_parent})")

                                    # Process additional stream messages
                                    if additional_parent == msg_id and additional_type == "stream":
                                        content = additional_data.get("content", {})
                                        text = content.get("text", "")
                                        if text:
                                            output_text.append(text)
                                            debug_print(f"    Additional stream output: {text.strip()}")
                                except asyncio.TimeoutError:
                                    continue
                                except:
                                    break
                            debug_print(f"  Finished collecting additional Rust messages")
                        execution_state_idle = True

                # Only process our messages for actual content
                if parent_msg_id == msg_id:
                    if msg_type == "stream":
                        content = msg_data.get("content", {})
                        text = content.get("text", "")
                        if text:
                            output_text.append(text)
                            debug_print(f"  Stream output: {text.strip()}")

                    elif msg_type == "display_data":
                        content = msg_data.get("content", {})
                        data = content.get("data", {})
                        if "text/plain" in data:
                            output_text.append(data["text/plain"])
                            debug_print(f"  Display data: {data['text/plain'].strip()}")

                    elif msg_type == "execute_result":
                        content = msg_data.get("content", {})
                        data = content.get("data", {})
                        if "text/plain" in data:
                            output_text.append(data["text/plain"])
                            debug_print(f"  Execute result: {data['text/plain'].strip()}")

                    elif msg_type == "error":
                        content = msg_data.get("content", {})
                        ename = content.get("ename", "Error")
                        evalue = content.get("evalue", "")
                        error_text = f"{ename}: {evalue}"
                        debug_print(f"  Error: {error_text}")

                    elif msg_type == "execute_reply":
                        debug_print(f"  Got execute_reply, status: {msg_data.get('content', {}).get('status', 'unknown')}")
                        reply_received = True
                        # For Python kernels, this might be enough
                        # Other kernels may need to wait for explicit idle state
                        if kernel in ["python3", "ir", "julia-1.10", "bash"]:
                            execution_state_idle = True

        except Exception as e:
            debug_print(f"Error during message collection: {e}")
            error_text = str(e)

    # Return error if we got one
    if error_text:
        return {
            "output": error_text,
            "error": True
        }

    # Otherwise return normal output
    output = "".join(output_text)
    return {
        "output": output,
        "error": False
    }

@mcp.tool()
async def execute(code: str, kernel: str = "python3") -> dict:
    """
    Execute code in a Jupyter kernel with persistent state.

    THE KEY INSIGHT: Variables persist across calls AND conversations. This enables
    true iterative development where each execution builds on the previous state.

    Args:
        code: The code to execute. Multi-line strings are supported.
        kernel: Kernel name (default: "python3"). Available kernels:
               - python3: Data science, ML, general purpose
               - julia-1.10: High-performance numerical computing
               - ir: R statistical computing (note: 'ir' not 'r')
               - ruby3: Scripting and web tools
               - gophernotes: Go programming (no generics support)
               - rust: Systems programming (1-2s compilation)
               - bash: Shell scripting in /home/jovyan
               - deno: TypeScript/JavaScript runtime

    Returns:
        dict: {
            "output": str,  # Execution output or error message
            "error": bool   # True if an error occurred
        }

    Examples:
        # PERSISTENT STATE EXAMPLE - Data Science Workflow
        # Monday: Load expensive dataset
        >>> execute(code='''
        ... import pandas as pd
        ... df = pd.read_csv('sales_data.csv')  # 10GB file
        ... print(f"Loaded {len(df)} records")
        ... ''')
        {"output": "Loaded 50000000 records\\n", "error": false}

        # Wednesday: Data is STILL loaded, continue analysis
        >>> execute(code="print(f'DataFrame still in memory: {df.shape}')")
        {"output": "DataFrame still in memory: (50000000, 47)\\n", "error": false}

        # ERRORS DON'T DESTROY YOUR SESSION
        >>> execute(code="result = 1/0  # Oops!")
        {"output": "ZeroDivisionError: division by zero", "error": true}

        >>> execute(code="print(f'df still here: {len(df)} rows')")
        {"output": "df still here: 50000000 rows\\n", "error": false}

        # CROSS-CONVERSATION COLLABORATION
        # Human in chat 1: "I started training this model"
        >>> execute(code='''
        ... model = train_complex_model(df, epochs=50)
        ... model.save('checkpoint.h5')
        ... ''')

        # AI in chat 2: "I see the model is still in memory"
        >>> execute(code="print(f'Model params: {model.count_params()}')")

        # MULTI-LANGUAGE with separate states
        >>> execute(code="x = 42", kernel="python3")
        >>> execute(code="x = 99", kernel="ruby3")
        >>> execute(code="print(x)", kernel="python3")
        {"output": "42\\n", "error": false}  # Each kernel is isolated

    When to use execute():
        - Quick exploration and testing ("what does this return?")
        - Building up state incrementally (load data, transform, analyze)
        - Continuing work from previous conversations
        - Testing code before adding to notebooks

    Note:
        - Kernels persist until reset() is called
        - No memory limits - can load large models/datasets
        - Errors are caught and reported without killing the kernel
        - For operations >5 seconds, use stream_execute() to see progress
    """
    return await _execute_code(code, kernel)

@mcp.tool()
async def list_available_kernels() -> dict:
    """
    List all available Jupyter kernels.

    Returns detailed information about each kernel including display names
    and supported languages.

    Returns:
        dict: {
            "available_kernels": list[dict],  # Kernel information
            "total_count": int                # Number of kernels
        }

        Each kernel dict contains:
        - name: Exact kernel identifier (use this in other calls)
        - display_name: Human-readable name
        - language: Programming language

    Example:
        >>> list_available_kernels()
        {
            "available_kernels": [
                {"name": "python3", "display_name": "Python 3 (ipykernel)", "language": "python"},
                {"name": "julia-1.10", "display_name": "Julia 1.10.0", "language": "julia"},
                {"name": "ir", "display_name": "R", "language": "R"},
                {"name": "ruby3", "display_name": "Ruby 3 (iruby kernel)", "language": "ruby"},
                {"name": "gophernotes", "display_name": "Go", "language": "go"},
                {"name": "rust", "display_name": "Rust", "language": "rust"},
                {"name": "bash", "display_name": "Bash", "language": "bash"},
                {"name": "deno", "display_name": "Deno", "language": "typescript"}
            ],
            "total_count": 8
        }

    Important:
        Always use the exact 'name' field when specifying kernels in other calls.
        Common mistakes: using 'r' instead of 'ir', 'julia' instead of 'julia-1.10'.
    """
    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    kernels_data = await _get_available_kernels(jupyter_url, headers)
    kernelspecs = kernels_data.get("kernelspecs", {})

    available_kernels = []
    for name, spec in kernelspecs.items():
        kernel_info = {
            "name": name,
            "display_name": spec.get("spec", {}).get("display_name", name),
            "language": spec.get("spec", {}).get("language", "unknown")
        }
        available_kernels.append(kernel_info)

    debug_print(f"Found {len(available_kernels)} available kernels")

    return {
        "available_kernels": available_kernels,
        "total_count": len(available_kernels)
    }

@mcp.tool()
async def reset(kernel: str = None) -> dict:
    """
    Delete kernel(s) and clear stored kernel state.

    IMPORTANT: This clears AI's persistent workspace! Use sparingly - the whole
    point is to maintain state across conversations.

    Args:
        kernel: Specific kernel to reset (e.g., 'python3', 'julia-1.10').
               If None, resets all kernels.

    Returns:
        dict: {"message": str}  # Confirmation message

    Examples:
        # Reset specific kernel
        >>> reset(kernel="python3")
        {"message": "Deleted kernel: python3"}

        # Reset all kernels
        >>> reset()
        {"message": "All kernels reset"}

        # Variables are cleared after reset
        >>> execute(code="x = 100")
        {"output": "", "error": false}
        >>> reset(kernel="python3")
        {"message": "Deleted kernel: python3"}
        >>> execute(code="print(x)")
        {"output": "NameError: name 'x' is not defined", "error": true}

    When to reset (rarely!):\n        - Starting completely unrelated project\n        - Recovering from corrupted state\n        - Explicitly requested memory cleanup\n        \n    When NOT to reset:\n        - Just because it's a new conversation\n        - Minor errors (kernel survives errors!)\n        - Switching between related tasks\n        \n    Remember: Persistent state enables AI to continue work across\n    conversations. Reset breaks this continuity!
    """
    global KERNEL_IDS

    if not KERNEL_IDS:
        return {"message": "No kernels to reset"}

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Determine which kernels to reset
    if kernel:
        if kernel not in KERNEL_IDS:
            return {"message": f"No {kernel} kernel to reset"}
        kernels_to_reset = {kernel: KERNEL_IDS[kernel]}
    else:
        kernels_to_reset = KERNEL_IDS.copy()

    # Delete the kernel(s)
    deleted_kernels = []
    async with httpx.AsyncClient() as client:
        for kernel_name, kernel_info in kernels_to_reset.items():
            try:
                kernel_id = kernel_info["id"]
                response = await client.delete(
                    f"{jupyter_url}/api/kernels/{kernel_id}",
                    headers=headers
                )
                response.raise_for_status()
                debug_print(f"Deleted {kernel_name} kernel: {kernel_id}")
                deleted_kernels.append(kernel_name)
                del KERNEL_IDS[kernel_name]
            except Exception as e:
                debug_print(f"Failed to delete {kernel_name} kernel: {e}")

    if deleted_kernels:
        return {"message": f"Deleted kernels: {', '.join(deleted_kernels)}"}
    else:
        return {"message": "No kernels were deleted"}

@mcp.tool()
async def create_notebook(name: str, kernel: str = "python3") -> dict:
    """
    Create a new Jupyter notebook.

    DECISION GUIDE: Use this when you want to save and share your analysis,
    not just run code. Perfect for creating reproducible workflows.

    Args:
        name: Notebook name (without .ipynb extension)
        kernel: Kernel type (default: "python3")

    Returns:
        dict: {
            "path": str,         # Full path with .ipynb extension
            "kernel": str,       # Kernel used
            "kernel_spec": dict, # Kernel specification
            "created": bool      # Success flag
        }

    Examples:
        >>> create_notebook(name="data_analysis", kernel="python3")
        {
            "path": "data_analysis.ipynb",
            "kernel": "python3",
            "kernel_spec": {"name": "python3", "display_name": "Python 3", "language": "python"},
            "created": true
        }

        >>> create_notebook(name="stats_project", kernel="ir")  # R notebook
        {"path": "stats_project.ipynb", "kernel": "ir", "created": true}

    When to use notebooks vs execute():

        Use execute() when:
        - Exploring and testing ideas
        - You don't need to save the process
        - Quick one-off calculations

        Use notebooks when:
        - Building something others will use
        - Creating documentation alongside code
        - You want reproducible analysis
        - Sharing results with team/community

    Note:
        - Don't include .ipynb in the name
        - Notebooks are created in the current working directory
        - Use meaningful names with underscores or hyphens
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Get kernel specification
    kernel_spec = _get_kernel_spec(kernel)

    # Create notebook content
    notebook_content = {
        "type": "notebook",
        "content": {
            "cells": [],
            "metadata": {
                "kernelspec": kernel_spec
            },
            "nbformat": 4,
            "nbformat_minor": 5
        }
    }

    # PUT to create the notebook
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{jupyter_url}/api/contents/{name}.ipynb",
            headers=headers,
            json=notebook_content
        )
        response.raise_for_status()

    debug_print(f"Created notebook: {name}.ipynb with {kernel} kernel")

    return {
        "path": f"{name}.ipynb",
        "kernel": kernel,
        "kernel_spec": kernel_spec,
        "created": True
    }

@mcp.tool()
async def add_to_notebook(notebook: str, code: str) -> dict:
    """
    Execute code and add it as a cell to a notebook.

    CRITICAL INSIGHT: This uses the SAME kernel state as execute()!
    If you've been exploring with execute(), you can save successful
    code to a notebook without re-running everything.

    Args:
        notebook: Notebook name (without .ipynb)
        code: Code to execute and add

    Returns:
        dict: {
            "output": str,      # Execution output
            "cell_number": int  # Cell index in notebook
        }

    Examples:
        # EXPLORATION TO NOTEBOOK WORKFLOW
        # Step 1: Explore with execute()
        >>> execute(code="import pandas as pd")
        >>> execute(code="df = pd.read_csv('sales.csv')")
        >>> execute(code="df['profit'] = df['revenue'] - df['costs']")
        >>> execute(code="df.head()")  # Looks good!

        # Step 2: Start documenting the working code
        >>> create_notebook(name="profit_analysis")
        >>> add_to_notebook(
        ...     notebook="profit_analysis",
        ...     code='''
        ... # This uses the ALREADY LOADED df!
        ... print(f"Analyzing {len(df)} sales records")
        ... profit_summary = df.groupby('region')['profit'].sum()
        ... print(profit_summary)
        ... '''
        ... )
        # The notebook gets the actual output from your current session!

        # ITERATIVE NOTEBOOK BUILDING
        >>> add_to_notebook(
        ...     notebook="analysis",
        ...     code='''
        ... import pandas as pd
        ... data = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
        ... print(data.describe())
        ... '''
        ... )
        {
            "output": "       x    y\\ncount  3.0  3.0\\nmean   2.0  5.0\\n...",
            "cell_number": 1
        }

    Workflow Pattern:
        1. Explore with execute() until code works
        2. create_notebook() when ready to document
        3. add_to_notebook() to save working code with outputs
        4. Share notebook with real results, not just code

    Pro tip: Your exploration with execute() becomes your notebook!
    No need to restart or reload data.
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not notebook.endswith('.ipynb'):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Get the kernel spec from the notebook metadata
        kernel_spec_name = "python3"  # default
        if "metadata" in content and "kernelspec" in content["metadata"]:
            kernel_spec_name = content["metadata"]["kernelspec"].get("name", "python3")

        # Convert kernel spec name to our kernel type
        kernel_type = _get_kernel_type_from_spec(kernel_spec_name)

        # Execute the code with the notebook's kernel
        execution_result = await _execute_code(code, kernel_type)

        # Create new cell
        new_cell = {
            "cell_type": "code",
            "execution_count": len(content["cells"]) + 1,
            "metadata": {},
            "source": code if isinstance(code, list) else code.splitlines(keepends=True),
            "outputs": []
        }

        # Add output if there was any
        if execution_result["output"]:
            if execution_result["error"]:
                error_parts = execution_result["output"].split(":", 1)
                ename = error_parts[0].strip()
                evalue = error_parts[1].strip() if len(error_parts) > 1 else ""
                new_cell["outputs"].append({
                    "output_type": "error",
                    "ename": ename,
                    "evalue": evalue,
                    "traceback": [execution_result["output"]]
                })
            else:
                new_cell["outputs"].append({
                    "output_type": "stream",
                    "name": "stdout",
                    "text": execution_result["output"]
                })

        # Append the new cell
        content["cells"].append(new_cell)

        # PUT the updated notebook back
        update_data = {
            "type": "notebook",
            "content": content
        }

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers,
            json=update_data
        )
        response.raise_for_status()

    debug_print(f"Added cell to notebook: {notebook}")

    return {
        "output": execution_result["output"],
        "cell_number": len(content["cells"])
    }

@mcp.tool()
async def read_notebook(name: str) -> dict:
    """
    Read all cells from a notebook.

    Returns the complete notebook structure including code, outputs,
    and markdown cells.

    Args:
        name: Notebook name (without .ipynb)

    Returns:
        dict: {"cells": list[dict]}  # All notebook cells

        Each cell contains:
        - type: "code" or "markdown"
        - source: Cell content
        - output: Execution output (code cells only)

    Example:
        >>> read_notebook(name="analysis")
        {
            "cells": [
                {"type": "markdown", "source": "# Data Analysis", "output": ""},
                {"type": "code", "source": "import pandas as pd", "output": ""},
                {"type": "code", "source": "print(data)", "output": "   x  y\\n0  1  4\\n..."}
            ]
        }

    Use Cases:
        - Review notebook contents
        - Extract code for reuse
        - Analyze notebook structure
        - Search for specific patterns
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not name.endswith('.ipynb'):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{name}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Process cells
        cells = []
        for cell in content["cells"]:
            cell_info = {
                "type": cell["cell_type"],
                "source": "\n".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
            }

            # Add outputs for code cells
            if cell["cell_type"] == "code" and "outputs" in cell:
                outputs = []
                for output in cell["outputs"]:
                    if output["output_type"] == "stream":
                        outputs.append(output.get("text", ""))
                    elif output["output_type"] == "error":
                        outputs.append(f"{output.get('ename', 'Error')}: {output.get('evalue', '')}")
                    elif output["output_type"] == "execute_result":
                        # Handle execute_result which may have data
                        if "text/plain" in output.get("data", {}):
                            outputs.append(output["data"]["text/plain"])

                # Join all outputs
                cell_info["output"] = "".join(outputs) if isinstance(outputs, list) else outputs
            else:
                cell_info["output"] = ""

            cells.append(cell_info)

    debug_print(f"Read notebook: {name} ({len(cells)} cells)")

    return {
        "cells": cells
    }

@mcp.tool()
async def list_notebooks() -> dict:
    """
    List all notebooks in the workspace.

    Returns:
        dict: {"notebooks": list[str]}  # Notebook filenames

    Example:
        >>> list_notebooks()
        {"notebooks": ["analysis.ipynb", "experiments.ipynb", "report_2024.ipynb"]}

    Note:
        - Returns filenames with .ipynb extension
        - When using notebooks in other functions, omit the extension
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    async with httpx.AsyncClient() as client:
        # GET the contents listing
        response = await client.get(
            f"{jupyter_url}/api/contents",
            headers=headers
        )
        response.raise_for_status()

        contents = response.json()["content"]

        # Filter for notebooks only
        notebooks = []
        for item in contents:
            if item["type"] == "notebook":
                notebooks.append(item["name"])

    debug_print(f"Found {len(notebooks)} notebooks")

    return {
        "notebooks": notebooks
    }

@mcp.tool()
async def execute_notebook_cell(notebook: str, cell: int) -> dict:
    """
    Execute a specific cell from a notebook.

    Useful for re-running individual cells without executing the entire notebook.

    Args:
        notebook: Notebook name (without .ipynb)
        cell: Cell index (0-based)

    Returns:
        dict: {
            "output": str,  # Cell execution output
            "error": bool,  # Whether an error occurred
            "cell": int     # Cell index that was executed
        }

    Example:
        >>> execute_notebook_cell(notebook="analysis", cell=3)
        {"output": "Plot updated successfully", "error": false, "cell": 3}

    Use Cases:
        - Update visualizations with new parameters
        - Re-run specific calculations
        - Debug individual cells
        - Refresh data imports
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not notebook.endswith('.ipynb'):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Get the kernel spec from the notebook metadata
        kernel_spec_name = "python3"  # default
        if "metadata" in content and "kernelspec" in content["metadata"]:
            kernel_spec_name = content["metadata"]["kernelspec"].get("name", "python3")

        # Convert kernel spec name to our kernel type
        kernel_type = _get_kernel_type_from_spec(kernel_spec_name)

        # Check if cell index is valid
        if cell < 0 or cell >= len(content["cells"]):
            return {
                "error": f"Cell index {cell} out of range. Notebook has {len(content['cells'])} cells.",
                "output": ""
            }

        # Get the cell
        target_cell = content["cells"][cell]

        # Check if it's a code cell
        if target_cell["cell_type"] != "code":
            return {
                "error": f"Cell {cell} is a {target_cell['cell_type']} cell, not a code cell.",
                "output": ""
            }

        # Get the code from the cell
        code = "".join(target_cell["source"]) if isinstance(target_cell["source"], list) else target_cell["source"]

        # Execute the code with the notebook's kernel
        execution_result = await _execute_code(code, kernel_type)

        # Update the cell's outputs
        target_cell["outputs"] = []
        if execution_result["output"]:
            if execution_result["error"]:
                error_parts = execution_result["output"].split(":", 1)
                ename = error_parts[0].strip()
                evalue = error_parts[1].strip() if len(error_parts) > 1 else ""
                target_cell["outputs"].append({
                    "output_type": "error",
                    "ename": ename,
                    "evalue": evalue,
                    "traceback": [execution_result["output"]]
                })
            else:
                target_cell["outputs"].append({
                    "output_type": "stream",
                    "name": "stdout",
                    "text": execution_result["output"]
                })

        # Update execution count
        target_cell["execution_count"] = cell + 1

        # PUT the updated notebook back
        update_data = {
            "type": "notebook",
            "content": content
        }

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers,
            json=update_data
        )
        response.raise_for_status()

    debug_print(f"Executed cell {cell} in notebook: {notebook}")

    return {
        "output": execution_result["output"],
        "error": execution_result["error"],
        "cell": cell
    }

@mcp.tool()
async def add_markdown_to_notebook(notebook: str, text: str) -> dict:
    """
    Add a markdown cell to a notebook.

    Use for documentation, explanations, and structuring your notebooks.

    Args:
        notebook: Notebook name (without .ipynb)
        text: Markdown formatted text

    Returns:
        dict: {"cell_number": int}  # Cell index in notebook

    Examples:
        >>> add_markdown_to_notebook(
        ...     notebook="analysis",
        ...     text="# Data Analysis\\n## Loading and Preprocessing"
        ... )
        {"cell_number": 2}

        >>> add_markdown_to_notebook(
        ...     notebook="analysis",
        ...     text='''
        ... ## Results Summary
        ... - Total records: 1,000
        ... - Mean value: 42.3
        ... - Standard deviation: 5.7
        ... '''
        ... )
        {"cell_number": 5}

    Markdown Tips:
        - Use # for headers (##, ### for subheaders)
        - Use - or * for bullet points
        - Use ``` for code blocks
        - Use **bold** and *italic* for emphasis
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not notebook.endswith('.ipynb'):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Create new markdown cell
        new_cell = {
            "cell_type": "markdown",
            "metadata": {},
            "source": text if isinstance(text, list) else text.splitlines(keepends=True)
        }

        # Append the new cell
        content["cells"].append(new_cell)

        # PUT the updated notebook back
        update_data = {
            "type": "notebook",
            "content": content
        }

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers,
            json=update_data
        )
        response.raise_for_status()

    debug_print(f"Added markdown cell to notebook: {notebook}")

    return {
        "cell_number": len(content["cells"])
    }

@mcp.tool()
async def delete_notebook(name: str) -> dict:
    """
    Delete a notebook from the workspace.

    Args:
        name: Notebook name (without .ipynb)

    Returns:
        dict: {
            "deleted": bool,  # Success flag
            "path": str       # Deleted file path
        }

    Example:
        >>> delete_notebook(name="old_analysis")
        {"deleted": true, "path": "old_analysis.ipynb"}

    Warning:
        This operation is permanent. Consider using copy_notebook()
        to create a backup before deleting.
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not name.endswith('.ipynb'):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # DELETE the notebook
        response = await client.delete(
            f"{jupyter_url}/api/contents/{name}",
            headers=headers
        )
        response.raise_for_status()

    debug_print(f"Deleted notebook: {name}")

    return {
        "deleted": True,
        "path": name
    }

@mcp.tool()
async def rename_notebook(old_name: str, new_name: str) -> dict:
    """
    Rename a notebook.

    Args:
        old_name: Current notebook name (without .ipynb)
        new_name: New notebook name (without .ipynb)

    Returns:
        dict: {
            "old_path": str,    # Original path
            "new_path": str,    # New path
            "renamed": bool     # Success flag
        }

    Example:
        >>> rename_notebook(old_name="untitled", new_name="customer_analysis_2024")
        {
            "old_path": "untitled.ipynb",
            "new_path": "customer_analysis_2024.ipynb",
            "renamed": true
        }
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook names end with .ipynb
    if not old_name.endswith('.ipynb'):
        old_name = f"{old_name}.ipynb"
    if not new_name.endswith('.ipynb'):
        new_name = f"{new_name}.ipynb"

    async with httpx.AsyncClient() as client:
        # PATCH to rename the notebook
        response = await client.patch(
            f"{jupyter_url}/api/contents/{old_name}",
            headers=headers,
            json={"path": new_name}
        )
        response.raise_for_status()

    debug_print(f"Renamed notebook: {old_name} to {new_name}")

    return {
        "old_path": old_name,
        "new_path": new_name,
        "renamed": True
    }

@mcp.tool()
async def search_notebooks(query: str) -> dict:
    """
    Search for text across all notebooks.

    YOUR AI MEMORY SEARCH: Find that code we wrote last week! This searches
    through AI's accumulated work across all conversations.

    Args:
        query: Search string (case-insensitive)

    Returns:
        dict: {
            "matches": list[dict],  # Search results
            "total": int           # Total number of matches
        }

        Each match contains:
        - notebook: Notebook filename
        - cell: Cell index
        - cell_type: "code" or "markdown"
        - preview: Text snippet with the match

    Examples:
        # Find previous work
        >>> search_notebooks(query="RandomForest")
        {
            "matches": [
                {
                    "notebook": "ml_experiments.ipynb",
                    "cell": 5,
                    "cell_type": "code",
                    "preview": "...rf = RandomForestClassifier(n_estimators=100)..."
                }
            ],
            "total": 3
        }

        # Human: "What was that API endpoint we were testing?"
        >>> search_notebooks(query="api.endpoint")
        # Finds the work from previous conversation!

        # Human: "Did we ever implement caching?"
        >>> search_notebooks(query="cache")
        # Shows all caching implementations across projects

    Real-world uses:\n        - "What was that clever solution we found?"
        - "Show me all our TensorFlow experiments"
        - "Find that data cleaning pipeline"
        - "Which notebook has the GPU optimization?"
        \n    This transforms AI from "I don't recall" to "Here's exactly\n    where we implemented that, from our session on Tuesday!"
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    matches = []

    async with httpx.AsyncClient() as client:
        # First, get list of all notebooks
        response = await client.get(
            f"{jupyter_url}/api/contents",
            headers=headers
        )
        response.raise_for_status()

        contents = response.json()["content"]
        notebooks = [item for item in contents if item["type"] == "notebook"]

        # Search each notebook
        for nb_info in notebooks:
            nb_name = nb_info["name"]

            # Get notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{nb_name}",
                headers=headers
            )
            response.raise_for_status()

            notebook_data = response.json()
            content = notebook_data["content"]

            # Search through cells
            for cell_idx, cell in enumerate(content["cells"]):
                # Get cell source as string
                cell_source = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]

                # Case-insensitive search
                if query.lower() in cell_source.lower():
                    # Create preview (first 150 chars or until newline)
                    preview = cell_source.strip()
                    if len(preview) > 150:
                        preview = preview[:150] + "..."
                    else:
                        # If short, show the whole cell
                        first_newline = preview.find('\n')
                        if first_newline > 0 and first_newline < 150:
                            preview = preview[:first_newline] + "..."

                    matches.append({
                        "notebook": nb_name,
                        "cell": cell_idx,
                        "cell_type": cell["cell_type"],
                        "preview": preview
                    })

                    debug_print(f"Found match in {nb_name}, cell {cell_idx}")

    debug_print(f"Search complete: found {len(matches)} matches for '{query}'")

    return {
        "query": query,
        "matches": matches,
        "total": len(matches)
    }

@mcp.tool()
async def clear_notebook_outputs(name: str) -> dict:
    """
    Clear all outputs from all cells in a notebook.

    Reduces file size and removes potentially sensitive output data.
    Essential before sharing notebooks.

    Args:
        name: Notebook name (without .ipynb)

    Returns:
        dict: {
            "notebook": str,    # Notebook filename
            "cleared": int,     # Number of outputs cleared
            "message": str      # Summary message
        }

    Example:
        >>> clear_notebook_outputs(name="analysis")
        {
            "notebook": "analysis.ipynb",
            "cleared": 12,
            "message": "Cleared outputs from 12 cells"
        }

    When to Use:
        - Before committing to version control
        - Before sharing notebooks publicly
        - To reduce notebook file size
        - To remove sensitive output data
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not name.endswith('.ipynb'):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{name}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Clear outputs from all code cells
        cleared_count = 0
        for cell in content["cells"]:
            if cell["cell_type"] == "code" and "outputs" in cell:
                if len(cell["outputs"]) > 0:
                    cleared_count += 1
                cell["outputs"] = []
                # Also reset execution count
                if "execution_count" in cell:
                    cell["execution_count"] = None

        # PUT the updated notebook back
        update_data = {
            "type": "notebook",
            "content": content
        }

        response = await client.put(
            f"{jupyter_url}/api/contents/{name}",
            headers=headers,
            json=update_data
        )
        response.raise_for_status()

    debug_print(f"Cleared outputs from {cleared_count} cells in notebook: {name}")

    return {
        "notebook": name,
        "cleared": cleared_count,
        "message": f"Cleared outputs from {cleared_count} cells"
    }

@mcp.tool()
async def run_all_cells(notebook: str) -> dict:
    """
    Execute all code cells in a notebook sequentially.

    Ensures reproducible execution of the entire notebook.

    Args:
        notebook: Notebook name (without .ipynb)

    Returns:
        dict: {
            "notebook": str,    # Notebook filename
            "executed": int,    # Number of cells executed
            "errors": int,      # Number of errors encountered
            "message": str      # Summary message
        }

    Example:
        >>> run_all_cells(notebook="analysis")
        {
            "executed": 15,
            "errors": 0,
            "message": "Executed 15 cells (0 errors)"
        }

    Best Practices:
        - Clear outputs first if sharing: clear_notebook_outputs()
        - Ensure data files are available before running
        - Check for errors in the return value
        - Use for final validation of notebooks
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not notebook.endswith('.ipynb'):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Get the kernel spec from the notebook metadata
        kernel_spec_name = "python3"  # default
        if "metadata" in content and "kernelspec" in content["metadata"]:
            kernel_spec_name = content["metadata"]["kernelspec"].get("name", "python3")

        # Convert kernel spec name to our kernel type
        kernel_type = _get_kernel_type_from_spec(kernel_spec_name)

        # Execute all code cells
        executed_count = 0
        error_count = 0
        execution_number = 1

        for cell_idx, cell in enumerate(content["cells"]):
            if cell["cell_type"] == "code":
                # Get the code from the cell
                code = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]

                debug_print(f"Executing cell {cell_idx} in {notebook}")

                # Execute the code with the notebook's kernel
                execution_result = await _execute_code(code, kernel_type)

                # Clear existing outputs and update with new ones
                cell["outputs"] = []

                if execution_result["output"]:
                    if execution_result["error"]:
                        error_count += 1
                        error_parts = execution_result["output"].split(":", 1)
                        ename = error_parts[0].strip()
                        evalue = error_parts[1].strip() if len(error_parts) > 1 else ""
                        cell["outputs"].append({
                            "output_type": "error",
                            "ename": ename,
                            "evalue": evalue,
                            "traceback": [execution_result["output"]]
                        })
                    else:
                        cell["outputs"].append({
                            "output_type": "stream",
                            "name": "stdout",
                            "text": execution_result["output"]
                        })

                # Update execution count
                cell["execution_count"] = execution_number
                execution_number += 1
                executed_count += 1

        # PUT the updated notebook back
        update_data = {
            "type": "notebook",
            "content": content
        }

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}",
            headers=headers,
            json=update_data
        )
        response.raise_for_status()

    debug_print(f"Executed {executed_count} cells in notebook: {notebook} ({error_count} errors)")

    return {
        "notebook": notebook,
        "executed": executed_count,
        "errors": error_count,
        "message": f"Executed {executed_count} cells ({error_count} errors)"
    }

@mcp.tool()
async def copy_notebook(source: str, destination: str) -> dict:
    """
    Copy a notebook to a new name.

    Useful for versioning or creating variations of analyses.

    Args:
        source: Source notebook name (without .ipynb)
        destination: Destination notebook name (without .ipynb)

    Returns:
        dict: {
            "source": str,      # Source path
            "destination": str, # Destination path
            "copied": bool      # Success flag
        }

    Example:
        >>> copy_notebook(source="model_v1", destination="model_v2")
        {
            "source": "model_v1.ipynb",
            "destination": "model_v2.ipynb",
            "copied": true
        }

    Use Cases:
        - Create versions before major changes
        - Fork analyses for different approaches
        - Backup important notebooks
        - Create templates from existing work
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook names end with .ipynb
    if not source.endswith('.ipynb'):
        source = f"{source}.ipynb"
    if not destination.endswith('.ipynb'):
        destination = f"{destination}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the source notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{source}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()

        # Create copy data with the new destination
        copy_data = {
            "type": "notebook",
            "content": notebook_data["content"]
        }

        # PUT to create the destination notebook
        response = await client.put(
            f"{jupyter_url}/api/contents/{destination}",
            headers=headers,
            json=copy_data
        )
        response.raise_for_status()

    debug_print(f"Copied notebook: {source} -> {destination}")

    return {
        "source": source,
        "destination": destination,
        "copied": True,
        "message": f"Successfully copied {source} to {destination}"
    }

@mcp.tool()
async def get_notebook_stats(name: str) -> dict:
    """
    Get statistics about a notebook.

    Provides insights into notebook complexity and structure.

    Args:
        name: Notebook name (without .ipynb)

    Returns:
        dict: {
            "notebook": str,            # Notebook filename
            "cells": int,               # Total number of cells
            "code_cells": int,          # Number of code cells
            "markdown_cells": int,      # Number of markdown cells
            "total_lines": int,         # Total lines in notebook
            "code_lines": int,          # Total lines of code
            "markdown_lines": int,      # Total lines of markdown
            "has_outputs": bool,        # Whether any cells have outputs
            "cells_with_outputs": int,  # Cells that have outputs
            "total_outputs": int,       # Total number of outputs
            "kernel": str,              # Kernel name
            "kernel_display_name": str  # Kernel display name
        }

    Example:
        >>> get_notebook_stats(name="ml_experiments")
        {
            "notebook": "ml_experiments.ipynb",
            "cells": 25,
            "code_cells": 18,
            "markdown_cells": 7,
            "total_lines": 400,
            "code_lines": 342,
            "markdown_lines": 58,
            "has_outputs": true,
            "cells_with_outputs": 15,
            "total_outputs": 22,
            "kernel": "python3",
            "kernel_display_name": "Python 3"
        }

    Use Cases:
        - Identify notebooks needing documentation
        - Find overly complex notebooks to refactor
        - Assess notebook maintainability
        - Track project growth over time
    """

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not name.endswith('.ipynb'):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{name}",
            headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Initialize counters
        total_cells = len(content["cells"])
        code_cells = 0
        markdown_cells = 0
        total_code_lines = 0
        total_markdown_lines = 0
        cells_with_outputs = 0
        total_outputs = 0

        # Count cells and lines
        for cell in content["cells"]:
            cell_source = cell["source"]
            if isinstance(cell_source, list):
                line_count = len(cell_source)
            else:
                line_count = cell_source.count('\n') + 1 if cell_source else 0

            if cell["cell_type"] == "code":
                code_cells += 1
                total_code_lines += line_count

                # Count outputs
                if "outputs" in cell and len(cell["outputs"]) > 0:
                    cells_with_outputs += 1
                    total_outputs += len(cell["outputs"])

            elif cell["cell_type"] == "markdown":
                markdown_cells += 1
                total_markdown_lines += line_count

        # Get kernel info
        kernel_name = "python3"  # default
        if "metadata" in content and "kernelspec" in content["metadata"]:
            kernel_name = content["metadata"]["kernelspec"].get("name", "python3")
            kernel_display = content["metadata"]["kernelspec"].get("display_name", kernel_name)
        else:
            kernel_display = kernel_name

    debug_print(f"Stats for {name}: {total_cells} cells, {total_code_lines} lines of code")

    return {
        "notebook": name,
        "cells": total_cells,
        "code_cells": code_cells,
        "markdown_cells": markdown_cells,
        "total_lines": total_code_lines + total_markdown_lines,
        "code_lines": total_code_lines,
        "markdown_lines": total_markdown_lines,
        "has_outputs": cells_with_outputs > 0,
        "cells_with_outputs": cells_with_outputs,
        "total_outputs": total_outputs,
        "kernel": kernel_name,
        "kernel_display_name": kernel_display
    }

@mcp.tool()
async def stream_execute(code: str, kernel: str = "python3") -> dict:
    """
    Execute code with real-time timestamped output streaming.

    THE KEY DIFFERENCE: You see output AS IT HAPPENS, not after completion.
    Each line gets timestamped, perfect for monitoring long operations.

    Args:
        code: The code to execute
        kernel: Kernel name (default: "python3")

    Returns:
        dict: {
            "outputs": list[dict],  # Timestamped output chunks
            "completed": bool,      # Whether execution finished
            "error": bool,         # Whether an error occurred
            "total_outputs": int   # Number of output chunks
        }

        Each output chunk contains:
        - type: "stream"
        - stream: "stdout" or "stderr"
        - text: The output text
        - timestamp: ISO format timestamp (for performance analysis)

    Examples:
        # REAL ML TRAINING MONITORING
        >>> stream_execute(code='''
        ... import time
        ... for epoch in range(100):
        ...     print(f"[{time.strftime('%H:%M:%S')}] Epoch {epoch+1}/100")
        ...     # Simulate training batch
        ...     time.sleep(0.5)
        ...     loss = 1.0 / (epoch + 1)
        ...     print(f"  Loss: {loss:.4f}")
        ...     if epoch % 10 == 0:
        ...         print(f"  🔵 Checkpoint saved at epoch {epoch}")
        ...     if loss < 0.01:
        ...         print("  🎯 Target loss reached! Stopping early.")
        ...         break
        ... ''')
        # Each line appears immediately with timestamp
        # Perfect for long training runs where you need progress visibility

        # SYSTEM MONITORING
        >>> stream_execute(code='''
        ... import psutil
        ... import time
        ... for _ in range(60):  # Monitor for 1 minute
        ...     cpu = psutil.cpu_percent(interval=1)
        ...     mem = psutil.virtual_memory().percent
        ...     print(f"[{time.strftime('%H:%M:%S')}] CPU: {cpu}% | Memory: {mem}%")
        ...     if cpu > 80:
        ...         print("⚠️  HIGH CPU ALERT!")
        ... ''', kernel="python3")

        # DATA PROCESSING WITH PROGRESS
        >>> stream_execute(code='''
        ... total_files = 1000
        ... for i in range(total_files):
        ...     if i % 100 == 0:
        ...         progress = (i / total_files) * 100
        ...         print(f"Processing file {i}/{total_files} ({progress:.1f}%)")
        ...     # Process file here
        ... print("✅ All files processed!")
        ... ''')

    When to use stream_execute() vs execute():

        execute() - When you want:
        ✓ Quick results (< 5 seconds)
        ✓ Final output only
        ✓ Simple calculations

        stream_execute() - When you need:
        ✓ Progress monitoring (training, processing)
        ✓ Timestamp analysis (performance debugging)
        ✓ Early stopping capability (see output, decide to continue)
        ✓ Long operations (> 5 seconds)
        ✓ Real-time feedback (system monitoring)

    Pro tip: Timestamps help identify bottlenecks. If there's a long gap
    between timestamps, that's where your code is slow!
    """
    global KERNEL_IDS

    # Map user-friendly kernel type to actual kernel spec name
    kernel_spec_name = _get_kernel_spec_name(kernel)
    debug_print(f"Stream execute: Mapping kernel '{kernel}' to spec name '{kernel_spec_name}'")

    # Use configured Jupyter URLs
    jupyter_url = JUPYTER_URL
    ws_url = JUPYTER_WS_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Check if we need to create a new kernel
    if kernel not in KERNEL_IDS:
        async with httpx.AsyncClient() as client:
            # Check if kernel is available before attempting to create
            kernels_data = await _get_available_kernels(jupyter_url, headers)
            available_kernels = list(kernels_data.get("kernelspecs", {}).keys())

            if kernel_spec_name not in available_kernels:
                debug_print(f"Warning: Kernel spec '{kernel_spec_name}' not found in available kernels: {available_kernels}")
                debug_print(f"Attempting to create anyway...")

            try:
                # Create kernel with specific kernel spec
                kernel_data = {"name": kernel_spec_name}
                debug_print(f"Attempting to create {kernel} kernel (spec: {kernel_spec_name})...")
                response = await client.post(
                    f"{jupyter_url}/api/kernels",
                    headers=headers,
                    json=kernel_data
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                debug_print(f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}")
                if e.response.status_code == 500:
                    debug_print(f"Kernel spec '{kernel_spec_name}' may not be available in Jupyter.")
                    debug_print(f"Available kernels: {available_kernels}")
                    debug_print(f"Response text: {e.response.text}")
                raise Exception(f"Cannot create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}. Available kernels: {available_kernels}")
            except Exception as e:
                debug_print(f"Error creating {kernel} kernel (spec: {kernel_spec_name}): {e}")
                raise Exception(f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {str(e)}")

            kernel_info = response.json()
            kernel_session = str(uuid.uuid4())
            KERNEL_IDS[kernel] = {
                "id": kernel_info["id"],
                "session": kernel_session
            }

            debug_print(f"Created {kernel} kernel: {KERNEL_IDS[kernel]['id']} with session: {KERNEL_IDS[kernel]['session'][:8]}...")

            # Add kernel initialization check for Deno and Rust
            if kernel in ["deno", "rust"]:
                debug_print(f"Waiting for {kernel} kernel to initialize...")
                ready = await _ensure_kernel_ready(KERNEL_IDS[kernel]["id"], KERNEL_IDS[kernel]["session"], ws_url, headers)
                if not ready:
                    debug_print(f"Warning: {kernel} kernel may not be fully ready")
                # Give it a bit more time for runtime initialization
                if kernel == "rust":
                    await asyncio.sleep(2.0)  # Rust needs more time for compilation environment
                else:
                    await asyncio.sleep(1.0)
    else:
        debug_print(f"Using existing {kernel} kernel: {KERNEL_IDS[kernel]['id']}")

        # Verify the kernel still exists before trying to connect
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{jupyter_url}/api/kernels/{KERNEL_IDS[kernel]['id']}",
                    headers=headers
                )
                if response.status_code == 404:
                    debug_print(f"Kernel {KERNEL_IDS[kernel]['id']} no longer exists, removing from cache")
                    del KERNEL_IDS[kernel]
                    # Recursively call to create a new kernel
                    return await stream_execute(code, kernel)
                response.raise_for_status()
                debug_print(f"Confirmed kernel {KERNEL_IDS[kernel]['id']} still exists")
        except Exception as e:
            debug_print(f"Error checking kernel existence: {e}")
            debug_print(f"Removing cached kernel and creating new one")
            del KERNEL_IDS[kernel]
            return await stream_execute(code, kernel)

    # Connect via WebSocket
    ws_endpoint = f"{ws_url}/api/kernels/{KERNEL_IDS[kernel]['id']}/channels"
    if JUPYTER_TOKEN:
        ws_endpoint += f"?token={JUPYTER_TOKEN}"

    outputs = []
    completed = False
    error_occurred = False

    async with websockets.connect(ws_endpoint, subprotocols=['kernel.v5.2']) as websocket:
        # Create execute_request message
        msg_id = str(uuid.uuid4())
        execute_msg = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "session": KERNEL_IDS[kernel]["session"],
                "username": "mcp",
                "version": "5.2",
                "date": datetime.utcnow().isoformat() + "Z"
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False
            },
            "channel": "shell"
        }

        # Send the execute request
        await websocket.send(json.dumps(execute_msg))
        debug_print(f"Sent execute_request for streaming execution")

        # Receive messages and collect outputs as they arrive
        try:
            while not completed:
                # Use a short timeout to check for messages
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                msg_data = json.loads(message)
                msg_type = msg_data.get("header", {}).get("msg_type", "")
                parent_msg_id = msg_data.get("parent_header", {}).get("msg_id", "")

                # Only process messages that are responses to our execute request
                if parent_msg_id != msg_id:
                    continue

                timestamp = datetime.utcnow().isoformat()

                # Collect stream output (stdout/stderr)
                if msg_type == "stream":
                    content = msg_data.get("content", {})
                    text = content.get("text", "")
                    stream_name = content.get("name", "stdout")
                    if text:
                        outputs.append({
                            "type": "stream",
                            "stream": stream_name,
                            "text": text,
                            "timestamp": timestamp
                        })
                        debug_print(f"  Stream output at {timestamp}: {text.strip()}")

                # Check for display data
                elif msg_type == "display_data":
                    content = msg_data.get("content", {})
                    data = content.get("data", {})
                    if "text/plain" in data:
                        outputs.append({
                            "type": "display_data",
                            "text": data["text/plain"],
                            "timestamp": timestamp
                        })
                        debug_print(f"  Display data at {timestamp}")

                # Check for execute result
                elif msg_type == "execute_result":
                    content = msg_data.get("content", {})
                    data = content.get("data", {})
                    if "text/plain" in data:
                        outputs.append({
                            "type": "execute_result",
                            "text": data["text/plain"],
                            "timestamp": timestamp
                        })
                        debug_print(f"  Execute result at {timestamp}")

                # Check for errors
                elif msg_type == "error":
                    content = msg_data.get("content", {})
                    ename = content.get("ename", "Error")
                    evalue = content.get("evalue", "")
                    outputs.append({
                        "type": "error",
                        "error_name": ename,
                        "error_value": evalue,
                        "timestamp": timestamp
                    })
                    error_occurred = True
                    debug_print(f"  Error at {timestamp}: {ename}: {evalue}")

                elif msg_type == "execute_reply":
                    status = msg_data.get("content", {}).get("status", "unknown")
                    completed = True
                    debug_print(f"  Execution completed with status: {status}")

        except Exception as e:
            debug_print(f"Error during streaming execution: {e}")
            error_occurred = True
            outputs.append({
                "type": "error",
                "error_name": "StreamingError",
                "error_value": str(e),
                "timestamp": datetime.utcnow().isoformat()
            })

    return {
        "outputs": outputs,
        "completed": completed,
        "error": error_occurred,
        "total_outputs": len(outputs)
    }

if __name__ == "__main__":
    import sys
    import logging

    # Set up logging for debugging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stderr
    )

    try:
        mcp.run()
    except KeyboardInterrupt:
        debug_print("Server stopped by user")
    except Exception as e:
        debug_print(f"Server error: {e}")
        raise
