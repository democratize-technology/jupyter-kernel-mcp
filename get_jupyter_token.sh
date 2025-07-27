#!/bin/bash
# Helper script to retrieve Jupyter token from various sources
# This addresses the authentication friction point

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🔍 Jupyter Token Finder${NC}"
echo "=========================="

# Function to extract token from jupyter server list output
extract_token() {
    echo "$1" | grep -o 'token=[^ ]*' | cut -d= -f2 | head -1
}

# Check for command line argument
if [ "$1" = "--docker" ] && [ -n "$2" ]; then
    echo -e "\n${GREEN}Checking Docker container: $2${NC}"
    TOKEN=$(docker exec "$2" jupyter server list 2>/dev/null | grep -o 'token=[^ ]*' | cut -d= -f2 | head -1)
    if [ -n "$TOKEN" ]; then
        echo -e "${GREEN}✅ Found token in Docker container${NC}"
        echo -e "\nToken: ${GREEN}$TOKEN${NC}"
        echo -e "\nAdd to your .env file:"
        echo "JUPYTER_TOKEN=$TOKEN"
        exit 0
    else
        echo -e "${RED}❌ No token found in Docker container${NC}"
    fi
fi

# Check for remote docker context
if [ "$1" = "--docker-context" ] && [ -n "$2" ] && [ -n "$3" ]; then
    echo -e "\n${GREEN}Checking Docker context: $2, container: $3${NC}"
    TOKEN=$(docker --context "$2" exec "$3" jupyter server list 2>/dev/null | grep -o 'token=[^ ]*' | cut -d= -f2 | head -1)
    if [ -n "$TOKEN" ]; then
        echo -e "${GREEN}✅ Found token in Docker container${NC}"
        echo -e "\nToken: ${GREEN}$TOKEN${NC}"
        echo -e "\nAdd to your .env file:"
        echo "JUPYTER_TOKEN=$TOKEN"
        exit 0
    else
        echo -e "${RED}❌ No token found in Docker container${NC}"
    fi
fi

# Method 1: Check local jupyter server list
echo -e "\n${GREEN}Method 1: Checking local Jupyter servers...${NC}"
if command -v jupyter &> /dev/null; then
    JUPYTER_OUTPUT=$(jupyter server list 2>/dev/null || true)
    if [ -n "$JUPYTER_OUTPUT" ]; then
        TOKEN=$(extract_token "$JUPYTER_OUTPUT")
        if [ -n "$TOKEN" ]; then
            echo -e "${GREEN}✅ Found local Jupyter server${NC}"
            echo "$JUPYTER_OUTPUT"
            echo -e "\nToken: ${GREEN}$TOKEN${NC}"
            echo -e "\nAdd to your .env file:"
            echo "JUPYTER_TOKEN=$TOKEN"
            exit 0
        else
            echo "Local Jupyter server found but no token detected"
        fi
    else
        echo "No local Jupyter servers running"
    fi
else
    echo "Jupyter not found in PATH"
fi

# Method 2: Check common docker containers
echo -e "\n${GREEN}Method 2: Checking Docker containers...${NC}"
if command -v docker &> /dev/null; then
    # Get list of running containers with jupyter in name
    CONTAINERS=$(docker ps --format "{{.Names}}" | grep -i jupyter || true)
    
    if [ -n "$CONTAINERS" ]; then
        for container in $CONTAINERS; do
            echo "Checking container: $container"
            TOKEN=$(docker exec "$container" jupyter server list 2>/dev/null | grep -o 'token=[^ ]*' | cut -d= -f2 | head -1 || true)
            if [ -n "$TOKEN" ]; then
                echo -e "${GREEN}✅ Found token in container: $container${NC}"
                echo -e "\nToken: ${GREEN}$TOKEN${NC}"
                echo -e "\nAdd to your .env file:"
                echo "JUPYTER_TOKEN=$TOKEN"
                exit 0
            fi
        done
    else
        echo "No Jupyter containers found"
    fi
else
    echo "Docker not available"
fi

# Method 3: Check JupyterLab config directory
echo -e "\n${GREEN}Method 3: Checking JupyterLab runtime files...${NC}"
RUNTIME_DIR="$HOME/.local/share/jupyter/runtime"
if [ -d "$RUNTIME_DIR" ]; then
    # Find most recent nbserver json file
    LATEST_FILE=$(ls -t "$RUNTIME_DIR"/nbserver-*.json 2>/dev/null | head -1)
    if [ -f "$LATEST_FILE" ]; then
        TOKEN=$(grep -o '"token": "[^"]*"' "$LATEST_FILE" | cut -d'"' -f4)
        if [ -n "$TOKEN" ]; then
            echo -e "${GREEN}✅ Found token in runtime file${NC}"
            echo "File: $LATEST_FILE"
            echo -e "\nToken: ${GREEN}$TOKEN${NC}"
            echo -e "\nAdd to your .env file:"
            echo "JUPYTER_TOKEN=$TOKEN"
            exit 0
        fi
    else
        echo "No runtime files found"
    fi
else
    echo "Runtime directory not found"
fi

# Method 4: Environment variable
echo -e "\n${GREEN}Method 4: Checking environment variable...${NC}"
if [ -n "$JUPYTER_TOKEN" ]; then
    echo -e "${GREEN}✅ Found JUPYTER_TOKEN in environment${NC}"
    echo -e "\nToken: ${GREEN}$JUPYTER_TOKEN${NC}"
    exit 0
else
    echo "JUPYTER_TOKEN not set in environment"
fi

# No token found
echo -e "\n${RED}❌ No Jupyter token found${NC}"
echo -e "\n${YELLOW}Manual steps to get a token:${NC}"
echo "1. Start Jupyter with a token:"
echo "   jupyter lab --NotebookApp.token=mytoken"
echo ""
echo "2. Or check the Jupyter startup logs for the token URL"
echo ""
echo "3. For Docker containers, use:"
echo "   $0 --docker <container_name>"
echo ""
echo "4. For remote Docker contexts:"
echo "   $0 --docker-context <context> <container_name>"
echo ""
echo "Example MCP config that auto-retrieves token:"
cat << 'EOF'
"jupyter-kernel": {
  "command": "bash",
  "args": [
    "-c",
    "export JUPYTER_TOKEN=$($0 --docker jupyter 2>/dev/null | grep 'Token:' | awk '{print $2}') && /path/to/run_server.sh"
  ]
}
EOF

exit 1