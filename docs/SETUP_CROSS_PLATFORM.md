# Cross-Platform Setup Guide

This document provides comprehensive information about the `./setup` script and its cross-platform compatibility features.

## Overview

The `./setup` script is designed to automatically install and configure all dependencies required for easy-opal across different operating systems and Linux distributions. It features robust error handling, multiple fallback methods, and comprehensive platform support.

## Quick Start

```bash
# Standard installation
./setup

# Skip certificate tools (for reverse proxy setups)
./setup --skip-mkcert

# Install/upgrade Python 3.8+ if needed (fixes Poetry compatibility issues)
./setup --upgrade-python

# Show help and options
./setup --help
```

## Command Line Options

| Option | Description | Use Case |
|--------|-------------|----------|
| `--upgrade-python` | Install/upgrade to Python 3.8+ if needed | Fix Poetry 2.x compatibility on older systems |
| `--skip-mkcert` | Skip mkcert installation | Manual SSL cert management or reverse proxy |
| `--help`, `-h` | Show usage information | Get help and see all options |

## System Requirements

### Supported Operating Systems

| OS/Distribution | Support Level | Notes |
|----------------|---------------|-------|
| **Ubuntu** (18.04+) | ✅ **Full** | Uses deadsnakes PPA for Python |
| **Debian** (9+) | ✅ **Full** | Uses deadsnakes PPA for Python |
| **Fedora** (30+) | ✅ **Full** | Native packages via dnf |
| **CentOS/RHEL** (7+) | ✅ **Full** | EPEL repository + alternatives |
| **AlmaLinux** (8+) | ✅ **Full** | EPEL repository + alternatives |
| **Rocky Linux** (8+) | ✅ **Full** | EPEL repository + alternatives |
| **openSUSE** (15+) | ✅ **Full** | Native packages via zypper |
| **SLES** (15+) | ✅ **Full** | Native packages via zypper |
| **Arch Linux** | ✅ **Full** | Native packages via pacman |
| **Manjaro** | ✅ **Full** | Native packages via pacman |
| **macOS** (10.14+) | ✅ **Full** | Homebrew required |
| **Other Linux** | ⚠️ **Partial** | Source compilation fallback |

### Dependencies Installed

The script automatically installs and configures:

1. **Python 3.8+** (required for Poetry 2.x)
2. **Git** (version control)
3. **curl** (HTTP client)
4. **Docker** (detection only - manual installation required)
5. **mkcert** (SSL certificate generation)
6. **Poetry** (Python dependency management)

## Python Version Compatibility

### The Poetry 2.x Problem

Many systems (especially CentOS/RHEL/AlmaLinux) ship with Python 3.6 or 3.7, but Poetry 2.x requires Python 3.8+. This causes installation failures like:

```
ERROR: Could not find a version that satisfies the requirement poetry==2.1.3
```

### Solution: Automatic Python Upgrade

Use the `--upgrade-python` flag to automatically install a compatible Python version:

```bash
./setup --upgrade-python
```

The script will:
1. **Detect** your current Python version
2. **Install** Python 3.11 using the best method for your system
3. **Configure** system alternatives to use the new version
4. **Verify** that `python3 --version` shows the upgraded version
5. **Install** Poetry successfully with the compatible Python

## Installation Methods by Distribution

### Ubuntu/Debian
```bash
# Python 3.11 via deadsnakes PPA
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get install python3.11 python3.11-pip python3.11-dev

# Configure alternatives
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 100
```

### CentOS/RHEL/AlmaLinux/Rocky
```bash
# Method 1: EPEL repository (preferred)
sudo dnf install epel-release
sudo dnf install python3.11 python3.11-pip python3.11-devel

# Method 2: Software Collections (older versions)
sudo yum install centos-release-scl
sudo yum install rh-python38

# Method 3: Source compilation (fallback)
# Automatic download and compilation from python.org
```

### Fedora
```bash
# Native packages
sudo dnf install python3.11 python3.11-pip python3.11-devel
```

### openSUSE/SLES
```bash
# Native packages
sudo zypper install python311 python311-pip python311-devel
```

### Arch/Manjaro
```bash
# Usually has latest Python by default
sudo pacman -S python python-pip

# Fallback to specific version if needed
sudo pacman -S python311 python311-pip
```

### macOS
```bash
# Via Homebrew
brew install python@3.11
```

## Multi-Tier Fallback System

The setup script uses a sophisticated fallback system to ensure installation success:

### Tier 1: Package Manager
- Uses distribution's native package manager
- Fastest and most reliable method
- Handles dependencies automatically

### Tier 2: Alternative Repositories
- EPEL for RHEL-based systems
- deadsnakes PPA for Ubuntu/Debian
- Software Collections for older CentOS

### Tier 3: Binary Downloads
- Direct download from GitHub releases
- Works when packages aren't available
- Automatically detects architecture (x86_64, arm64, etc.)

### Tier 4: Source Compilation
- Ultimate fallback for any system
- Downloads and compiles from python.org
- Installs to `/usr/local` with proper alternatives

## System Alternative Management

The script uses a cross-platform approach to manage Python versions:

### Method 1: update-alternatives (Most Systems)
```bash
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 100
sudo update-alternatives --set python3 /usr/bin/python3.11
```

### Method 2: alternatives (Legacy RHEL/CentOS)
```bash
sudo alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 100
sudo alternatives --set python3 /usr/bin/python3.11
```

### Method 3: Direct Symlinks (Fallback)
```bash
sudo ln -sf /usr/bin/python3.11 /usr/bin/python3
```

### Method 4: PATH Management (Protected Systems)
```bash
export PATH="/usr/local/bin:$PATH"
```

## SSL Certificates (mkcert)

### Installation Methods

The script installs mkcert using multiple approaches:

| Distribution | Method | Fallback |
|-------------|--------|----------|
| Ubuntu/Debian | libnss3-tools + binary | GitHub binary |
| Fedora | dnf package | nss-tools + binary |
| CentOS/RHEL | EPEL package | nss-tools + binary |
| openSUSE/SLES | mozilla-nss-tools + binary | GitHub binary |
| Arch/Manjaro | pacman package | nss + binary |
| macOS | Homebrew package | N/A |
| Others | GitHub binary | Manual installation |

### Skip mkcert Installation

If you prefer to manage SSL certificates manually:

```bash
./setup --skip-mkcert
```

This is useful for:
- **Reverse proxy** setups (nginx, Cloudflare, etc.)
- **Manual certificate** management
- **Let's Encrypt** usage
- **Corporate PKI** environments

## Error Handling and Recovery

### Automatic Retry Logic

The script includes retry mechanisms for:
- **Poetry installation** (3 attempts with cache clearing)
- **Dependency installation** (2 attempts)
- **Network downloads** (with timeout handling)

### Comprehensive Error Messages

When failures occur, you get specific guidance:

```bash
❌ [ERROR] Failed to install project dependencies

📋 This might be due to:
1. Python version incompatibility (requires Python 3.8+)
   Solution: Run './setup --upgrade-python'

2. Network connectivity issues
   Solution: Check internet connection and retry

3. Poetry cache corruption
   Solution: Run 'poetry cache clear --all .' and retry

4. Missing system dependencies
   Solution: Install build tools for your distribution
```

### Manual Installation Guidance

For each dependency, the script provides distribution-specific commands:

```bash
Could not install git automatically. Please install it manually.
Common commands:
  • Ubuntu/Debian: sudo apt install git
  • Fedora: sudo dnf install git
  • CentOS/RHEL: sudo yum install git
  • openSUSE: sudo zypper install git
  • Arch: sudo pacman -S git
```

## System Detection

The script automatically detects your system:

```bash
🖥️  System: CentOS Linux 7.9.2009
📦 Package manager: yum
🔍 Using python3 from: /usr/bin/python3
🐍 Python version: 3.6
⚠️  Python 3.6 detected. Poetry 2.x requires Python 3.8+
```

## Troubleshooting

### Common Issues

#### 1. Poetry Installation Fails
**Symptom:**
```
ERROR: Could not find a version that satisfies the requirement poetry==2.1.3
```

**Solution:**
```bash
./setup --upgrade-python
```

#### 2. Python Upgrade Not Taking Effect
**Symptom:**
```bash
python3 --version  # Still shows old version
```

**Solutions:**
```bash
# Method 1: Open new terminal
exit
# Start new terminal session

# Method 2: Refresh shell
source ~/.bashrc
hash -r

# Method 3: Check alternatives
sudo update-alternatives --config python3
```

#### 3. Permission Errors
**Symptom:**
```
Permission denied when creating symlinks
```

**Solution:**
```bash
# The script uses sudo automatically, but if issues persist:
sudo ./setup --upgrade-python
```

#### 4. Network/Firewall Issues
**Symptom:**
```
Failed to fetch from remote repository
```

**Solutions:**
- Check internet connectivity
- Configure proxy settings if behind corporate firewall
- Use `--skip-mkcert` if certificate downloads fail

### Debug Information

Run the script to see detailed debug output:
- System detection results
- Python path being used
- Package manager commands executed
- Installation attempts and fallbacks

### Getting Help

1. **Check the output**: The script provides detailed progress information
2. **Review error messages**: Each error includes specific solutions
3. **Try again**: Many issues are transient (network, etc.)
4. **Use flags**: `--upgrade-python` solves most Python-related issues
5. **Manual installation**: Follow the provided distribution-specific commands

## Advanced Usage

### Corporate/Restricted Environments

```bash
# Skip tools that require internet access
./setup --skip-mkcert

# Then manually configure:
# - Provide your own SSL certificates
# - Use reverse proxy mode
# - Configure corporate PKI
```

### Development vs Production

```bash
# Development (full setup with self-signed certs)
./setup

# Production (with reverse proxy)
./setup --skip-mkcert
# Then configure nginx/Apache/Cloudflare SSL termination
```

### CI/CD Environments

```bash
# Non-interactive setup with Python upgrade
./setup --upgrade-python --skip-mkcert
```

## Architecture Support

The script automatically detects and handles different architectures:

- **x86_64** (Intel/AMD 64-bit)
- **aarch64** (ARM 64-bit)
- **armv7l** (ARM 32-bit)
- **i386/i686** (Intel 32-bit)

Binary downloads are automatically selected for your architecture.

## Contributing

When adding support for new distributions:

1. Add detection logic to `detect_system()`
2. Add package manager detection
3. Add Python installation method
4. Add dependency installation commands
5. Test on the target distribution
6. Update this documentation

## Changelog

### Recent Improvements

- ✅ **Enhanced Python version handling** with `--upgrade-python`
- ✅ **Multi-tier fallback system** for all installations
- ✅ **Cross-platform alternatives management**
- ✅ **Comprehensive error handling** with specific solutions
- ✅ **System detection and debug information**
- ✅ **Extended distribution support** (openSUSE, Arch, etc.)
- ✅ **Retry logic** for network-dependent operations
- ✅ **Manual installation guidance** for each dependency

The setup script is now enterprise-ready for deployment across diverse environments while maintaining ease of use for development setups. 