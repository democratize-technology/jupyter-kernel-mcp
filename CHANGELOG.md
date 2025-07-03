# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-01-02

### Added
- Initial release of Jupyter Kernel MCP Server
- Support for persistent kernel sessions across conversations
- Multi-language support (Python, R, Julia, Go, Rust, TypeScript, Bash)
- Notebook creation and management
- Code execution with real-time streaming output
- Search functionality across notebooks
- Comprehensive documentation and examples

### Security
- Simplified authentication requiring explicit JUPYTER_TOKEN configuration
- Removed automatic token retrieval for better security