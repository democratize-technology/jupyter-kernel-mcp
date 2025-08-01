"""Tests for notebook operations."""

import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import uuid

# Import the module to test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jupyter_kernel_mcp


class TestNotebookOperations:
    """Test notebook creation and manipulation functions."""

    @pytest.mark.asyncio
    async def test_create_notebook(self, mock_httpx_client):
        """Test creating a new notebook."""
        # Mock successful notebook creation
        create_response = AsyncMock()
        create_response.raise_for_status = AsyncMock()
        create_response.json = AsyncMock(return_value={
            "name": "test_notebook.ipynb",
            "path": "/test_notebook.ipynb",
            "type": "notebook"
        })
        
        mock_httpx_client.put.return_value = create_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.create_notebook("test_notebook", kernel="python3")
                
                assert result["name"] == "test_notebook.ipynb"
                assert result["kernel"] == "python3"
                assert "created successfully" in result["message"]
                
                # Verify notebook structure
                call_args = mock_httpx_client.put.call_args
                notebook_data = json.loads(call_args[1]["content"])
                assert notebook_data["metadata"]["kernelspec"]["name"] == "python3"
                assert len(notebook_data["cells"]) == 0

    @pytest.mark.asyncio
    async def test_create_notebook_already_exists(self, mock_httpx_client):
        """Test creating notebook that already exists."""
        # Mock conflict response
        error_response = AsyncMock()
        error_response.raise_for_status.side_effect = Exception("File already exists")
        
        mock_httpx_client.put.return_value = error_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.create_notebook("existing_notebook")
                
                assert "error" in result
                assert "already exists" in result["error"]

    @pytest.mark.asyncio
    async def test_add_to_notebook(self, mock_httpx_client, sample_notebook):
        """Test adding code cell to notebook."""
        # Mock get notebook
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        
        # Mock update notebook
        update_response = AsyncMock()
        update_response.raise_for_status = AsyncMock()
        
        mock_httpx_client.get.return_value = get_response
        mock_httpx_client.put.return_value = update_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.add_to_notebook(
                    "test_notebook.ipynb",
                    "print('New cell')"
                )
                
                assert result["cell_count"] == 3  # Original 2 + 1 new
                assert "Cell added successfully" in result["message"]
                
                # Verify the new cell was added
                update_call = mock_httpx_client.put.call_args
                updated_content = json.loads(update_call[1]["content"])
                new_cell = updated_content["content"]["cells"][-1]
                assert new_cell["cell_type"] == "code"
                assert new_cell["source"] == "print('New cell')"

    @pytest.mark.asyncio
    async def test_add_to_notebook_not_found(self, mock_httpx_client):
        """Test adding to non-existent notebook."""
        mock_httpx_client.get.side_effect = Exception("404 Not Found")
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.add_to_notebook(
                    "nonexistent.ipynb",
                    "print('test')"
                )
                
                assert "error" in result
                assert "404" in result["error"]

    @pytest.mark.asyncio
    async def test_read_notebook(self, mock_httpx_client, sample_notebook):
        """Test reading notebook contents."""
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        mock_httpx_client.get.return_value = get_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.read_notebook("test_notebook.ipynb")
                
                assert result["name"] == "test_notebook.ipynb"
                assert result["kernel"] == "python3"
                assert result["cell_count"] == 2
                assert len(result["cells"]) == 2
                assert result["cells"][0]["type"] == "code"
                assert result["cells"][1]["type"] == "markdown"

    @pytest.mark.asyncio
    async def test_notebook_combined_function(self, mock_httpx_client, sample_notebook):
        """Test the combined notebook function."""
        # Test read operation
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        mock_httpx_client.get.return_value = get_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.notebook(
                    name="test_notebook.ipynb",
                    action="read"
                )
                
                assert result["name"] == "test_notebook.ipynb"
                assert result["cell_count"] == 2

    @pytest.mark.asyncio
    async def test_notebook_create_action(self, mock_httpx_client):
        """Test notebook function with create action."""
        create_response = AsyncMock()
        create_response.raise_for_status = AsyncMock()
        create_response.json = AsyncMock(return_value={"name": "new.ipynb"})
        mock_httpx_client.put.return_value = create_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.notebook(
                    name="new_notebook",
                    action="create",
                    kernel="python3"
                )
                
                assert "created successfully" in result["message"]

    @pytest.mark.asyncio
    async def test_list_notebooks(self, mock_httpx_client):
        """Test listing notebooks."""
        notebooks_list = {
            "content": [
                {"name": "notebook1.ipynb", "type": "notebook", "path": "/notebook1.ipynb"},
                {"name": "notebook2.ipynb", "type": "notebook", "path": "/notebook2.ipynb"},
                {"name": "data.csv", "type": "file", "path": "/data.csv"},
            ]
        }
        
        list_response = AsyncMock()
        list_response.json = AsyncMock(return_value=notebooks_list)
        mock_httpx_client.get.return_value = list_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.list_notebooks()
                
                assert result["count"] == 2  # Only notebooks, not files
                assert len(result["notebooks"]) == 2
                assert all(nb["name"].endswith(".ipynb") for nb in result["notebooks"])

    @pytest.mark.asyncio
    async def test_execute_notebook_cell(self, mock_httpx_client, sample_notebook):
        """Test executing a specific notebook cell."""
        # Mock get notebook
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        mock_httpx_client.get.return_value = get_response
        
        # Mock code execution
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "Hello", "error": False}
            
            with patch("httpx.AsyncClient", return_value=mock_httpx_client):
                with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                    result = await jupyter_kernel_mcp.execute_notebook_cell(
                        "test_notebook.ipynb",
                        cell=0
                    )
                    
                    assert result["output"] == "Hello"
                    assert result["cell_index"] == 0
                    mock_execute.assert_called_once_with("print('Hello')", "python3")

    @pytest.mark.asyncio
    async def test_execute_notebook_cell_invalid_index(self, mock_httpx_client, sample_notebook):
        """Test executing cell with invalid index."""
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        mock_httpx_client.get.return_value = get_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.execute_notebook_cell(
                    "test_notebook.ipynb",
                    cell=10  # Out of bounds
                )
                
                assert "error" in result
                assert "Invalid cell index" in result["error"]

    @pytest.mark.asyncio
    async def test_add_markdown_to_notebook(self, mock_httpx_client, sample_notebook):
        """Test adding markdown cell to notebook."""
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        
        update_response = AsyncMock()
        update_response.raise_for_status = AsyncMock()
        
        mock_httpx_client.get.return_value = get_response
        mock_httpx_client.put.return_value = update_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.add_markdown_to_notebook(
                    "test_notebook.ipynb",
                    "## New Section\nThis is markdown content."
                )
                
                assert result["cell_count"] == 3
                assert "Markdown cell added" in result["message"]
                
                # Verify markdown cell
                update_call = mock_httpx_client.put.call_args
                updated_content = json.loads(update_call[1]["content"])
                new_cell = updated_content["content"]["cells"][-1]
                assert new_cell["cell_type"] == "markdown"

    @pytest.mark.asyncio
    async def test_delete_notebook(self, mock_httpx_client):
        """Test deleting a notebook."""
        delete_response = AsyncMock()
        delete_response.raise_for_status = AsyncMock()
        mock_httpx_client.delete.return_value = delete_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.delete_notebook("test_notebook.ipynb")
                
                assert result["message"] == "Notebook 'test_notebook.ipynb' deleted successfully"
                mock_httpx_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_rename_notebook(self, mock_httpx_client):
        """Test renaming a notebook."""
        rename_response = AsyncMock()
        rename_response.raise_for_status = AsyncMock()
        rename_response.json = AsyncMock(return_value={"name": "new_name.ipynb"})
        mock_httpx_client.patch.return_value = rename_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.rename_notebook(
                    "old_name.ipynb",
                    "new_name.ipynb"
                )
                
                assert "renamed successfully" in result["message"]
                assert result["new_name"] == "new_name.ipynb"

    @pytest.mark.asyncio
    async def test_search_notebooks(self, mock_httpx_client, sample_notebook):
        """Test searching notebooks."""
        # Mock list notebooks
        notebooks_list = {
            "content": [
                {"name": "data_analysis.ipynb", "type": "notebook"},
                {"name": "machine_learning.ipynb", "type": "notebook"},
                {"name": "test.ipynb", "type": "notebook"},
            ]
        }
        
        list_response = AsyncMock()
        list_response.json = AsyncMock(return_value=notebooks_list)
        
        # Mock getting each notebook for search
        ml_notebook = sample_notebook.copy()
        ml_notebook["content"]["cells"][0]["source"] = "import tensorflow"
        
        get_responses = [
            AsyncMock(json=AsyncMock(return_value=sample_notebook)),
            AsyncMock(json=AsyncMock(return_value=ml_notebook)),
            AsyncMock(json=AsyncMock(return_value=sample_notebook)),
        ]
        
        mock_httpx_client.get.side_effect = [list_response] + get_responses
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.search_notebooks("tensorflow")
                
                assert result["total_matches"] == 1
                assert len(result["results"]) == 1
                assert result["results"][0]["notebook"] == "machine_learning.ipynb"

    @pytest.mark.asyncio
    async def test_clear_notebook_outputs(self, mock_httpx_client, sample_notebook):
        """Test clearing notebook outputs."""
        # Add outputs to sample notebook
        notebook_with_outputs = sample_notebook.copy()
        notebook_with_outputs["content"]["cells"][0]["outputs"] = [
            {"output_type": "stream", "text": "Output text"}
        ]
        
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=notebook_with_outputs)
        
        update_response = AsyncMock()
        update_response.raise_for_status = AsyncMock()
        
        mock_httpx_client.get.return_value = get_response
        mock_httpx_client.put.return_value = update_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.clear_notebook_outputs("test_notebook.ipynb")
                
                assert result["cleared_count"] == 1
                assert "outputs cleared" in result["message"]
                
                # Verify outputs were cleared
                update_call = mock_httpx_client.put.call_args
                updated_content = json.loads(update_call[1]["content"])
                for cell in updated_content["content"]["cells"]:
                    if cell["cell_type"] == "code":
                        assert cell["outputs"] == []
                        assert cell["execution_count"] is None

    @pytest.mark.asyncio
    async def test_run_all_cells(self, mock_httpx_client, sample_notebook):
        """Test running all cells in a notebook."""
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        
        update_response = AsyncMock()
        update_response.raise_for_status = AsyncMock()
        
        mock_httpx_client.get.return_value = get_response
        mock_httpx_client.put.return_value = update_response
        
        # Mock code execution
        with patch("jupyter_kernel_mcp._execute_code") as mock_execute:
            mock_execute.return_value = {"output": "Cell output", "error": False}
            
            with patch("httpx.AsyncClient", return_value=mock_httpx_client):
                with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                    result = await jupyter_kernel_mcp.run_all_cells("test_notebook.ipynb")
                    
                    assert result["cells_executed"] == 1  # Only code cells
                    assert result["execution_time"] > 0
                    assert len(result["results"]) == 1

    @pytest.mark.asyncio
    async def test_copy_notebook(self, mock_httpx_client, sample_notebook):
        """Test copying a notebook."""
        # Mock get source
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=sample_notebook)
        
        # Mock create destination
        create_response = AsyncMock()
        create_response.raise_for_status = AsyncMock()
        create_response.json = AsyncMock(return_value={"name": "copy.ipynb"})
        
        mock_httpx_client.get.return_value = get_response
        mock_httpx_client.put.return_value = create_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.copy_notebook(
                    "original.ipynb",
                    "copy.ipynb"
                )
                
                assert "copied successfully" in result["message"]
                assert result["destination"] == "copy.ipynb"

    @pytest.mark.asyncio
    async def test_get_notebook_stats(self, mock_httpx_client, sample_notebook):
        """Test getting notebook statistics."""
        # Enhance sample notebook with more cells
        enhanced_notebook = sample_notebook.copy()
        enhanced_notebook["content"]["cells"].extend([
            {"cell_type": "code", "source": "x = 42\ny = 100", "outputs": []},
            {"cell_type": "markdown", "source": "## Analysis\nMore text here"},
            {"cell_type": "code", "source": "# Empty cell", "outputs": []},
        ])
        
        get_response = AsyncMock()
        get_response.json = AsyncMock(return_value=enhanced_notebook)
        mock_httpx_client.get.return_value = get_response
        
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            with patch.object(jupyter_kernel_mcp, 'JUPYTER_TOKEN', 'test-token'):
                result = await jupyter_kernel_mcp.get_notebook_stats("test_notebook.ipynb")
                
                assert result["total_cells"] == 5
                assert result["code_cells"] == 3
                assert result["markdown_cells"] == 2
                assert result["total_lines"] == 6  # Count all lines
                assert result["has_outputs"] is False