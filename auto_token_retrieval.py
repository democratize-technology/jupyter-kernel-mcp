#!/usr/bin/env python3
"""
Auto-retrieve Jupyter token via API
This solves the authentication friction by automatically finding tokens
"""

import json
import os
import sys
import subprocess
from pathlib import Path

def get_token_from_runtime_files():
    """Check Jupyter runtime files for active servers with tokens"""
    runtime_dir = Path.home() / ".local/share/jupyter/runtime"
    if runtime_dir.exists():
        # Find all nbserver files
        server_files = sorted(runtime_dir.glob("nbserver-*.json"), 
                            key=lambda x: x.stat().st_mtime, 
                            reverse=True)
        
        for server_file in server_files:
            try:
                with open(server_file) as f:
                    data = json.load(f)
                    token = data.get("token")
                    if token:
                        return {
                            "token": token,
                            "url": data.get("url", "http://localhost:8888"),
                            "source": f"runtime file: {server_file.name}"
                        }
            except Exception:
                continue
    return None

def get_token_from_config():
    """Check Jupyter config for hardcoded tokens"""
    config_paths = [
        Path.home() / ".jupyter/jupyter_server_config.py",
        Path.home() / ".jupyter/jupyter_notebook_config.py",
    ]
    
    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path) as f:
                    content = f.read()
                    # Look for c.NotebookApp.token or c.ServerApp.token
                    import re
                    token_match = re.search(r'c\.(ServerApp|NotebookApp)\.token\s*=\s*["\']([^"\']+)["\']', content)
                    if token_match:
                        return {
                            "token": token_match.group(2),
                            "url": "http://localhost:8888",
                            "source": f"config file: {config_path.name}"
                        }
            except Exception:
                continue
    return None

def get_token_via_api(host="localhost", port=8888, protocol="http"):
    """Try to access Jupyter API without auth to get login page with token info"""
    try:
        import urllib.request
        import urllib.error
        
        # First try to access the API endpoint
        url = f"{protocol}://{host}:{port}/api"
        try:
            response = urllib.request.urlopen(url, timeout=2)
            # If we can access without auth, server might be token-less
            return {
                "token": "",
                "url": f"{protocol}://{host}:{port}",
                "source": "no authentication required"
            }
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # This is expected - server requires auth
                # Try to get token from the login page
                login_url = f"{protocol}://{host}:{port}/login"
                try:
                    response = urllib.request.urlopen(login_url, timeout=2)
                    response.read().decode()  # Read to complete request
                    # Sometimes the login page has hints about the token
                    # But usually this won't work for security reasons
                except Exception:
                    pass
    except Exception:
        pass
    return None

def get_token_from_docker(container_name=None, context=None):
    """Get token from Docker container running Jupyter"""
    try:
        cmd = ["docker"]
        if context:
            cmd.extend(["--context", context])
        
        if container_name:
            # Specific container
            cmd.extend(["exec", container_name, "jupyter", "server", "list"])
        else:
            # Find jupyter containers
            ps_cmd = cmd + ["ps", "--format", "{{.Names}}", "--filter", "ancestor=jupyter/base-notebook"]
            result = subprocess.run(ps_cmd, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                container_name = result.stdout.strip().split('\n')[0]
                cmd.extend(["exec", container_name, "jupyter", "server", "list"])
            else:
                return None
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Parse output for token
            import re
            token_match = re.search(r'token=([a-zA-Z0-9]+)', result.stdout)
            if token_match:
                return {
                    "token": token_match.group(1),
                    "url": "http://localhost:8888",  # Might need adjustment
                    "source": f"docker container: {container_name}"
                }
    except Exception:
        pass
    return None

def main():
    """Try all methods to find a Jupyter token"""
    
    # Priority order of methods
    methods = [
        ("Runtime files", get_token_from_runtime_files),
        ("Docker containers", lambda: get_token_from_docker()),
        ("Config files", get_token_from_config),
        ("Direct API", lambda: get_token_via_api(
            host=os.environ.get("JUPYTER_HOST", "localhost"),
            port=os.environ.get("JUPYTER_PORT", "8888"),
            protocol=os.environ.get("JUPYTER_PROTOCOL", "http")
        )),
    ]
    
    # Check environment first
    if "JUPYTER_TOKEN" in os.environ:
        print(json.dumps({
            "token": os.environ["JUPYTER_TOKEN"],
            "url": f"{os.environ.get('JUPYTER_PROTOCOL', 'http')}://{os.environ.get('JUPYTER_HOST', 'localhost')}:{os.environ.get('JUPYTER_PORT', '8888')}",
            "source": "environment variable"
        }))
        return 0
    
    # Try each method
    for method_name, method_func in methods:
        result = method_func()
        if result:
            result["method"] = method_name
            print(json.dumps(result))
            return 0
    
    # No token found
    print(json.dumps({
        "error": "No Jupyter token found",
        "suggestions": [
            "Start Jupyter with: jupyter lab",
            "Check Docker containers: docker ps",
            "Set JUPYTER_TOKEN environment variable",
            "Use --NotebookApp.token=<token> when starting Jupyter"
        ]
    }), file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main())