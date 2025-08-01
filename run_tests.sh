#!/bin/bash
# Run tests with coverage reporting

echo "🧪 Running jupyter-kernel-mcp test suite..."
echo "========================================="

# Install test dependencies if not already installed
echo "📦 Installing test dependencies..."
uv pip install -e ".[test]" --quiet

# Run tests with coverage
echo ""
echo "🏃 Running tests with coverage..."
uv run pytest -v --cov=jupyter_kernel_mcp --cov-report=term-missing --cov-report=html --cov-report=json

# Check coverage threshold
echo ""
echo "📊 Coverage Summary:"
uv run python -c "
import json
with open('coverage.json', 'r') as f:
    data = json.load(f)
    total = data['totals']['percent_covered']
    print(f'Total Coverage: {total:.1f}%')
    if total >= 100:
        print('✅ Achieved 100% coverage!')
    elif total >= 90:
        print('🎯 Good coverage, but not quite 100%')
    else:
        print('⚠️  Coverage below 90%, needs improvement')
"

echo ""
echo "📄 Detailed coverage report available at: htmlcov/index.html"
echo "========================================="