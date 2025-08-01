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
import os
import websockets
import json
import uuid
import asyncio
import sys
from datetime import datetime
import re


# Debug mode - set via environment variable
DEBUG = os.environ.get("JUPYTER_MCP_DEBUG", "").lower() == "true"

# Helper function to print debug messages to stderr
def debug_print(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs, file=sys.stderr)


# Progress indicator for kernel startup
class KernelStartupProgress:
    """Visual progress indicator for kernel startup"""

    def __init__(self, kernel_name: str):
        self.kernel_name = kernel_name
        self.steps = []
        self.current_step = 0
        self.start_time = asyncio.get_event_loop().time()

    def add_step(self, message: str, icon: str = "🔄"):
        """Add a progress step and print it"""
        elapsed = asyncio.get_event_loop().time() - self.start_time
        self.steps.append((message, elapsed))
        debug_print(f"{icon} [{elapsed:.1f}s] {self.kernel_name}: {message}")

    def complete(self, success: bool = True):
        """Mark completion"""
        elapsed = asyncio.get_event_loop().time() - self.start_time
        if success:
            debug_print(f"✅ [{elapsed:.1f}s] {self.kernel_name}: Ready!")
        else:
            debug_print(f"❌ [{elapsed:.1f}s] {self.kernel_name}: Failed to start")


mcp = FastMCP("jupyter-kernel")

CONFIG = {
    # Jupyter server configuration
    "JUPYTER_HOST": os.environ.get("JUPYTER_HOST", "localhost"),
    "JUPYTER_PORT": os.environ.get("JUPYTER_PORT", "8888"),
    "JUPYTER_PROTOCOL": os.environ.get("JUPYTER_PROTOCOL", "http"),
    "JUPYTER_WS_PROTOCOL": os.environ.get("JUPYTER_WS_PROTOCOL", "ws"),
}

JUPYTER_URL = (
    f"{CONFIG['JUPYTER_PROTOCOL']}://{CONFIG['JUPYTER_HOST']}:{CONFIG['JUPYTER_PORT']}"
)
JUPYTER_WS_URL = f"{CONFIG['JUPYTER_WS_PROTOCOL']}://{CONFIG['JUPYTER_HOST']}:{CONFIG['JUPYTER_PORT']}"

JUPYTER_TOKEN = os.environ.get("JUPYTER_TOKEN")
if not JUPYTER_TOKEN:
    debug_print(
        "WARNING: No JUPYTER_TOKEN found in environment. Authentication will fail."
    )
else:
    debug_print("Jupyter token loaded from environment")

# Global dictionary to store kernel IDs and session IDs by kernel type
# Structure: {kernel_type: {"id": kernel_id, "session": session_id}}
KERNEL_IDS = {}
# Lock to protect concurrent access to KERNEL_IDS
KERNEL_IDS_LOCK = asyncio.Lock()


def _get_kernel_spec(kernel_type: str) -> dict:
    """Get kernel specification based on kernel type"""
    kernel_specs = {
        "python3": {
            "name": "python3",
            "display_name": "Python 3",
            "language": "python",
        },
        "deno": {"name": "deno", "display_name": "Deno", "language": "typescript"},
        "julia": {
            "name": "julia-1.10",
            "display_name": "Julia 1.10",
            "language": "julia",
        },
        "r": {"name": "ir", "display_name": "R", "language": "r"},
        "go": {"name": "gophernotes", "display_name": "Go", "language": "go"},
        "rust": {"name": "evcxr_jupyter", "display_name": "Rust", "language": "rust"},
        "bash": {"name": "bash", "display_name": "Bash", "language": "bash"},
        "ruby": {"name": "ruby3", "display_name": "Ruby 3", "language": "ruby"},
    }

    # Return requested kernel spec or fallback to a sensible default
    if kernel_type in kernel_specs:
        return kernel_specs[kernel_type]

    # If requested kernel not found, return python3 as fallback
    # In production, you might want to query Jupyter for available kernels
    debug_print(
        f"Warning: Unknown kernel type '{kernel_type}', falling back to python3"
    )
    return kernel_specs["python3"]


def _get_kernel_type_from_spec(kernel_spec_name: str) -> str:
    """Map kernel spec name back to our kernel type"""
    spec_to_type = {
        "python3": "python3",
        "deno": "deno",
        "julia-1.10": "julia",
        "ir": "r",
        "gophernotes": "go",
        "evcxr_jupyter": "rust",
        "bash": "bash",
        "ruby3": "ruby",
    }
    return spec_to_type.get(kernel_spec_name, "python3")


def _get_kernel_spec_name(kernel_type: str) -> str:
    """Map our kernel type to the actual kernel spec name used by Jupyter"""
    type_to_spec = {
        "python3": "python3",
        "deno": "deno",
        "julia": "julia-1.10",
        "julia-1.10": "julia-1.10",
        "r": "ir",
        "ir": "ir",
        "go": "gophernotes",
        "gophernotes": "gophernotes",
        "rust": "evcxr_jupyter",
        "evcxr_jupyter": "evcxr_jupyter",
        "bash": "bash",
        "ruby": "ruby3",
        "ruby3": "ruby3",
    }
    return type_to_spec.get(kernel_type, "python3")


async def _get_available_kernels(jupyter_url: str, headers: dict) -> dict:
    """Get list of available kernels from Jupyter"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{jupyter_url}/api/kernelspecs", headers=headers
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        debug_print(f"Warning: Could not fetch available kernels: {e}")
        return {"kernelspecs": {}}


async def _ensure_kernel_ready(
    kernel_id: str, session_id: str, ws_url: str, headers: dict
) -> bool:
    """Ensure kernel is ready by sending kernel_info_request"""
    ws_endpoint = f"{ws_url}/api/kernels/{kernel_id}/channels"
    if JUPYTER_TOKEN:
        ws_endpoint += f"?token={JUPYTER_TOKEN}"

    try:
        ws = await _connect_with_backoff(ws_endpoint, subprotocols=["kernel.v5.2"])
        async with ws:
            msg_id = str(uuid.uuid4())
            kernel_info_msg = {
                "header": {
                    "msg_id": msg_id,
                    "msg_type": "kernel_info_request",
                    "session": session_id,
                    "username": "mcp",
                    "version": "5.2",
                    "date": datetime.utcnow().isoformat() + "Z",
                },
                "parent_header": {},
                "metadata": {},
                "content": {},
                "channel": "shell",
            }

            await ws.send(json.dumps(kernel_info_msg))

            try:
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < 5.0:
                    message = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    msg_data = json.loads(message)
                    if (
                        msg_data.get("header", {}).get("msg_type")
                        == "kernel_info_reply"
                        and msg_data.get("parent_header", {}).get("msg_id") == msg_id
                    ):
                        debug_print(f"Kernel {kernel_id} is ready")
                        return True
            except asyncio.TimeoutError:
                pass

        return False
    except Exception as e:
        debug_print(f"Error checking kernel readiness: {e}")
        return False


async def _connect_with_backoff(
    ws_endpoint: str, subprotocols: list, max_retries: int = 5
) -> websockets.WebSocketClientProtocol:
    """
    Connect to WebSocket with exponential backoff retry logic.
    Maintains delightful UX with encouraging messages.
    """
    retry_messages = [
        "🔄 Hmm, connection hiccup! Let me try again...",
        "💪 Still working on it - sometimes kernels need a moment to wake up!",
        "🎯 One more try - third time's the charm!",
        "🚀 Persistence is key - giving it another shot!",
        "⚡ Almost there - kernels can be sleepy sometimes!",
    ]

    base_delay = 1.0  # Start with 1 second
    max_delay = 30.0  # Cap at 30 seconds

    for attempt in range(max_retries):
        try:
            return await websockets.connect(ws_endpoint, subprotocols=subprotocols)
        except Exception as e:
            if attempt == max_retries - 1:
                raise Exception(
                    f"🔌 Connection troubles after {max_retries} attempts. The kernel might be taking a break. Error: {str(e)}"
                )

            # Calculate delay with exponential backoff
            delay = min(base_delay * (2**attempt), max_delay)

            message = retry_messages[min(attempt, len(retry_messages) - 1)]
            debug_print(f"{message} (waiting {delay:.1f}s)")

            await asyncio.sleep(delay)

    raise Exception("🔌 Unable to establish connection")


async def _execute_code(code: str, kernel: str = "python3") -> dict:
    """Internal function to execute code in a Jupyter kernel"""
    global KERNEL_IDS

    kernel_spec_name = _get_kernel_spec_name(kernel)
    debug_print(f"Mapping kernel '{kernel}' to spec name '{kernel_spec_name}'")

    jupyter_url = JUPYTER_URL
    ws_url = JUPYTER_WS_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Check if kernel exists with lock
    async with KERNEL_IDS_LOCK:
        kernel_exists = kernel in KERNEL_IDS
    
    if not kernel_exists:
        progress = KernelStartupProgress(kernel)
        progress.add_step("Starting kernel creation", "🚀")

        async with httpx.AsyncClient() as client:
            progress.add_step("Checking available kernels", "🔍")
            kernels_data = await _get_available_kernels(jupyter_url, headers)
            available_kernels = list(kernels_data.get("kernelspecs", {}).keys())

            if kernel_spec_name not in available_kernels:
                progress.add_step(
                    f"Kernel spec '{kernel_spec_name}' not in available list", "⚠️"
                )
                debug_print(f"Available kernels: {available_kernels}")
                progress.add_step("Attempting to create anyway", "🔧")

            try:
                kernel_data = {"name": kernel_spec_name}
                progress.add_step(
                    f"Creating {kernel} kernel (spec: {kernel_spec_name})", "⚙️"
                )
                response = await client.post(
                    f"{jupyter_url}/api/kernels", headers=headers, json=kernel_data
                )
                response.raise_for_status()
                progress.add_step("Kernel created successfully", "✨")
            except httpx.HTTPStatusError as e:
                progress.complete(success=False)
                debug_print(
                    f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}"
                )
                if e.response.status_code == 500:
                    debug_print(
                        f"Kernel spec '{kernel_spec_name}' may not be available in Jupyter."
                    )
                    debug_print(f"Available kernels: {available_kernels}")
                    debug_print(f"Response text: {e.response.text}")
                raise Exception(
                    f"Cannot create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}. Available kernels: {available_kernels}"
                )
            except Exception as e:
                progress.complete(success=False)
                debug_print(
                    f"Error creating {kernel} kernel (spec: {kernel_spec_name}): {e}"
                )
                raise Exception(
                    f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {str(e)}"
                )

            kernel_info = response.json()
            kernel_session = str(uuid.uuid4())
            async with KERNEL_IDS_LOCK:
                KERNEL_IDS[kernel] = {"id": kernel_info["id"], "session": kernel_session}

            progress.add_step(f"Kernel ID: {kernel_info['id'][:8]}...", "🆔")

            if kernel in ["deno", "rust"]:
                progress.add_step(f"Initializing {kernel} runtime environment", "🔄")
                ready = await _ensure_kernel_ready(
                    KERNEL_IDS[kernel]["id"],
                    KERNEL_IDS[kernel]["session"],
                    ws_url,
                    headers,
                )
                if not ready:
                    progress.add_step("Kernel may not be fully ready", "⚠️")
                if kernel == "rust":
                    progress.add_step(
                        "Waiting for Rust compilation environment (2s)", "⏳"
                    )
                    await asyncio.sleep(
                        2.0
                    )  # Rust needs more time for compilation environment
                else:
                    progress.add_step("Waiting for runtime initialization (1s)", "⏳")
                    await asyncio.sleep(1.0)

            progress.complete(success=True)
            
            # Set kernel_id and session for the newly created kernel
            async with KERNEL_IDS_LOCK:
                kernel_id = KERNEL_IDS[kernel]['id']
                kernel_session = KERNEL_IDS[kernel]['session']
    else:
        async with KERNEL_IDS_LOCK:
            kernel_id = KERNEL_IDS[kernel]['id']
        debug_print(f"Using existing {kernel} kernel: {kernel_id}")

        # Verify the kernel still exists before trying to connect
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{jupyter_url}/api/kernels/{kernel_id}",
                    headers=headers,
                )
                if response.status_code == 404:
                    debug_print(
                        f"Kernel {kernel_id} no longer exists, removing from cache"
                    )
                    async with KERNEL_IDS_LOCK:
                        del KERNEL_IDS[kernel]
                    # Recursively call to create a new kernel
                    return await _execute_code(code, kernel)
                response.raise_for_status()
                debug_print(f"Confirmed kernel {kernel_id} still exists")
        except Exception as e:
            debug_print(f"Error checking kernel existence: {e}")
            debug_print("Removing cached kernel and creating new one")
            async with KERNEL_IDS_LOCK:
                del KERNEL_IDS[kernel]
            return await _execute_code(code, kernel)

    # Get kernel info with lock
    async with KERNEL_IDS_LOCK:
        kernel_id = KERNEL_IDS[kernel]['id']
        kernel_session = KERNEL_IDS[kernel]['session']
    
    ws_endpoint = f"{ws_url}/api/kernels/{kernel_id}/channels"
    if JUPYTER_TOKEN:
        ws_endpoint += f"?token={JUPYTER_TOKEN}"

    websocket = await _connect_with_backoff(ws_endpoint, subprotocols=["kernel.v5.2"])
    async with websocket:
        msg_id = str(uuid.uuid4())
        execute_msg = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "session": kernel_session,
                "username": "mcp",
                "version": "5.2",
                "date": datetime.utcnow().isoformat() + "Z",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False,
            },
            "channel": "shell",
        }

        await websocket.send(json.dumps(execute_msg))
        debug_print(f"Sent execute_request for: {code}")

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

                if msg_type == "status":
                    exec_state = msg_data.get("content", {}).get("execution_state", "")
                    debug_print(f"  Execution state: {exec_state}")
                    if exec_state == "idle" and reply_received:
                        # For Rust, wait a bit more to catch additional stream messages
                        if kernel == "rust":
                            debug_print(
                                "  Rust idle detected, waiting for additional messages..."
                            )
                            # Continue collecting messages for a short time
                            idle_time = asyncio.get_event_loop().time()
                            while (asyncio.get_event_loop().time() - idle_time) < 1.0:
                                try:
                                    additional_msg = await asyncio.wait_for(
                                        websocket.recv(), timeout=0.1
                                    )
                                    additional_data = json.loads(additional_msg)
                                    additional_type = additional_data.get(
                                        "header", {}
                                    ).get("msg_type", "")
                                    additional_parent = additional_data.get(
                                        "parent_header", {}
                                    ).get("msg_id", "")

                                    debug_print(
                                        f"  Additional message: {additional_type} (parent: {additional_parent})"
                                    )

                                    # Process additional stream messages
                                    if (
                                        additional_parent == msg_id
                                        and additional_type == "stream"
                                    ):
                                        content = additional_data.get("content", {})
                                        text = content.get("text", "")
                                        if text:
                                            output_text.append(text)
                                            debug_print(
                                                f"    Additional stream output: {text.strip()}"
                                            )
                                except asyncio.TimeoutError:
                                    continue
                                except Exception:
                                    break
                            debug_print(
                                "  Finished collecting additional Rust messages"
                            )
                        execution_state_idle = True

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
                            debug_print(
                                f"  Execute result: {data['text/plain'].strip()}"
                            )

                    elif msg_type == "error":
                        content = msg_data.get("content", {})
                        ename = content.get("ename", "Error")
                        evalue = content.get("evalue", "")
                        error_text = f"{ename}: {evalue}"
                        debug_print(f"  Error: {error_text}")

                    elif msg_type == "execute_reply":
                        debug_print(
                            f"  Got execute_reply, status: {msg_data.get('content', {}).get('status', 'unknown')}"
                        )
                        reply_received = True
                        # For Python kernels, this might be enough
                        # Other kernels may need to wait for explicit idle state
                        if kernel in ["python3", "ir", "julia-1.10", "bash"]:
                            execution_state_idle = True

        except Exception as e:
            debug_print(f"Error during message collection: {e}")
            error_text = str(e)

    if error_text:
        return {"output": error_text, "error": True}

    output = "".join(output_text)
    return {"output": output, "error": False}


@mcp.tool()
async def compute(code: str, kernel: str = "python3", mode: str = "auto") -> dict:
    """
    Your computational thinking companion - just write code naturally.

    This intelligently handles execution, streaming when beneficial, and provides
    rich feedback about your computation.

    Args:
        code: Your code to run. Multi-line? Complex? Simple? All welcome!
        kernel: Language context (python3, julia, r, etc.) or use natural hints:
               - "python", "py" → python3
               - "julia", "jl" → julia
               - "R", "r-stats" → r
               - "typescript", "ts", "js" → deno
        mode: "auto" (default), "stream", or "direct"
              Auto-detects when streaming helps (progress bars, long loops, etc.)

    Returns:
        Rich execution result with context and suggestions

    Examples:
        # Quick math
        >>> compute("2 + 2")
        4

        # Auto-detects need for streaming
        >>> compute('''
        ... for i in range(100):
        ...     print(f"Processing {i}")
        ...     time.sleep(0.1)
        ... ''')
        # Streams output in real-time!

        # Natural language kernel selection
        >>> compute("x = [1, 2, 3]", kernel="julia")
        >>> compute("data.frame(x=1:10)", kernel="r")

    The magic: Your variables persist! Work continues across messages.
    """
    # Map friendly kernel names to actual specs
    kernel_map = {
        "python": "python3",
        "py": "python3",
        "julia": "julia",
        "jl": "julia",
        "r": "r",
        "R": "r",
        "r-stats": "r",
        "typescript": "deno",
        "ts": "deno",
        "js": "deno",
        "javascript": "deno",
        "go": "go",
        "golang": "go",
        "rust": "rust",
        "rs": "rust",
        "bash": "bash",
        "sh": "bash",
        "shell": "bash",
        "ruby": "ruby",
        "rb": "ruby",
    }

    actual_kernel = kernel_map.get(kernel, kernel)

    # Auto-detect if streaming would be beneficial
    should_stream = mode == "stream"
    if mode == "auto":
        streaming_patterns = [
            "import time",
            "time.sleep",
            "for ",
            "while ",
            "tqdm",
            "progress",
            "Progress",
            "print(",
            "console.log(",
            "epoch",
            "iteration",
            "step",
            "train",
            "fit",
            "model.",
            "download",
            "fetch",
            "load_data",
        ]
        should_stream = any(pattern in code for pattern in streaming_patterns)

    if should_stream:
        # Use the internal streaming execution logic directly
        # This avoids calling the external stream_execute tool
        try:
            # For now, fall back to regular execution for simplicity
            # TODO: Implement inline streaming logic later
            result = await _execute_code(code, actual_kernel)
            # Still need to add error suggestions for streaming path
            enhanced_result = {
                "output": result.get("output", ""),
                "error": result.get("error", False),
                "streamed": False,
                "kernel": actual_kernel,
                "note": "Auto-streaming detected but using regular execution for compatibility",
            }

            # Add helpful suggestions on error (streaming path)
            if result.get("error"):
                output = result.get("output", "").strip()
                error_msg = output.lower()
                import re

                if "nameerror" in error_msg:
                    # Match patterns like: NameError: name 'xyz' is not defined
                    match = re.search(r"name ['\"](\w+)['\"]", output)
                    var_name = match.group(1) if match else "variable"
                    enhanced_result["suggestion"] = (
                        f"💡 '{var_name}' is not defined. Did you run previous cells?"
                    )
                elif "modulenotfounderror" in error_msg:
                    # Match patterns like: No module named 'xyz'
                    match = re.search(r"module named ['\"](\S+)['\"]", output)
                    module = match.group(1) if match else "module"
                    enhanced_result["suggestion"] = f"💡 Try: !pip install {module}"
                elif "syntaxerror" in error_msg:
                    enhanced_result["suggestion"] = (
                        "💡 Check for missing colons, parentheses, or indentation"
                    )

            return enhanced_result
        except Exception as e:
            return {
                "output": f"❌ Error during execution: {str(e)}",
                "error": True,
                "kernel": actual_kernel,
            }

    result = await _execute_code(code, actual_kernel)

    # Enhance output with helpful context
    output = result.get("output", "").strip()

    # Add execution metadata
    enhanced_result = {
        "output": output if output else "✓ Executed successfully (no output)",
        "error": result.get("error", False),
        "kernel": actual_kernel,
        "streamed": False,
    }

    # Add helpful suggestions on error
    if result.get("error"):
        error_msg = output.lower()
        import re

        if "nameerror" in error_msg:
            # Match patterns like: NameError: name 'xyz' is not defined
            match = re.search(r"name ['\"](\w+)['\"]", output)
            var_name = match.group(1) if match else "variable"
            enhanced_result["suggestion"] = (
                f"💡 '{var_name}' is not defined. Did you run previous cells?"
            )
        elif "modulenotfounderror" in error_msg:
            # Match patterns like: No module named 'xyz'
            match = re.search(r"module named ['\"](\S+)['\"]", output)
            module = match.group(1) if match else "module"
            enhanced_result["suggestion"] = f"💡 Try: !pip install {module}"
        elif "syntaxerror" in error_msg:
            enhanced_result["suggestion"] = (
                "💡 Check for missing colons, parentheses, or indentation"
            )

    return enhanced_result


@mcp.tool()
async def q(code: str, kernel: str = "python3") -> str:
    """
    Quick compute - for when you just need a fast answer.

    Super lightweight, returns just the output. Perfect for calculations,
    quick checks, and one-liners.

    Examples:
        >>> q("2 + 2")
        "4"

        >>> q("import sys; sys.version")
        "3.11.5 | packaged by conda-forge ..."

        >>> q("[i**2 for i in range(5)]")
        "[0, 1, 4, 9, 16]"
    """
    kernel_map = {
        "python": "python3",
        "py": "python3",
        "julia": "julia",
        "jl": "julia",
        "r": "r",
        "R": "r",
        "typescript": "deno",
        "ts": "deno",
        "js": "deno",
    }
    actual_kernel = kernel_map.get(kernel, kernel)

    # Use direct execution
    result = await _execute_code(code, actual_kernel)
    output = result.get("output", "").strip()
    return output if output else "✓ Executed (no output)"


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
            "language": spec.get("spec", {}).get("language", "unknown"),
        }
        available_kernels.append(kernel_info)

    debug_print(f"Found {len(available_kernels)} available kernels")

    return {
        "available_kernels": available_kernels,
        "total_count": len(available_kernels),
    }


@mcp.tool()
async def vars(kernel: str = "python3", detailed: bool = False) -> dict:
    """
    Peek into your kernel's memory - see what variables are defined.

    Like checking your pockets to see what you're carrying around!

    Args:
        kernel: Which kernel to inspect (default: python3)
        detailed: Show more info about each variable (sizes, types, etc.)

    Returns:
        A friendly summary of your workspace state

    Examples:
        >>> vars()
        🧠 Python kernel state:
        📊 df: DataFrame (1000x5)
        🔢 x: int = 42
        📝 names: list[50]
        🎯 model: RandomForestClassifier

        >>> vars(detailed=True)
        [More detailed type and memory information]

    Perfect for:
        - "What was that variable called again?"
        - "Is my data still loaded?"
        - "What kernels have I been using?"
    """
    kernel_map = {
        "python": "python3",
        "py": "python3",
        "julia": "julia",
        "jl": "julia",
        "r": "r",
        "R": "r",
        "typescript": "deno",
        "ts": "deno",
        "js": "deno",
    }
    actual_kernel = kernel_map.get(kernel, kernel)

    # Check if kernel exists
    if actual_kernel not in KERNEL_IDS:
        return {
            "message": f"🔍 No {actual_kernel} kernel running. Start one with: compute('x = 1', kernel='{actual_kernel}')",
            "kernel": actual_kernel,
            "active": False,
        }

    # Language-specific variable inspection code
    if actual_kernel == "python3":
        inspect_code = """
import sys
import types

# Get all variables except built-ins and modules
vars_info = []
for name, obj in list(globals().items()):
    if not name.startswith('_') and name not in ['In', 'Out', 'exit', 'quit', 'get_ipython']:
        # Skip modules and built-in functions
        if isinstance(obj, types.ModuleType) or isinstance(obj, types.BuiltinFunctionType):
            continue
        
        # Get type info
        type_name = type(obj).__name__
        
        # Get size/shape info
        if hasattr(obj, 'shape'):  # numpy/pandas
            shape_info = str(obj.shape)
            vars_info.append(f"{name}: {type_name} {shape_info}")
        elif hasattr(obj, '__len__') and not isinstance(obj, str):
            try:
                length = len(obj)
                vars_info.append(f"{name}: {type_name}[{length}]")
            except:
                vars_info.append(f"{name}: {type_name}")
        elif isinstance(obj, (int, float, bool)):
            vars_info.append(f"{name}: {type_name} = {obj}")
        elif isinstance(obj, str):
            preview = repr(obj[:50] + '...' if len(obj) > 50 else obj)
            vars_info.append(f"{name}: {type_name} = {preview}")
        else:
            vars_info.append(f"{name}: {type_name}")

if vars_info:
    print("\\n".join(sorted(vars_info)))
else:
    print("No variables defined yet")
"""
    elif actual_kernel == "r":
        inspect_code = """
vars <- ls(envir = .GlobalEnv)
if (length(vars) > 0) {
    for (var in vars) {
        obj <- get(var)
        if (is.data.frame(obj)) {
            cat(var, ": data.frame (", nrow(obj), "x", ncol(obj), ")\\n", sep="")
        } else if (is.matrix(obj)) {
            cat(var, ": matrix (", nrow(obj), "x", ncol(obj), ")\\n", sep="")
        } else if (is.vector(obj) && length(obj) > 1) {
            cat(var, ": ", class(obj), "[", length(obj), "]\\n", sep="")
        } else if (is.function(obj)) {
            # Skip functions
        } else {
            cat(var, ": ", class(obj), " = ", toString(obj), "\\n", sep="")
        }
    }
} else {
    cat("No variables defined yet\\n")
}
"""
    elif actual_kernel == "julia":
        inspect_code = """
vars = names(Main)
user_vars = filter(v -> !startswith(string(v), "#") && v ∉ [:Base, :Core, :Main, :ans], vars)
if !isempty(user_vars)
    for var in sort(user_vars)
        val = getfield(Main, var)
        t = typeof(val)
        if t <: AbstractArray
            println("$var: $t $(size(val))")
        elseif t <: Number
            println("$var: $t = $val")
        elseif t <: AbstractString
            preview = length(val) > 50 ? val[1:50] * "..." : val
            println("$var: $t = \\"$preview\\"")
        else
            println("$var: $t")
        end
    end
else
    println("No variables defined yet")
end
"""
    else:
        inspect_code = "echo 'Variable inspection not yet implemented for this kernel'"

    result = await _execute_code(inspect_code, actual_kernel)

    if result.get("error"):
        return {
            "message": f"❌ Error inspecting {actual_kernel} kernel: {result['output']}",
            "kernel": actual_kernel,
            "error": True,
        }

    output = result.get("output", "").strip()

    if output == "No variables defined yet":
        return {
            "message": f"🧠 {actual_kernel} kernel is running but no variables defined yet",
            "kernel": actual_kernel,
            "variables": [],
        }

    formatted_vars = [f"🧠 {actual_kernel} kernel state:"]
    for line in output.split("\n"):
        if line.strip():
            if "DataFrame" in line or "data.frame" in line:
                formatted_vars.append(f"📊 {line}")
            elif "array" in line.lower() or "matrix" in line.lower():
                formatted_vars.append(f"🔢 {line}")
            elif "list" in line or "List" in line or "vector" in line:
                formatted_vars.append(f"📝 {line}")
            elif "model" in line.lower() or "classifier" in line.lower():
                formatted_vars.append(f"🎯 {line}")
            elif "dict" in line.lower() or "map" in line.lower():
                formatted_vars.append(f"📚 {line}")
            elif ": str " in line or ": String" in line:
                formatted_vars.append(f"💬 {line}")
            elif any(t in line for t in ["int", "float", "number", "Number"]):
                formatted_vars.append(f"🔢 {line}")
            else:
                formatted_vars.append(f"📦 {line}")

    return {
        "message": "\n".join(formatted_vars),
        "kernel": actual_kernel,
        "raw_output": output,
    }


@mcp.tool()
async def workspace() -> dict:
    """
    Get a bird's eye view of your entire computational workspace.

    Shows all active kernels, notebooks, and recent activity.

    Returns:
        A comprehensive workspace summary

    Example:
        >>> workspace()
        🏠 Jupyter Workspace Status:

        🧠 Active Kernels:
        • python3: 5 variables defined
        • julia: 2 variables defined

        📚 Notebooks (3):
        • analysis.ipynb (15 cells)
        • experiments.ipynb (23 cells)
        • scratch.ipynb (5 cells)

        💡 Tip: Use vars('python3') to see variables in a specific kernel
    """
    workspace_info = ["🏠 Jupyter Workspace Status:\n"]

    # Check active kernels
    if KERNEL_IDS:
        workspace_info.append("🧠 Active Kernels:")
        for kernel_name in KERNEL_IDS:
            # Quick check if kernel has variables
            try:
                # Call the vars function directly instead of as MCP tool
                if kernel_name == "python3":
                    inspect_code = """
import sys
import types
vars_info = []
for name, obj in list(globals().items()):
    if not name.startswith('_') and name not in ['In', 'Out', 'exit', 'quit', 'get_ipython']:
        if isinstance(obj, types.ModuleType) or isinstance(obj, types.BuiltinFunctionType):
            continue
        vars_info.append(name)
print(len(vars_info))
"""
                else:
                    # For other kernels, just check if kernel is responsive
                    inspect_code = (
                        "println(length(names(Main)))"
                        if kernel_name == "julia"
                        else "length(ls())"
                    )

                result = await _execute_code(inspect_code, kernel_name)
                if not result.get("error"):
                    output_clean = result.get("output", "0").strip()
                    var_count = int(output_clean) if output_clean.isdigit() else 0
                    if var_count > 0:
                        workspace_info.append(
                            f"  • {kernel_name}: {var_count} variables defined"
                        )
                    else:
                        workspace_info.append(
                            f"  • {kernel_name}: running (no variables)"
                        )
                else:
                    workspace_info.append(f"  • {kernel_name}: active")
            except Exception:
                workspace_info.append(f"  • {kernel_name}: active")
    else:
        workspace_info.append("🧠 No active kernels")

    # List notebooks
    try:
        import os

        notebook_files = [f for f in os.listdir(".") if f.endswith(".ipynb")]
        notebooks = notebook_files

        if notebooks:
            workspace_info.append(f"\n📚 Notebooks ({len(notebooks)}):")
            for nb in notebooks[:5]:  # Show first 5
                try:
                    # Read notebook file directly to get cell count
                    import json

                    with open(nb, "r") as f:
                        nb_data = json.load(f)
                    cell_count = len(nb_data.get("cells", []))
                    workspace_info.append(f"  • {nb} ({cell_count} cells)")
                except Exception:
                    workspace_info.append(f"  • {nb}")

            if len(notebooks) > 5:
                workspace_info.append(f"  ... and {len(notebooks) - 5} more")
        else:
            workspace_info.append("\n📚 No notebooks yet")
    except Exception:
        workspace_info.append("\n📚 Could not list notebooks")

    # Add helpful tips
    workspace_info.append("\n💡 Tips:")
    if not KERNEL_IDS:
        workspace_info.append("  • Start with: compute('x = 42')")
    else:
        workspace_info.append("  • Check variables: vars('python3')")
    workspace_info.append("  • Create notebook: notebook('create', 'my_analysis')")

    return {
        "message": "\n".join(workspace_info),
        "kernels": list(KERNEL_IDS.keys()),
        "kernel_count": len(KERNEL_IDS),
    }


@mcp.tool()
async def kernel_state(show_all: bool = False) -> dict:
    """
    Get detailed state information across all active kernels with visual clarity.

    This solves the "state opacity" problem - see exactly what's loaded in each kernel
    at a glance, with memory usage estimates and helpful categorization.

    Args:
        show_all: Show all variables including internals (default: False)

    Returns:
        Comprehensive state overview with memory estimates

    Example:
        >>> kernel_state()
        🎯 Kernel State Overview
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        📊 Python3 Kernel (43.2 MB used)
        ├─ DataFrames (2):
        │  • sales_df: DataFrame (10000×25) ~38.1 MB
        │  • customers: DataFrame (500×12) ~2.3 MB
        ├─ Models (1):
        │  • rf_model: RandomForestClassifier ~1.8 MB
        ├─ Arrays (3):
        │  • X_train: ndarray (8000×25) ~610 KB
        │  • y_train: ndarray (8000,) ~31 KB
        │  • predictions: ndarray (2000,) ~8 KB
        └─ Others (5):
           • config: dict[12] ~2 KB
           • api_key: str (hidden)
           • results: dict[3] ~512 B

        🔬 Julia Kernel (12.7 MB used)
        ├─ Arrays (2):
        │  • data_matrix: Matrix{Float64} (1000×1000) ~7.6 MB
        │  • coefficients: Vector{Float64} (1000,) ~7.8 KB
        └─ Functions (1):
           • optimize_model: Function

        💡 Tips:
        • Total memory across kernels: 55.9 MB
        • Largest object: sales_df (38.1 MB)
        • Use vars('kernel') for simple variable list
    """
    if not KERNEL_IDS:
        return {
            "message": "🔍 No active kernels. Start with: compute('x = 42')",
            "total_memory": "0 MB",
            "kernel_states": {},
        }

    state_info = ["🎯 Kernel State Overview", "━" * 42, ""]
    kernel_states = {}
    total_memory = 0

    for kernel_name in sorted(KERNEL_IDS.keys()):
        # Language-specific inspection code with memory estimation
        if kernel_name == "python3":
            inspect_code = """
import sys
import types
import numpy as np
import pandas as pd
import re

def get_size_mb(obj):
    try:
        # For DataFrames
        if hasattr(obj, 'memory_usage'):
            return obj.memory_usage(deep=True).sum() / 1024 / 1024
        # For numpy arrays
        elif hasattr(obj, 'nbytes'):
            return obj.nbytes / 1024 / 1024
        # For general objects
        else:
            return sys.getsizeof(obj) / 1024 / 1024
    except:
        return 0

# Categorize variables
dataframes = []
models = []
arrays = []
functions = []
others = []
total_size = 0

for name, obj in list(globals().items()):
    if name.startswith('_') or name in ['In', 'Out', 'exit', 'quit', 'get_ipython', 'get_size_mb', 
                                         'np', 'pd', 'sys', 'types', 'dataframes', 'models', 
                                         'arrays', 'functions', 'others', 'total_size']:
        continue
    
    if isinstance(obj, types.ModuleType) or isinstance(obj, types.BuiltinFunctionType):
        continue
    
    size_mb = get_size_mb(obj)
    total_size += size_mb
    
    # Categorize by type
    type_name = type(obj).__name__
    
    if 'DataFrame' in type_name:
        shape = str(obj.shape)
        dataframes.append((name, type_name, shape, size_mb))
    elif any(model in type_name.lower() for model in ['model', 'classifier', 'regressor', 'estimator']):
        models.append((name, type_name, size_mb))
    elif 'ndarray' in type_name or 'array' in type_name.lower():
        shape = str(obj.shape) if hasattr(obj, 'shape') else f"[{len(obj)}]"
        arrays.append((name, type_name, shape, size_mb))
    elif callable(obj) and not isinstance(obj, type):
        functions.append((name, type_name))
    else:
        # Format value for display
        # Comprehensive credential detection patterns
        sensitive_patterns = [
            'key', 'token', 'secret', 'password', 'passwd', 'pwd', 
            'auth', 'credential', 'api', 'private', 'priv',
            'access', 'bearer', 'jwt', 'oauth', 'session',
            'cookie', 'csrf', 'xsrf', 'hash', 'salt',
            'signature', 'sig', 'cert', 'certificate', 'pem',
            'rsa', 'dsa', 'ecdsa', 'pub', 'ssh'
        ]
        
        name_lower = name.lower()
        is_sensitive_name = any(pattern in name_lower for pattern in sensitive_patterns)
        
        # Also check for patterns like KEY_2, SECRET_SAUCE, etc
        is_sensitive_pattern = bool(re.match(r'.*(_|-)?(key|token|secret|pass|auth|api|cert|sig)(_|-)?\d*$', name_lower, re.IGNORECASE))
        
        # Check value content for sensitive patterns (if string)
        is_sensitive_value = False
        if isinstance(obj, str) and len(obj) > 8:
            # Common token/key patterns
            if re.match(r'^[A-Za-z0-9+/=_-]{20,}$', obj):  # Base64-like tokens
                is_sensitive_value = True
            elif re.match(r'^[a-f0-9]{32,}$', obj.lower()):  # Hex tokens
                is_sensitive_value = True
            elif obj.startswith(('sk_', 'pk_', 'api_', 'pat_', 'ghp_', 'ghs_', 'glpat-')):  # Common API key prefixes
                is_sensitive_value = True
            elif 'BEGIN' in obj and 'KEY' in obj:  # PEM format keys
                is_sensitive_value = True
        
        if isinstance(obj, str) and (is_sensitive_name or is_sensitive_pattern or is_sensitive_value):
            value_str = "(hidden)"
        elif isinstance(obj, (int, float, bool)):
            value_str = str(obj)
        elif isinstance(obj, str):
            value_str = repr(obj[:30] + '...' if len(obj) > 30 else obj)
        elif hasattr(obj, '__len__'):
            value_str = f"[{len(obj)}]"
        else:
            value_str = ""
        others.append((name, type_name, value_str, size_mb))

# Output results as JSON for parsing
import json
print(json.dumps({
    'total_mb': round(total_size, 1),
    'dataframes': dataframes,
    'models': models,
    'arrays': arrays,
    'functions': functions,
    'others': others
}))
"""
        elif kernel_name == "r":
            inspect_code = """
library(jsonlite)

get_size_mb <- function(obj) {
  tryCatch({
    object.size(obj) / 1024 / 1024
  }, error = function(e) { 0 })
}

vars <- ls(envir = .GlobalEnv)
dataframes <- list()
arrays <- list()
functions <- list()
others <- list()
total_size <- 0

for (var in vars) {
  obj <- get(var)
  size_mb <- as.numeric(get_size_mb(obj))
  total_size <- total_size + size_mb
  
  if (is.data.frame(obj)) {
    dataframes[[length(dataframes) + 1]] <- list(var, "data.frame", 
                                                  paste0("(", nrow(obj), "×", ncol(obj), ")"), 
                                                  round(size_mb, 1))
  } else if (is.matrix(obj) || is.array(obj)) {
    arrays[[length(arrays) + 1]] <- list(var, class(obj)[1], 
                                         paste0("(", paste(dim(obj), collapse="×"), ")"),
                                         round(size_mb, 1))
  } else if (is.function(obj)) {
    functions[[length(functions) + 1]] <- list(var, "function")
  } else {
    val_str <- if(is.numeric(obj) && length(obj) == 1) as.character(obj) else ""
    others[[length(others) + 1]] <- list(var, class(obj)[1], val_str, round(size_mb, 1))
  }
}

cat(toJSON(list(
  total_mb = round(total_size, 1),
  dataframes = dataframes,
  arrays = arrays,
  functions = functions,
  others = others
), auto_unbox = TRUE))
"""
        elif kernel_name == "julia":
            inspect_code = """
using JSON

function get_size_mb(obj)
    try
        sizeof(obj) / 1024 / 1024
    catch
        0.0
    end
end

vars = names(Main)
user_vars = filter(v -> !startswith(string(v), "#") && v ∉ [:Base, :Core, :Main, :ans, :JSON, :get_size_mb], vars)

dataframes = []
arrays = []
functions = []
others = []
total_size = 0.0

for var in user_vars
    obj = getfield(Main, var)
    size_mb = get_size_mb(obj)
    total_size += size_mb
    
    t = typeof(obj)
    type_str = string(t)
    
    if t <: AbstractArray
        shape_str = string(size(obj))
        push!(arrays, [string(var), type_str, shape_str, round(size_mb, digits=1)])
    elseif t <: Function
        push!(functions, [string(var), "Function"])
    else
        val_str = t <: Number ? string(obj) : ""
        push!(others, [string(var), type_str, val_str, round(size_mb, digits=1)])
    end
end

result = Dict(
    "total_mb" => round(total_size, digits=1),
    "dataframes" => dataframes,
    "arrays" => arrays,
    "functions" => functions,
    "others" => others
)

print(JSON.json(result))
"""
        else:
            # Basic inspection for other kernels
            inspect_code = 'echo \'{"total_mb": 0, "dataframes": [], "models": [], "arrays": [], "functions": [], "others": []}\''

        # Execute inspection
        result = await _execute_code(inspect_code, kernel_name)

        if result.get("error"):
            state_info.append(f"❌ {kernel_name} kernel: Error getting state")
            continue

        try:
            import json

            state_data = json.loads(result["output"].strip())
            total_mb = state_data.get("total_mb", 0)
            total_memory += total_mb

            # Format kernel section
            state_info.append(f"📊 {kernel_name.title()} Kernel ({total_mb} MB used)")

            # Store state for return value
            kernel_states[kernel_name] = {"memory_mb": total_mb, "categories": {}}

            # Add categorized variables
            categories = [
                ("DataFrames", state_data.get("dataframes", []), "📊"),
                ("Models", state_data.get("models", []), "🎯"),
                ("Arrays", state_data.get("arrays", []), "🔢"),
                ("Functions", state_data.get("functions", []), "🔧"),
                ("Others", state_data.get("others", []), "📦"),
            ]

            for cat_name, items, icon in categories:
                if items:
                    kernel_states[kernel_name]["categories"][cat_name.lower()] = len(
                        items
                    )
                    state_info.append(f"├─ {cat_name} ({len(items)}):")

                    # Show top items by size
                    if cat_name in ["DataFrames", "Models", "Arrays", "Others"]:
                        # Sort by size (assuming size is last element)
                        sorted_items = sorted(
                            items,
                            key=lambda x: x[-1] if len(x) > 2 else 0,
                            reverse=True,
                        )
                        for i, item in enumerate(sorted_items[:5]):
                            is_last = (i == len(items) - 1) or (i == 4)
                            prefix = "└─" if is_last else "├─"

                            if len(item) >= 4:  # Has size
                                name, type_str, extra, size = item[:4]
                                size_str = (
                                    f"~{size} MB"
                                    if size > 0.1
                                    else f"~{size * 1024:.0f} KB"
                                )
                                state_info.append(
                                    f"│  {prefix} {name}: {type_str} {extra} {size_str}"
                                )
                            elif len(item) >= 3:  # Has extra info but no size
                                name, type_str, extra = item[:3]
                                state_info.append(
                                    f"│  {prefix} {name}: {type_str} {extra}"
                                )
                            else:  # Just name and type
                                name, type_str = item[:2]
                                state_info.append(f"│  {prefix} {name}: {type_str}")

                        if len(items) > 5:
                            state_info.append(f"│  └─ ... and {len(items) - 5} more")
                    else:
                        # Functions - just list names
                        for i, item in enumerate(items[:3]):
                            name = item[0]
                            is_last = (i == len(items) - 1) or (i == 2)
                            prefix = "└─" if is_last else "├─"
                            state_info.append(f"│  {prefix} {name}: Function")
                        if len(items) > 3:
                            state_info.append(f"│  └─ ... and {len(items) - 3} more")

            state_info.append("")  # Empty line between kernels

        except Exception:
            state_info.append(f"⚠️  {kernel_name} kernel: Could not parse state")
            state_info.append("")

    # Add summary tips
    state_info.append("💡 Summary:")
    state_info.append(f"  • Total memory across kernels: {total_memory:.1f} MB")

    # Find largest objects across all kernels
    for kernel, state in kernel_states.items():
        if state.get("memory_mb", 0) > 10:
            state_info.append(
                f"  • {kernel} using significant memory: {state['memory_mb']} MB"
            )

    state_info.append("  • Use kernel_state(show_all=True) to see internal variables")
    state_info.append("  • Use vars('kernel') for a simple variable list")

    return {
        "message": "\n".join(state_info),
        "total_memory_mb": round(total_memory, 1),
        "kernel_states": kernel_states,
    }


@mcp.tool()
async def suggest_next(kernel: str = "python3") -> dict:
    """
    When you're wondering "what's next?" - get AI-friendly suggestions based on your current context.

    This tool analyzes your kernel state and suggests relevant next steps, making discovery delightful!

    Args:
        kernel: Which kernel to analyze (default: python3)

    Returns:
        Contextual suggestions for your next move

    Examples:
        >>> suggest_next()
        🔮 Based on your current state:

        📊 You have a DataFrame 'df' loaded (1000×5)
        You might want to:
        • df.describe() - Get statistical summary
        • df.info() - Check data types and memory usage
        • df.head(10) - View first 10 rows
        • df.isnull().sum() - Check for missing values

        📦 You have pandas and matplotlib imported
        Try visualizations:
        • df.plot() - Quick line plot
        • df.hist() - Distribution of numeric columns

        💡 Pro tip: Your df has a 'date' column - try: df['date'] = pd.to_datetime(df['date'])
    """
    global KERNEL_IDS

    # Check if kernel exists
    if kernel not in KERNEL_IDS:
        return {
            "suggestions": [],
            "message": f"🤔 No {kernel} kernel running. Try: execute('print(\"Hello!\")', kernel='{kernel}')",
        }

    # Get current kernel state
    try:
        # First, get the variables in the kernel
        var_result = await _execute_code(
            """
import sys
import json

def _get_kernel_suggestions():
    # Get all variables
    vars_info = []
    
    # Get imported modules
    imported_modules = set()
    for name, obj in globals().items():
        if hasattr(obj, '__module__') and not name.startswith('_'):
            if hasattr(obj, '__name__'):
                imported_modules.add(obj.__name__)
    
    # Analyze key variables
    for name, obj in globals().items():
        if name.startswith('_') or name in ['In', 'Out', 'exit', 'quit', 'get_ipython']:
            continue
            
        try:
            obj_type = type(obj).__name__
            obj_info = {"name": name, "type": obj_type}
            
            # Add size info for common types
            if hasattr(obj, 'shape'):
                obj_info["shape"] = str(obj.shape)
            elif hasattr(obj, '__len__') and not isinstance(obj, str):
                obj_info["length"] = len(obj)
            
            # Special handling for DataFrames
            if obj_type == 'DataFrame':
                obj_info["columns"] = list(obj.columns)[:10]  # First 10 columns
                obj_info["has_datetime"] = any('date' in col.lower() or 'time' in col.lower() for col in obj.columns)
                obj_info["numeric_columns"] = list(obj.select_dtypes(include=['number']).columns)[:10]
                
            vars_info.append(obj_info)
        except:
            pass
    
    result = {
        "variables": vars_info[:20],  # Top 20 variables
        "modules": list(imported_modules)
    }
    
    print(json.dumps(result))

_get_kernel_suggestions()
""",
            kernel,
        )

        if var_result.get("error"):
            # Simpler fallback
            var_result = await _execute_code(
                "import json; print(json.dumps({'variables': [], 'modules': []}))",
                kernel,
            )

        # Parse the kernel state
        import json

        try:
            kernel_info = json.loads(var_result.get("output", "{}"))
        except (json.JSONDecodeError, TypeError):
            kernel_info = {"variables": [], "modules": []}

        # Build contextual suggestions
        suggestions = []
        tips = []

        # Check for DataFrames
        dataframes = [
            v for v in kernel_info.get("variables", []) if v.get("type") == "DataFrame"
        ]
        if dataframes:
            for df in dataframes:
                df_name = df["name"]
                shape = df.get("shape", "")
                suggestions.append(
                    {
                        "context": f"📊 You have a DataFrame '{df_name}' loaded {shape}",
                        "actions": [
                            f"{df_name}.describe() - Get statistical summary",
                            f"{df_name}.info() - Check data types and memory usage",
                            f"{df_name}.head(10) - View first 10 rows",
                            f"{df_name}.isnull().sum() - Check for missing values",
                        ],
                    }
                )

                # Add datetime tip if relevant
                if df.get("has_datetime"):
                    tips.append(
                        f"💡 Pro tip: Found date/time columns in {df_name} - try pd.to_datetime() for time series analysis"
                    )

                # Add visualization suggestions if matplotlib/seaborn available
                if any(
                    mod in kernel_info.get("modules", [])
                    for mod in ["matplotlib", "seaborn", "plotly"]
                ):
                    suggestions.append(
                        {
                            "context": f"📈 Visualization ready for '{df_name}'",
                            "actions": [
                                f"{df_name}.plot() - Quick line plot",
                                f"{df_name}.hist() - Distribution of numeric columns",
                                f"{df_name}.corr() - Correlation matrix",
                                f"sns.heatmap({df_name}.corr()) - Correlation heatmap"
                                if "seaborn" in kernel_info.get("modules", [])
                                else None,
                            ],
                        }
                    )

        # Check for models
        models = [
            v
            for v in kernel_info.get("variables", [])
            if any(
                model_type in v.get("type", "")
                for model_type in ["Classifier", "Regressor", "Model", "Estimator"]
            )
        ]
        if models:
            for model in models:
                model_name = model["name"]
                suggestions.append(
                    {
                        "context": f"🤖 You have a model '{model_name}'",
                        "actions": [
                            f"{model_name}.score(X_test, y_test) - Evaluate model performance",
                            f"{model_name}.predict(X_test) - Make predictions",
                            f"{model_name}.feature_importances_ - Check feature importance (if available)",
                        ],
                    }
                )

        # Check for arrays/lists
        arrays = [
            v
            for v in kernel_info.get("variables", [])
            if v.get("type") in ["ndarray", "list", "Series"]
        ]
        if arrays and not dataframes:  # Only if no DataFrames (to avoid redundancy)
            for arr in arrays[:3]:  # Limit to 3 arrays
                arr_name = arr["name"]
                size_info = (
                    f" ({arr.get('shape', arr.get('length', ''))})"
                    if arr.get("shape") or arr.get("length")
                    else ""
                )
                suggestions.append(
                    {
                        "context": f"📐 You have array/list '{arr_name}'{size_info}",
                        "actions": [
                            f"np.mean({arr_name}) - Calculate mean",
                            f"np.std({arr_name}) - Calculate standard deviation",
                            f"plt.plot({arr_name}) - Quick visualization",
                        ],
                    }
                )

        # Module-specific suggestions
        modules = kernel_info.get("modules", [])

        if "pandas" in modules and not dataframes:
            suggestions.append(
                {
                    "context": "📚 Pandas is imported but no data loaded",
                    "actions": [
                        "pd.read_csv('data.csv') - Load CSV file",
                        "pd.read_excel('data.xlsx') - Load Excel file",
                        "pd.DataFrame({'x': [1,2,3], 'y': [4,5,6]}) - Create sample DataFrame",
                    ],
                }
            )

        if "sklearn" in modules:
            suggestions.append(
                {
                    "context": "🔬 Scikit-learn is ready",
                    "actions": [
                        "from sklearn.model_selection import train_test_split",
                        "from sklearn.ensemble import RandomForestClassifier",
                        "from sklearn.metrics import classification_report",
                    ],
                }
            )

        # If nothing specific found, give general suggestions
        if not suggestions:
            suggestions.append(
                {
                    "context": "🚀 Ready to start exploring!",
                    "actions": [
                        "import pandas as pd - Data manipulation",
                        "import numpy as np - Numerical computing",
                        "import matplotlib.pyplot as plt - Plotting",
                        "%matplotlib inline - Enable inline plots (Jupyter)",
                    ],
                }
            )

        # Format the response
        formatted_suggestions = []
        for sugg in suggestions:
            formatted_suggestions.append(f"\n{sugg['context']}")
            formatted_suggestions.append("You might want to:")
            for action in sugg["actions"]:
                if action:  # Skip None values
                    formatted_suggestions.append(f"  • {action}")

        # Add tips if any
        if tips:
            formatted_suggestions.append("\n" + "\n".join(tips))

        return {
            "message": "🔮 Based on your current state:",
            "suggestions": "\n".join(formatted_suggestions),
            "kernel": kernel,
            "variables_count": len(kernel_info.get("variables", [])),
            "modules_count": len(modules),
        }

    except Exception as e:
        return {
            "message": "🤔 Let me check what's available...",
            "suggestions": "Try: vars() to see your current variables",
            "error": str(e),
        }


@mcp.tool()
async def clear_kernel_state(kernel: str = None, confirm: bool = False) -> dict:
    """
    Clear kernel state (formerly 'reset') - ⚠️ DESTRUCTIVE OPERATION!

    This completely restarts kernels, losing all variables and state.
    Think twice before using this - the whole point is persistence!

    Args:
        kernel: Specific kernel to clear, or None for all kernels
        confirm: Must be True to actually perform the operation

    Returns:
        Confirmation or warning message

    Examples:
        >>> clear_kernel_state("python3")
        ⚠️ This will DELETE all variables in python3 kernel!
        Call again with confirm=True if you're sure.

        >>> clear_kernel_state("python3", confirm=True)
        🗑️ Cleared python3 kernel state

    When to use (rarely!):
        - Starting completely unrelated project
        - Recovering from corrupted state
        - Explicit cleanup request

    Better alternatives:
        - Just define new variables (old ones persist but don't interfere)
        - Use different kernel for different project
        - Create new notebook for new analysis
    """
    if not confirm:
        if kernel:
            return {
                "message": f"⚠️ This will DELETE all variables in {kernel} kernel!\nYour work will be lost. Call again with confirm=True if you're sure.",
                "requires_confirmation": True,
                "kernel": kernel,
            }
        else:
            kernel_list = list(KERNEL_IDS.keys()) if KERNEL_IDS else []
            return {
                "message": f"⚠️ This will DELETE all variables in ALL kernels ({', '.join(kernel_list) if kernel_list else 'none active'})!\nYour work will be lost. Call again with confirm=True if you're sure.",
                "requires_confirmation": True,
                "kernels": kernel_list,
            }

    # User confirmed, proceed with reset
    result = await reset(kernel)

    # Make the message clearer about what happened
    if "Deleted" in result.get("message", ""):
        cleared = result["message"].replace("Deleted", "🗑️ Cleared state for")
        return {"message": cleared, "cleared": True, "kernel": kernel}

    return result


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
                    f"{jupyter_url}/api/kernels/{kernel_id}", headers=headers
                )
                response.raise_for_status()
                debug_print(f"Deleted {kernel_name} kernel: {kernel_id}")
                deleted_kernels.append(kernel_name)
                async with KERNEL_IDS_LOCK:
                    del KERNEL_IDS[kernel_name]
            except Exception as e:
                debug_print(f"Failed to delete {kernel_name} kernel: {e}")

    if deleted_kernels:
        return {"message": f"Deleted kernels: {', '.join(deleted_kernels)}"}
    else:
        return {"message": "No kernels were deleted"}


def validate_notebook_name(name: str) -> str:
    """
    Validate and sanitize notebook names to prevent path traversal attacks.
    
    Args:
        name: The proposed notebook name
        
    Returns:
        str: Sanitized notebook name
        
    Raises:
        ValueError: If the name is invalid or potentially malicious
    """
    # Remove any .ipynb extension if provided
    if name.endswith('.ipynb'):
        name = name[:-6]
    
    # Check for empty or whitespace-only names
    if not name or not name.strip():
        raise ValueError("Notebook name cannot be empty")
    
    # Check for path traversal attempts
    if any(char in name for char in ['..', '/', '\\', '~']):
        raise ValueError("Notebook name cannot contain path traversal characters (.. / \\ ~)")
    
    # Check for absolute paths
    if name.startswith(('/', '\\')) or (len(name) > 1 and name[1] == ':'):
        raise ValueError("Notebook name cannot be an absolute path")
    
    # Validate characters - allow alphanumeric, dash, underscore, space
    if not re.match(r'^[a-zA-Z0-9_\- ]+$', name):
        raise ValueError("Notebook name can only contain letters, numbers, spaces, dashes, and underscores")
    
    # Limit length
    if len(name) > 255:
        raise ValueError("Notebook name is too long (max 255 characters)")
    
    # Check for reserved names that might cause issues
    reserved_names = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4',
                      'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 
                      'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
                      '.', '..']
    if name.upper() in reserved_names or name.upper().startswith(tuple(n + '.' for n in reserved_names if n not in ['.', '..'])):
        raise ValueError(f"'{name}' is a reserved name and cannot be used")
    
    # Sanitize spaces to underscores for better compatibility
    name = name.strip().replace(' ', '_')
    
    return name


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

    # Validate and sanitize the notebook name
    try:
        name = validate_notebook_name(name)
    except ValueError as e:
        return {"error": True, "message": str(e)}

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
            "metadata": {"kernelspec": kernel_spec},
            "nbformat": 4,
            "nbformat_minor": 5,
        },
    }

    # PUT to create the notebook
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{jupyter_url}/api/contents/{name}.ipynb",
            headers=headers,
            json=notebook_content,
        )
        response.raise_for_status()

    debug_print(f"Created notebook: {name}.ipynb with {kernel} kernel")

    return {
        "path": f"{name}.ipynb",
        "kernel": kernel,
        "kernel_spec": kernel_spec,
        "created": True,
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
    if not notebook.endswith(".ipynb"):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers
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
            "source": code
            if isinstance(code, list)
            else code.splitlines(keepends=True),
            "outputs": [],
        }

        # Add output if there was any
        if execution_result["output"]:
            if execution_result["error"]:
                error_parts = execution_result["output"].split(":", 1)
                ename = error_parts[0].strip()
                evalue = error_parts[1].strip() if len(error_parts) > 1 else ""
                new_cell["outputs"].append(
                    {
                        "output_type": "error",
                        "ename": ename,
                        "evalue": evalue,
                        "traceback": [execution_result["output"]],
                    }
                )
            else:
                new_cell["outputs"].append(
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": execution_result["output"],
                    }
                )

        # Append the new cell
        content["cells"].append(new_cell)

        # PUT the updated notebook back
        update_data = {"type": "notebook", "content": content}

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers, json=update_data
        )
        response.raise_for_status()

    debug_print(f"Added cell to notebook: {notebook}")

    return {"output": execution_result["output"], "cell_number": len(content["cells"])}


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
    if not name.endswith(".ipynb"):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{name}", headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Process cells
        cells = []
        for cell in content["cells"]:
            cell_info = {
                "type": cell["cell_type"],
                "source": "\n".join(cell["source"])
                if isinstance(cell["source"], list)
                else cell["source"],
            }

            # Add outputs for code cells
            if cell["cell_type"] == "code" and "outputs" in cell:
                outputs = []
                for output in cell["outputs"]:
                    if output["output_type"] == "stream":
                        outputs.append(output.get("text", ""))
                    elif output["output_type"] == "error":
                        outputs.append(
                            f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
                        )
                    elif output["output_type"] == "execute_result":
                        # Handle execute_result which may have data
                        if "text/plain" in output.get("data", {}):
                            outputs.append(output["data"]["text/plain"])

                # Join all outputs
                cell_info["output"] = (
                    "".join(outputs) if isinstance(outputs, list) else outputs
                )
            else:
                cell_info["output"] = ""

            cells.append(cell_info)

    debug_print(f"Read notebook: {name} ({len(cells)} cells)")

    return {"cells": cells}


@mcp.tool()
async def notebook(
    operation: str,
    name: str = None,
    content: str = None,
    cell_type: str = "code",
    kernel: str = "python3",
    destination: str = None,
    confirm: bool = False,
) -> dict:
    """
    Your notebook companion - natural operations without the cognitive load.

    Just tell me what you want to do, and I'll handle the details.

    Operations:
        "create" / "new" - Start a fresh notebook
        "add" / "append" - Add code or markdown to notebook
        "read" / "show" / "view" - Display notebook contents
        "list" / "ls" - List all notebooks
        "search" / "find" - Search across notebooks
        "run" / "execute" - Run all cells in notebook
        "stats" / "info" - Get notebook statistics
        "clear" - Clear all outputs from notebook
        "copy" / "duplicate" - Make a copy of notebook
        "rename" / "mv" - Rename a notebook
        "delete" / "rm" - Delete a notebook (with confirmation)

    Args:
        operation: What you want to do (see above)
        name: Notebook name (I'll handle the .ipynb extension)
        content: Code or markdown content (for add operations)
        cell_type: "code" or "markdown" for add operations
        kernel: Kernel type for create operations
        destination: Destination name for copy/rename operations
        confirm: Set to True to confirm destructive operations

    Examples:
        # Create and build a notebook naturally
        >>> notebook("create", "analysis")
        📓 Created notebook: analysis.ipynb

        >>> notebook("add", "analysis", "import pandas as pd\\ndf = pd.read_csv('data.csv')")
        ✅ Added code cell #1 to analysis.ipynb

        >>> notebook("add", "analysis", "# Data Analysis\\nExploring sales data", cell_type="markdown")
        📝 Added markdown cell #2 to analysis.ipynb

        # Natural reading
        >>> notebook("show", "analysis")
        📓 Notebook: analysis.ipynb (2 cells)
        [Shows formatted notebook content]

        # Quick operations
        >>> notebook("list")
        📚 Found 3 notebooks: analysis.ipynb, experiments.ipynb, scratch.ipynb

        >>> notebook("search", content="RandomForest")
        🔍 Found 2 matches in experiments.ipynb

    The magic: Operations are forgiving and helpful. Forgot .ipynb? No problem.
    Need suggestions? I'll provide them. Want confirmation on dangerous ops? Got it.
    """

    # Normalize operation names
    op_map = {
        "create": "create",
        "new": "create",
        "make": "create",
        "add": "add",
        "append": "add",
        "write": "add",
        "read": "read",
        "show": "read",
        "view": "read",
        "cat": "read",
        "list": "list",
        "ls": "list",
        "dir": "list",
        "search": "search",
        "find": "search",
        "grep": "search",
        "run": "run",
        "execute": "run",
        "run_all": "run",
        "stats": "stats",
        "info": "stats",
        "stat": "stats",
        "clear": "clear",
        "clear_outputs": "clear",
        "copy": "copy",
        "duplicate": "copy",
        "cp": "copy",
        "rename": "rename",
        "mv": "rename",
        "move": "rename",
        "delete": "delete",
        "rm": "delete",
        "remove": "delete",
    }

    op = op_map.get(operation.lower(), operation.lower())

    # Auto-handle .ipynb extension
    if name and not name.endswith(".ipynb"):
        notebook_name = name
        name = f"{name}.ipynb"
    else:
        notebook_name = name.replace(".ipynb", "") if name else None

    # Route to appropriate operation
    if op == "create":
        if not notebook_name:
            return {"error": "📝 Please provide a notebook name"}

        # Create notebook directly on filesystem for reliability
        import json
        import os
        import re

        # Sanitize notebook name - remove any potentially dangerous characters
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", notebook_name)
        safe_name = safe_name[:100]  # Limit length to prevent filesystem issues

        # Ensure we're writing to current directory only
        filename = os.path.basename(f"{safe_name}.ipynb")
        filepath = os.path.join(os.getcwd(), filename)

        # Check if file already exists
        if os.path.exists(filepath):
            return {
                "error": f"📓 Notebook '{filename}' already exists. Choose a different name or delete the existing notebook.",
                "path": filename,
            }

        # Get kernel specification
        kernel_spec = _get_kernel_spec(kernel)

        # Create notebook content
        notebook_content = {
            "cells": [],
            "metadata": {
                "kernelspec": kernel_spec,
                "language_info": {"name": kernel_spec.get("language", "python")},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }

        # Write notebook to filesystem
        try:
            with open(filepath, "w") as f:
                json.dump(notebook_content, f, indent=2)

            return {
                "message": f"📓 Created notebook: {filename}",
                "path": filename,
                "kernel": kernel,
            }
        except Exception as e:
            return {
                "error": f"❌ Failed to create notebook: {str(e)}",
                "path": filename,
            }

    elif op == "add":
        if not notebook_name or not content:
            return {"error": "📝 Please provide notebook name and content"}

        cell_type = cell_type.lower()

        # Direct filesystem implementation for reliability
        import json
        import os
        import re

        # Sanitize notebook name
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", notebook_name.replace(".ipynb", ""))
        safe_name = safe_name[:100]

        # Ensure notebook name ends with .ipynb
        notebook_file = f"{safe_name}.ipynb"
        filepath = os.path.join(os.getcwd(), notebook_file)

        try:
            # Read existing notebook
            with open(filepath, "r") as f:
                notebook_content = json.load(f)
        except FileNotFoundError:
            return {
                "error": f"📝 Notebook {notebook_file} not found. Create it first with notebook('create', '{safe_name}')"
            }

        if cell_type == "markdown" or cell_type == "md":
            # Create new markdown cell
            new_cell = {
                "cell_type": "markdown",
                "metadata": {},
                "source": content.splitlines(keepends=True)
                if isinstance(content, str)
                else content,
            }
        else:
            # Code cell - execute and capture output
            kernel_spec_name = "python3"  # default
            if (
                "metadata" in notebook_content
                and "kernelspec" in notebook_content["metadata"]
            ):
                kernel_spec_name = notebook_content["metadata"]["kernelspec"].get(
                    "name", "python3"
                )

            # Convert kernel spec name to our kernel type
            kernel_type = _get_kernel_type_from_spec(kernel_spec_name)

            # Execute the code
            execution_result = await _execute_code(content, kernel_type)

            # Create new code cell
            new_cell = {
                "cell_type": "code",
                "metadata": {},
                "source": content.splitlines(keepends=True)
                if isinstance(content, str)
                else content,
                "outputs": [],
                "execution_count": len(notebook_content["cells"]) + 1,
            }

            # Add output if there was any
            if execution_result.get("output"):
                if execution_result.get("error"):
                    new_cell["outputs"].append(
                        {
                            "output_type": "error",
                            "ename": "Error",
                            "evalue": execution_result["output"],
                            "traceback": [execution_result["output"]],
                        }
                    )
                else:
                    new_cell["outputs"].append(
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": execution_result["output"],
                        }
                    )

        # Append the new cell
        notebook_content["cells"].append(new_cell)
        cell_number = len(notebook_content["cells"])

        # Write updated notebook back to filesystem
        try:
            with open(filepath, "w") as f:
                json.dump(notebook_content, f, indent=2)

            cell_type_emoji = "📝" if cell_type in ["markdown", "md"] else "✅"
            return {
                "message": f"{cell_type_emoji} Added {cell_type} cell #{cell_number} to {notebook_file}",
                "cell_number": cell_number,
            }
        except Exception as e:
            return {"error": f"❌ Failed to save notebook: {str(e)}"}

    elif op == "read":
        if not notebook_name:
            return {"error": "📝 Please provide a notebook name"}

        # Direct implementation of read functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure notebook name ends with .ipynb
        notebook_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # GET the notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{notebook_file}", headers=headers
            )
            response.raise_for_status()

            notebook_data = response.json()
            content = notebook_data["content"]

            # Process cells
            cells = []
            for cell in content["cells"]:
                cell_info = {
                    "type": cell["cell_type"],
                    "source": "\n".join(cell["source"])
                    if isinstance(cell["source"], list)
                    else cell["source"],
                }

                # Add outputs for code cells
                if cell["cell_type"] == "code" and "outputs" in cell:
                    outputs = []
                    for output in cell["outputs"]:
                        if output["output_type"] == "stream":
                            outputs.append(output.get("text", ""))
                        elif output["output_type"] == "error":
                            outputs.append(
                                f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
                            )
                        elif output["output_type"] == "execute_result":
                            # Handle execute_result which may have data
                            if "text/plain" in output.get("data", {}):
                                outputs.append(output["data"]["text/plain"])

                    # Join all outputs
                    cell_info["output"] = (
                        "".join(outputs) if isinstance(outputs, list) else outputs
                    )
                else:
                    cell_info["output"] = ""

                cells.append(cell_info)

        # Format cells nicely
        formatted = [f"📓 Notebook: {name} ({len(cells)} cells)\n"]
        for i, cell in enumerate(cells):
            if cell["type"] == "markdown":
                preview = cell["source"][:80].replace("\n", " ")
                if len(cell["source"]) > 80:
                    preview += "..."
                formatted.append(f"[{i}] 📝 Markdown: {preview}")
            else:
                lines = cell["source"].split("\n")
                preview = lines[0][:60] if lines else "(empty)"
                if len(lines) > 1 or len(lines[0]) > 60:
                    preview += "..."
                formatted.append(f"[{i}] 💻 Code: {preview}")
                if cell.get("output"):
                    out_preview = cell["output"][:60].strip().replace("\n", " ")
                    if len(cell["output"]) > 60:
                        out_preview += "..."
                    formatted.append(f"    → {out_preview}")

        return {
            "message": "\n".join(formatted),
            "cells": cells,
            "total_cells": len(cells),
        }

    elif op == "list":
        # Get notebooks directly via Jupyter API
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{jupyter_url}/api/contents", headers=headers)
            response.raise_for_status()
            contents = response.json()["content"]
            notebooks = [
                item["name"] for item in contents if item["type"] == "notebook"
            ]

        if not notebooks:
            return {
                "message": "📚 No notebooks found. Create one with: notebook('create', 'my_first_notebook')"
            }

        return {
            "message": f"📚 Found {len(notebooks)} notebook{'s' if len(notebooks) != 1 else ''}: {', '.join(notebooks)}",
            "notebooks": notebooks,
            "count": len(notebooks),
        }

    elif op == "search":
        query = content or ""
        if not query:
            return {"error": "🔍 Please provide search query in content parameter"}

        # Direct implementation of search functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        matches = []

        async with httpx.AsyncClient() as client:
            # First, get list of all notebooks
            response = await client.get(f"{jupyter_url}/api/contents", headers=headers)
            response.raise_for_status()

            contents = response.json()["content"]
            notebooks = [item for item in contents if item["type"] == "notebook"]

            # Search each notebook
            for nb_info in notebooks:
                nb_name = nb_info["name"]

                # Get notebook content
                response = await client.get(
                    f"{jupyter_url}/api/contents/{nb_name}", headers=headers
                )
                response.raise_for_status()

                notebook_data = response.json()
                content = notebook_data["content"]

                # Search through cells
                for cell_idx, cell in enumerate(content["cells"]):
                    # Get cell source as string
                    cell_source = (
                        "".join(cell["source"])
                        if isinstance(cell["source"], list)
                        else cell["source"]
                    )

                    # Case-insensitive search
                    if query.lower() in cell_source.lower():
                        # Create preview (first 150 chars or until newline)
                        preview = cell_source.strip()
                        if len(preview) > 150:
                            preview = preview[:150] + "..."
                        else:
                            # If short, show the whole cell
                            first_newline = preview.find("\n")
                            if first_newline > 0 and first_newline < 150:
                                preview = preview[:first_newline] + "..."

                        matches.append(
                            {
                                "notebook": nb_name,
                                "cell": cell_idx,
                                "cell_type": cell["cell_type"],
                                "preview": preview,
                            }
                        )

        if not matches:
            return {"message": f"🔍 No matches found for '{query}'"}

        # Group by notebook
        by_notebook = {}
        for match in matches:
            nb = match["notebook"]
            if nb not in by_notebook:
                by_notebook[nb] = []
            by_notebook[nb].append(match)

        formatted = [
            f"🔍 Found {len(matches)} match{'es' if len(matches) != 1 else ''} for '{query}':\n"
        ]
        for nb, nb_matches in by_notebook.items():
            formatted.append(f"\n📓 {nb} ({len(nb_matches)} matches):")
            for m in nb_matches[:3]:  # Show first 3 matches per notebook
                formatted.append(
                    f"  [{m['cell']}] {m['cell_type']}: {m['preview'][:60]}..."
                )
            if len(nb_matches) > 3:
                formatted.append(f"  ... and {len(nb_matches) - 3} more matches")

        return {
            "message": "\n".join(formatted),
            "matches": matches,
            "total": len(matches),
        }

    elif op == "run":
        if not notebook_name:
            return {"error": "📝 Please provide a notebook name"}

        # Direct implementation of run_all_cells functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure notebook name ends with .ipynb
        notebook_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # GET the notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{notebook_file}", headers=headers
            )
            response.raise_for_status()

            notebook_data = response.json()
            content = notebook_data["content"]

            # Get the kernel spec from the notebook metadata
            kernel_spec_name = "python3"  # default
            if "metadata" in content and "kernelspec" in content["metadata"]:
                kernel_spec_name = content["metadata"]["kernelspec"].get(
                    "name", "python3"
                )

            # Convert kernel spec name to our kernel type
            kernel_type = _get_kernel_type_from_spec(kernel_spec_name)

            # Execute all code cells
            executed_count = 0
            error_count = 0
            execution_number = 1

            for cell_idx, cell in enumerate(content["cells"]):
                if cell["cell_type"] == "code":
                    # Get the code from the cell
                    code = (
                        "".join(cell["source"])
                        if isinstance(cell["source"], list)
                        else cell["source"]
                    )

                    # Execute the code with the notebook's kernel
                    execution_result = await _execute_code(code, kernel_type)

                    # Clear existing outputs and update with new ones
                    cell["outputs"] = []

                    if execution_result["output"]:
                        if execution_result["error"]:
                            error_count += 1
                            error_parts = execution_result["output"].split(":", 1)
                            ename = error_parts[0].strip()
                            evalue = (
                                error_parts[1].strip() if len(error_parts) > 1 else ""
                            )
                            cell["outputs"].append(
                                {
                                    "output_type": "error",
                                    "ename": ename,
                                    "evalue": evalue,
                                    "traceback": [execution_result["output"]],
                                }
                            )
                        else:
                            cell["outputs"].append(
                                {
                                    "output_type": "stream",
                                    "name": "stdout",
                                    "text": execution_result["output"],
                                }
                            )

                    # Update execution count
                    cell["execution_count"] = execution_number
                    execution_number += 1
                    executed_count += 1

            # PUT the updated notebook back
            update_data = {"type": "notebook", "content": content}

            response = await client.put(
                f"{jupyter_url}/api/contents/{notebook_file}",
                headers=headers,
                json=update_data,
            )
            response.raise_for_status()

        status = "✅" if error_count == 0 else "⚠️"
        return {
            "message": f"{status} Executed {executed_count} cells in {name} ({error_count} errors)",
            "executed": executed_count,
            "errors": error_count,
        }

    elif op == "stats":
        if not notebook_name:
            return {"error": "📝 Please provide a notebook name"}

        # Direct implementation of get_notebook_stats functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure notebook name ends with .ipynb
        notebook_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # GET the notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{notebook_file}", headers=headers
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
                    line_count = cell_source.count("\n") + 1 if cell_source else 0

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

            # Get kernel information
            kernel_name = "python3"
            kernel_display_name = "Python 3"
            if "metadata" in content and "kernelspec" in content["metadata"]:
                kernel_spec = content["metadata"]["kernelspec"]
                kernel_name = kernel_spec.get("name", "python3")
                kernel_display_name = kernel_spec.get("display_name", "Python 3")

            result = {
                "notebook": notebook_file,
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
                "kernel_display_name": kernel_display_name,
            }

        return {
            "message": f"📊 {name}: {result['code_cells']} code cells, {result['markdown_cells']} markdown cells, {result['code_lines']} lines of code",
            **result,
        }

    elif op == "clear":
        if not notebook_name:
            return {"error": "📝 Please provide a notebook name"}

        # Direct implementation of clear_notebook_outputs functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure notebook name ends with .ipynb
        notebook_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # GET the notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{notebook_file}", headers=headers
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
            update_data = {"type": "notebook", "content": content}

            response = await client.put(
                f"{jupyter_url}/api/contents/{notebook_file}",
                headers=headers,
                json=update_data,
            )
            response.raise_for_status()

        return {
            "message": f"🧹 Cleared outputs from {cleared_count} cells in {name}",
            "cleared": cleared_count,
        }

    elif op == "copy":
        if not notebook_name:
            return {"error": "📝 Please provide source notebook name"}

        dest = destination or f"{notebook_name}_copy"
        dest_name = dest.replace(".ipynb", "") if dest.endswith(".ipynb") else dest
        dest_file = f"{dest_name}.ipynb"

        # Direct implementation of copy_notebook functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure source notebook name ends with .ipynb
        source_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # GET the source notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{source_file}", headers=headers
            )
            response.raise_for_status()

            notebook_data = response.json()

            # Create copy data with the new destination
            copy_data = {"type": "notebook", "content": notebook_data["content"]}

            # PUT to create the destination notebook
            response = await client.put(
                f"{jupyter_url}/api/contents/{dest_file}",
                headers=headers,
                json=copy_data,
            )
            response.raise_for_status()

        return {
            "message": f"📋 Copied {name} → {dest_file}",
            "source": name,
            "destination": dest_file,
        }

    elif op == "rename":
        if not notebook_name:
            return {"error": "📝 Please provide current notebook name"}

        new_name = destination or content
        if not new_name:
            return {"error": "📝 Please provide new name"}

        new_file = new_name if new_name.endswith(".ipynb") else f"{new_name}.ipynb"

        # Direct implementation of rename_notebook functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure old notebook name ends with .ipynb
        old_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # PATCH to rename the notebook
            response = await client.patch(
                f"{jupyter_url}/api/contents/{old_file}",
                headers=headers,
                json={"path": new_file},
            )
            response.raise_for_status()

        return {
            "message": f"✏️ Renamed {name} → {new_file}",
            "old_name": name,
            "new_name": new_file,
        }

    elif op == "delete":
        if not notebook_name:
            return {"error": "📝 Please provide notebook name"}

        # Add confirmation check
        if not confirm:
            return {
                "message": f"⚠️ Delete {name}? Call again with confirm=True to proceed",
                "requires_confirmation": True,
            }

        # Direct implementation of delete_notebook functionality
        jupyter_url = JUPYTER_URL
        headers = {}
        if JUPYTER_TOKEN:
            headers["Authorization"] = f"token {JUPYTER_TOKEN}"

        # Ensure notebook name ends with .ipynb
        notebook_file = (
            notebook_name
            if notebook_name.endswith(".ipynb")
            else f"{notebook_name}.ipynb"
        )

        async with httpx.AsyncClient() as client:
            # DELETE the notebook
            response = await client.delete(
                f"{jupyter_url}/api/contents/{notebook_file}", headers=headers
            )
            response.raise_for_status()

        return {"message": f"🗑️ Deleted {name}", "deleted": True}

    else:
        return {
            "error": f"❓ Unknown operation '{operation}'",
            "suggestion": "Try: create, add, read, list, search, run, stats, clear, copy, rename, or delete",
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
        response = await client.get(f"{jupyter_url}/api/contents", headers=headers)
        response.raise_for_status()

        contents = response.json()["content"]

        # Filter for notebooks only
        notebooks = []
        for item in contents:
            if item["type"] == "notebook":
                notebooks.append(item["name"])

    debug_print(f"Found {len(notebooks)} notebooks")

    return {"notebooks": notebooks}


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
    if not notebook.endswith(".ipynb"):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers
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
                "output": "",
            }

        # Get the cell
        target_cell = content["cells"][cell]

        # Check if it's a code cell
        if target_cell["cell_type"] != "code":
            return {
                "error": f"Cell {cell} is a {target_cell['cell_type']} cell, not a code cell.",
                "output": "",
            }

        # Get the code from the cell
        code = (
            "".join(target_cell["source"])
            if isinstance(target_cell["source"], list)
            else target_cell["source"]
        )

        # Execute the code with the notebook's kernel
        execution_result = await _execute_code(code, kernel_type)

        # Update the cell's outputs
        target_cell["outputs"] = []
        if execution_result["output"]:
            if execution_result["error"]:
                error_parts = execution_result["output"].split(":", 1)
                ename = error_parts[0].strip()
                evalue = error_parts[1].strip() if len(error_parts) > 1 else ""
                target_cell["outputs"].append(
                    {
                        "output_type": "error",
                        "ename": ename,
                        "evalue": evalue,
                        "traceback": [execution_result["output"]],
                    }
                )
            else:
                target_cell["outputs"].append(
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": execution_result["output"],
                    }
                )

        # Update execution count
        target_cell["execution_count"] = cell + 1

        # PUT the updated notebook back
        update_data = {"type": "notebook", "content": content}

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers, json=update_data
        )
        response.raise_for_status()

    debug_print(f"Executed cell {cell} in notebook: {notebook}")

    return {
        "output": execution_result["output"],
        "error": execution_result["error"],
        "cell": cell,
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
    if not notebook.endswith(".ipynb"):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()
        content = notebook_data["content"]

        # Create new markdown cell
        new_cell = {
            "cell_type": "markdown",
            "metadata": {},
            "source": text
            if isinstance(text, list)
            else text.splitlines(keepends=True),
        }

        # Append the new cell
        content["cells"].append(new_cell)

        # PUT the updated notebook back
        update_data = {"type": "notebook", "content": content}

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers, json=update_data
        )
        response.raise_for_status()

    debug_print(f"Added markdown cell to notebook: {notebook}")

    return {"cell_number": len(content["cells"])}


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

    # Validate and sanitize the notebook name
    try:
        name = validate_notebook_name(name)
    except ValueError as e:
        return {"error": True, "message": str(e), "deleted": False}

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook name ends with .ipynb
    if not name.endswith(".ipynb"):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # DELETE the notebook
        response = await client.delete(
            f"{jupyter_url}/api/contents/{name}", headers=headers
        )
        response.raise_for_status()

    debug_print(f"Deleted notebook: {name}")

    return {"deleted": True, "path": name}


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

    # Validate and sanitize both notebook names
    try:
        old_name = validate_notebook_name(old_name)
        new_name = validate_notebook_name(new_name)
    except ValueError as e:
        return {"error": True, "message": str(e), "renamed": False}

    # Use configured Jupyter URL
    jupyter_url = JUPYTER_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    # Ensure notebook names end with .ipynb
    if not old_name.endswith(".ipynb"):
        old_name = f"{old_name}.ipynb"
    if not new_name.endswith(".ipynb"):
        new_name = f"{new_name}.ipynb"

    async with httpx.AsyncClient() as client:
        # PATCH to rename the notebook
        response = await client.patch(
            f"{jupyter_url}/api/contents/{old_name}",
            headers=headers,
            json={"path": new_name},
        )
        response.raise_for_status()

    debug_print(f"Renamed notebook: {old_name} to {new_name}")

    return {"old_path": old_name, "new_path": new_name, "renamed": True}


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
        response = await client.get(f"{jupyter_url}/api/contents", headers=headers)
        response.raise_for_status()

        contents = response.json()["content"]
        notebooks = [item for item in contents if item["type"] == "notebook"]

        # Search each notebook
        for nb_info in notebooks:
            nb_name = nb_info["name"]

            # Get notebook content
            response = await client.get(
                f"{jupyter_url}/api/contents/{nb_name}", headers=headers
            )
            response.raise_for_status()

            notebook_data = response.json()
            content = notebook_data["content"]

            # Search through cells
            for cell_idx, cell in enumerate(content["cells"]):
                # Get cell source as string
                cell_source = (
                    "".join(cell["source"])
                    if isinstance(cell["source"], list)
                    else cell["source"]
                )

                # Case-insensitive search
                if query.lower() in cell_source.lower():
                    # Create preview (first 150 chars or until newline)
                    preview = cell_source.strip()
                    if len(preview) > 150:
                        preview = preview[:150] + "..."
                    else:
                        # If short, show the whole cell
                        first_newline = preview.find("\n")
                        if first_newline > 0 and first_newline < 150:
                            preview = preview[:first_newline] + "..."

                    matches.append(
                        {
                            "notebook": nb_name,
                            "cell": cell_idx,
                            "cell_type": cell["cell_type"],
                            "preview": preview,
                        }
                    )

                    debug_print(f"Found match in {nb_name}, cell {cell_idx}")

    debug_print(f"Search complete: found {len(matches)} matches for '{query}'")

    return {"query": query, "matches": matches, "total": len(matches)}


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
    if not name.endswith(".ipynb"):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{name}", headers=headers
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
        update_data = {"type": "notebook", "content": content}

        response = await client.put(
            f"{jupyter_url}/api/contents/{name}", headers=headers, json=update_data
        )
        response.raise_for_status()

    debug_print(f"Cleared outputs from {cleared_count} cells in notebook: {name}")

    return {
        "notebook": name,
        "cleared": cleared_count,
        "message": f"Cleared outputs from {cleared_count} cells",
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
    if not notebook.endswith(".ipynb"):
        notebook = f"{notebook}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers
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
                code = (
                    "".join(cell["source"])
                    if isinstance(cell["source"], list)
                    else cell["source"]
                )

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
                        cell["outputs"].append(
                            {
                                "output_type": "error",
                                "ename": ename,
                                "evalue": evalue,
                                "traceback": [execution_result["output"]],
                            }
                        )
                    else:
                        cell["outputs"].append(
                            {
                                "output_type": "stream",
                                "name": "stdout",
                                "text": execution_result["output"],
                            }
                        )

                # Update execution count
                cell["execution_count"] = execution_number
                execution_number += 1
                executed_count += 1

        # PUT the updated notebook back
        update_data = {"type": "notebook", "content": content}

        response = await client.put(
            f"{jupyter_url}/api/contents/{notebook}", headers=headers, json=update_data
        )
        response.raise_for_status()

    debug_print(
        f"Executed {executed_count} cells in notebook: {notebook} ({error_count} errors)"
    )

    return {
        "notebook": notebook,
        "executed": executed_count,
        "errors": error_count,
        "message": f"Executed {executed_count} cells ({error_count} errors)",
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
    if not source.endswith(".ipynb"):
        source = f"{source}.ipynb"
    if not destination.endswith(".ipynb"):
        destination = f"{destination}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the source notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{source}", headers=headers
        )
        response.raise_for_status()

        notebook_data = response.json()

        # Create copy data with the new destination
        copy_data = {"type": "notebook", "content": notebook_data["content"]}

        # PUT to create the destination notebook
        response = await client.put(
            f"{jupyter_url}/api/contents/{destination}", headers=headers, json=copy_data
        )
        response.raise_for_status()

    debug_print(f"Copied notebook: {source} -> {destination}")

    return {
        "source": source,
        "destination": destination,
        "copied": True,
        "message": f"Successfully copied {source} to {destination}",
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
    if not name.endswith(".ipynb"):
        name = f"{name}.ipynb"

    async with httpx.AsyncClient() as client:
        # GET the notebook content
        response = await client.get(
            f"{jupyter_url}/api/contents/{name}", headers=headers
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
                line_count = cell_source.count("\n") + 1 if cell_source else 0

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
            kernel_display = content["metadata"]["kernelspec"].get(
                "display_name", kernel_name
            )
        else:
            kernel_display = kernel_name

    debug_print(
        f"Stats for {name}: {total_cells} cells, {total_code_lines} lines of code"
    )

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
        "kernel_display_name": kernel_display,
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

    kernel_spec_name = _get_kernel_spec_name(kernel)
    debug_print(
        f"Stream execute: Mapping kernel '{kernel}' to spec name '{kernel_spec_name}'"
    )

    jupyter_url = JUPYTER_URL
    ws_url = JUPYTER_WS_URL

    headers = {}
    if JUPYTER_TOKEN:
        headers["Authorization"] = f"token {JUPYTER_TOKEN}"

    if kernel not in KERNEL_IDS:
        async with httpx.AsyncClient() as client:
            # Check if kernel is available before attempting to create
            kernels_data = await _get_available_kernels(jupyter_url, headers)
            available_kernels = list(kernels_data.get("kernelspecs", {}).keys())

            if kernel_spec_name not in available_kernels:
                debug_print(
                    f"Warning: Kernel spec '{kernel_spec_name}' not found in available kernels: {available_kernels}"
                )
                debug_print("Attempting to create anyway...")

            try:
                kernel_data = {"name": kernel_spec_name}
                debug_print(
                    f"Attempting to create {kernel} kernel (spec: {kernel_spec_name})..."
                )
                response = await client.post(
                    f"{jupyter_url}/api/kernels", headers=headers, json=kernel_data
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                debug_print(
                    f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}"
                )
                if e.response.status_code == 500:
                    debug_print(
                        f"Kernel spec '{kernel_spec_name}' may not be available in Jupyter."
                    )
                    debug_print(f"Available kernels: {available_kernels}")
                    debug_print(f"Response text: {e.response.text}")
                raise Exception(
                    f"Cannot create {kernel} kernel (spec: {kernel_spec_name}): {e.response.status_code} {e.response.reason_phrase}. Available kernels: {available_kernels}"
                )
            except Exception as e:
                debug_print(
                    f"Error creating {kernel} kernel (spec: {kernel_spec_name}): {e}"
                )
                raise Exception(
                    f"Failed to create {kernel} kernel (spec: {kernel_spec_name}): {str(e)}"
                )

            kernel_info = response.json()
            kernel_session = str(uuid.uuid4())
            async with KERNEL_IDS_LOCK:
                KERNEL_IDS[kernel] = {"id": kernel_info["id"], "session": kernel_session}

            debug_print(
                f"Created {kernel} kernel: {KERNEL_IDS[kernel]['id']} with session: {KERNEL_IDS[kernel]['session'][:8]}..."
            )

            if kernel in ["deno", "rust"]:
                debug_print(f"Waiting for {kernel} kernel to initialize...")
                ready = await _ensure_kernel_ready(
                    KERNEL_IDS[kernel]["id"],
                    KERNEL_IDS[kernel]["session"],
                    ws_url,
                    headers,
                )
                if not ready:
                    debug_print(f"Warning: {kernel} kernel may not be fully ready")
                if kernel == "rust":
                    await asyncio.sleep(
                        2.0
                    )  # Rust needs more time for compilation environment
                else:
                    await asyncio.sleep(1.0)
            
            # Set kernel_id and session for the newly created kernel
            async with KERNEL_IDS_LOCK:
                kernel_id = KERNEL_IDS[kernel]['id']
                kernel_session = KERNEL_IDS[kernel]['session']
    else:
        async with KERNEL_IDS_LOCK:
            kernel_id = KERNEL_IDS[kernel]['id']
        debug_print(f"Using existing {kernel} kernel: {kernel_id}")

        # Verify the kernel still exists before trying to connect
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{jupyter_url}/api/kernels/{kernel_id}",
                    headers=headers,
                )
                if response.status_code == 404:
                    debug_print(
                        f"Kernel {kernel_id} no longer exists, removing from cache"
                    )
                    async with KERNEL_IDS_LOCK:
                        del KERNEL_IDS[kernel]
                    # Recursively call to create a new kernel
                    return await stream_execute(code, kernel)
                response.raise_for_status()
                debug_print(f"Confirmed kernel {kernel_id} still exists")
        except Exception as e:
            debug_print(f"Error checking kernel existence: {e}")
            debug_print("Removing cached kernel and creating new one")
            async with KERNEL_IDS_LOCK:
                del KERNEL_IDS[kernel]
            return await stream_execute(code, kernel)

    # Get kernel info with lock
    async with KERNEL_IDS_LOCK:
        kernel_id = KERNEL_IDS[kernel]['id']
        kernel_session = KERNEL_IDS[kernel]['session']
    
    ws_endpoint = f"{ws_url}/api/kernels/{kernel_id}/channels"
    if JUPYTER_TOKEN:
        ws_endpoint += f"?token={JUPYTER_TOKEN}"

    outputs = []
    completed = False
    error_occurred = False

    websocket = await _connect_with_backoff(ws_endpoint, subprotocols=["kernel.v5.2"])
    async with websocket:
        msg_id = str(uuid.uuid4())
        execute_msg = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "session": kernel_session,
                "username": "mcp",
                "version": "5.2",
                "date": datetime.utcnow().isoformat() + "Z",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False,
            },
            "channel": "shell",
        }

        await websocket.send(json.dumps(execute_msg))
        debug_print("Sent execute_request for streaming execution")

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
                        outputs.append(
                            {
                                "type": "stream",
                                "stream": stream_name,
                                "text": text,
                                "timestamp": timestamp,
                            }
                        )
                        debug_print(f"  Stream output at {timestamp}: {text.strip()}")

                # Check for display data
                elif msg_type == "display_data":
                    content = msg_data.get("content", {})
                    data = content.get("data", {})
                    if "text/plain" in data:
                        outputs.append(
                            {
                                "type": "display_data",
                                "text": data["text/plain"],
                                "timestamp": timestamp,
                            }
                        )
                        debug_print(f"  Display data at {timestamp}")

                # Check for execute result
                elif msg_type == "execute_result":
                    content = msg_data.get("content", {})
                    data = content.get("data", {})
                    if "text/plain" in data:
                        outputs.append(
                            {
                                "type": "execute_result",
                                "text": data["text/plain"],
                                "timestamp": timestamp,
                            }
                        )
                        debug_print(f"  Execute result at {timestamp}")

                # Check for errors
                elif msg_type == "error":
                    content = msg_data.get("content", {})
                    ename = content.get("ename", "Error")
                    evalue = content.get("evalue", "")
                    outputs.append(
                        {
                            "type": "error",
                            "error_name": ename,
                            "error_value": evalue,
                            "timestamp": timestamp,
                        }
                    )
                    error_occurred = True
                    debug_print(f"  Error at {timestamp}: {ename}: {evalue}")

                elif msg_type == "execute_reply":
                    status = msg_data.get("content", {}).get("status", "unknown")
                    completed = True
                    debug_print(f"  Execution completed with status: {status}")

        except Exception as e:
            debug_print(f"Error during streaming execution: {e}")
            error_occurred = True
            outputs.append(
                {
                    "type": "error",
                    "error_name": "StreamingError",
                    "error_value": str(e),
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

    return {
        "outputs": outputs,
        "completed": completed,
        "error": error_occurred,
        "total_outputs": len(outputs),
    }


if __name__ == "__main__":
    import sys
    import logging

    # Set up logging for debugging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    try:
        mcp.run()
    except KeyboardInterrupt:
        debug_print("Server stopped by user")
    except Exception as e:
        debug_print(f"Server error: {e}")
        raise
