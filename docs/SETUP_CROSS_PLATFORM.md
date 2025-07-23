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

# Install/upgrade Docker CE to latest version
./setup --upgrade-docker

# Combined upgrades for older systems
./setup --upgrade-python --upgrade-docker

# Show help and options
./setup --help
```

## Command Line Options

| Option | Description | Use Case |
|--------|-------------|----------|
| `--upgrade-python` | Install/upgrade to Python 3.8+ if needed | Fix Poetry 2.x compatibility on older systems |
| `--upgrade-docker` | Install/upgrade Docker CE to latest version | Modern Docker with Compose V2 support |
| `--skip-mkcert` | Skip mkcert installation | Manual SSL cert management or reverse proxy |
| `--help`, `-h` | Show usage information | Get help and see all options |

## System Requirements

### Supported Operating Systems

| OS/Distribution | Support Level | Notes |
|----------------|---------------|-------|
| **Ubuntu** (18.04+) | ✅ **Full** | Uses deadsnakes PPA for Python, Docker CE repo |
| **Debian** (9+) | ✅ **Full** | Uses deadsnakes PPA for Python, Docker CE repo |
| **Fedora** (30+) | ✅ **Full** | Native packages via dnf, Docker CE repo |
| **CentOS/RHEL** (8+) | ✅ **Full** | EPEL repository + Docker CE (8+ only) |
| **AlmaLinux** (8+) | ✅ **Full** | EPEL repository + Docker CE |
| **Rocky Linux** (8+) | ✅ **Full** | EPEL repository + Docker CE |
| **openSUSE** (15+) | ✅ **Full** | Native packages via zypper, Docker CE repo |
| **SLES** (15+) | ✅ **Full** | Native packages via zypper, Docker CE repo |
| **Arch Linux** | ✅ **Full** | Native packages via pacman |
| **Manjaro** | ✅ **Full** | Native packages via pacman |
| **Alpine Linux** | ✅ **Full** | Native packages via apk |
| **Void Linux** | ✅ **Full** | Native packages via xbps |
| **Gentoo** | ✅ **Full** | Portage packages (emerge) |
| **NixOS** | ⚠️ **Manual** | Requires system configuration |
| **Clear Linux** | ✅ **Full** | Native bundles via swupd |
| **FreeBSD** | ✅ **Full** | Native packages via pkg (Podman) |
| **macOS** (10.14+) | ⚠️ **Manual** | Homebrew + Docker Desktop |
| **Other Linux** | ⚠️ **Partial** | Source compilation fallback |

### Dependencies Installed

The script automatically installs and configures:

1. **Python 3.8+** (required for Poetry 2.x)
2. **Docker CE 17.06+** (container runtime with Compose V2)
3. **Git** (version control)
4. **curl** (HTTP client)
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

## Docker Version Compatibility

### The Docker Version Problem

Many systems have older Docker versions or lack Docker entirely. Modern easy-opal requires:
- **Docker CE 17.06+** (minimum)
- **Docker CE 20.10+** (recommended)
- **Docker Compose V2** (preferred over V1)

### Solution: Automatic Docker Installation

Use the `--upgrade-docker` flag to automatically install Docker CE:

```bash
./setup --upgrade-docker
```

The script will:
1. **Remove** old Docker packages (docker.io, docker-engine)
2. **Add** Docker's official repository
3. **Install** Docker CE with Compose V2 plugin
4. **Configure** Docker service to start automatically
5. **Add** your user to the docker group
6. **Verify** installation and version compatibility

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

### Alpine Linux
```bash
# Native packages
sudo apk update
sudo apk add python3 python3-dev py3-pip
```

### Void Linux
```bash
# Native packages
sudo xbps-install -y python3 python3-devel python3-pip
```

### FreeBSD
```bash
# Native packages
sudo pkg install -y python311 py311-pip
```

### Gentoo
```bash
# Manual configuration required
emerge -av =dev-lang/python-3.11*
eselect python set python3.11
```

### NixOS
```bash
# System configuration required
# Add to /etc/nixos/configuration.nix:
# environment.systemPackages = with pkgs; [ python311 python311Packages.pip ];
sudo nixos-rebuild switch
```

### Clear Linux
```bash
# Native bundles
sudo swupd bundle-add python3-basic python-basic-dev
```

### macOS
```bash
# Via Homebrew
brew install python@3.11
```

## Docker Installation Methods by Distribution

### Ubuntu/Debian
```bash
# Official Docker CE repository
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### Fedora
```bash
# Official Docker CE repository
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### CentOS/RHEL/AlmaLinux/Rocky (8+)
```bash
# Official Docker CE repository (requires RHEL 8+)
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### Arch/Manjaro
```bash
# Community repository
sudo pacman -S docker docker-compose
```

### openSUSE/SLES
```bash
# Official Docker CE repository
sudo zypper addrepo https://download.docker.com/linux/sles/docker-ce.repo
sudo zypper install docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### Alpine Linux
```bash
# Community repository
sudo apk add docker docker-compose
```

### Void Linux
```bash
# Native packages
sudo xbps-install docker docker-compose
```

### Gentoo
```bash
# Portage packages
sudo emerge app-containers/docker app-containers/docker-compose
sudo gpasswd -a $(whoami) docker
```

### NixOS
```bash
# System configuration required
# Add to /etc/nixos/configuration.nix:
# virtualisation.docker.enable = true;
# users.users.username.extraGroups = [ "docker" ];
sudo nixos-rebuild switch
```

### Clear Linux
```bash
# Container bundle
sudo swupd bundle-add containers-basic
sudo systemctl enable docker
sudo systemctl start docker
```

### FreeBSD
```bash
# Docker doesn't run natively - use Podman instead
sudo pkg install podman
# Use 'podman' commands instead of 'docker'
# Optional: alias docker=podman
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
| Alpine Linux | nss-tools + binary | GitHub binary |
| Void Linux | nss + binary | GitHub binary |
| Gentoo | dev-libs/nss + binary | GitHub binary |
| Clear Linux | network-basic + binary | GitHub binary |
| FreeBSD | pkg nss + binary | GitHub binary |
| NixOS | System config required | Manual installation |
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
  • Alpine: sudo apk add git
  • FreeBSD: sudo pkg install git
  • Gentoo: sudo emerge dev-vcs/git
  • Void: sudo xbps-install git
  • NixOS: nix-env -iA nixpkgs.git
  • Clear Linux: sudo swupd bundle-add git
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

#### 2. Docker Installation Fails
**Symptom:**
```
Docker CE requires CentOS/RHEL 8+. Your version: 7.9
```

**Solution:**
```bash
# For older systems, manual installation required
# Visit: https://docs.docker.com/engine/install/
```

#### 3. Docker Permission Denied
**Symptom:**
```bash
docker: permission denied while trying to connect to the Docker daemon socket
```

**Solutions:**
```bash
# Method 1: Log out and back in (refresh group membership)
exit
# Start new terminal session

# Method 2: Check docker group membership
groups $USER

# Method 3: Manually add to docker group (if not done automatically)
sudo usermod -aG docker $USER
```

#### 4. Docker Service Not Running
**Symptom:**
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

**Solutions:**
```bash
# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Check service status
sudo systemctl status docker
```

#### 5. FreeBSD Docker Alternative
**Symptom:**
```
Docker doesn't run natively on FreeBSD
```

**Solutions:**
```bash
# Use Podman (Docker-compatible)
sudo pkg install podman

# Create alias for Docker compatibility
echo "alias docker=podman" >> ~/.bashrc
source ~/.bashrc

# Verify Podman installation
podman --version
```

#### 6. NixOS Docker Configuration
**Symptom:**
```
NixOS requires system configuration for Docker
```

**Solutions:**
```bash
# Add to /etc/nixos/configuration.nix:
sudo nano /etc/nixos/configuration.nix

# Add these lines:
# virtualisation.docker.enable = true;
# users.users.yourusername.extraGroups = [ "docker" ];

# Rebuild system
sudo nixos-rebuild switch

# Log out and back in for group changes
```

#### 7. Gentoo Docker Compilation
**Symptom:**
```
emerge takes a long time to compile Docker
```

**Solutions:**
```bash
# Use binary packages if available
emerge --usepkg app-containers/docker

# Or add FEATURES="parallel-fetch" to make.conf for faster downloads
echo 'FEATURES="parallel-fetch"' >> /etc/portage/make.conf
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
- ✅ **Comprehensive Docker installation** with `--upgrade-docker`
- ✅ **Universal container support** (Docker CE + Podman for FreeBSD)
- ✅ **Multi-tier fallback system** for all installations
- ✅ **Cross-platform alternatives management**
- ✅ **Comprehensive error handling** with specific solutions
- ✅ **System detection and debug information**
- ✅ **Extended distribution support** (openSUSE, Arch, Alpine, Void, etc.)
- ✅ **Additional package managers** (apk, pkg, emerge, xbps, nix, swupd)
- ✅ **FreeBSD and BSD system support** (with Podman)
- ✅ **Container-optimized distributions** (Alpine Linux)
- ✅ **Specialized distributions** (Gentoo, NixOS, Clear Linux)
- ✅ **Official Docker repositories** for modern systems
- ✅ **Native package support** for all major distributions
- ✅ **Retry logic** for network-dependent operations
- ✅ **Manual installation guidance** for each dependency

## Complete System Coverage Matrix

| System | Python | Git/curl | mkcert | **Docker/Containers** | Status |
|--------|--------|----------|--------|-----------------------|---------|
| **Ubuntu** | ✅ deadsnakes PPA | ✅ apt-get | ✅ binary + nss | ✅ **Docker CE** | 🟢 **Full** |
| **Debian** | ✅ deadsnakes PPA | ✅ apt-get | ✅ binary + nss | ✅ **Docker CE** | 🟢 **Full** |
| **Fedora** | ✅ dnf packages | ✅ dnf | ✅ dnf + binary | ✅ **Docker CE** | 🟢 **Full** |
| **CentOS/RHEL 8+** | ✅ EPEL | ✅ dnf/yum | ✅ EPEL + binary | ✅ **Docker CE** | 🟢 **Full** |
| **AlmaLinux** | ✅ EPEL | ✅ dnf | ✅ EPEL + binary | ✅ **Docker CE** | 🟢 **Full** |
| **Rocky Linux** | ✅ EPEL | ✅ dnf | ✅ EPEL + binary | ✅ **Docker CE** | 🟢 **Full** |
| **openSUSE/SLES** | ✅ zypper | ✅ zypper | ✅ binary + nss | ✅ **Docker CE** | 🟢 **Full** |
| **Arch/Manjaro** | ✅ pacman | ✅ pacman | ✅ pacman + binary | ✅ **Docker** | 🟢 **Full** |
| **Alpine Linux** | ✅ apk | ✅ apk | ✅ binary + nss | ✅ **Docker** | 🟢 **Full** |
| **Void Linux** | ✅ xbps | ✅ xbps | ✅ binary + nss | ✅ **Docker** | 🟢 **Full** |
| **Gentoo** | ⚠️ manual | ✅ emerge | ✅ emerge + binary | ✅ **emerge** | 🟡 **Mostly** |
| **Clear Linux** | ✅ swupd bundles | ✅ swupd | ✅ binary + bundles | ✅ **swupd bundles** | 🟢 **Full** |
| **FreeBSD** | ✅ pkg | ✅ pkg | ✅ binary + nss | ✅ **Podman** | 🟢 **Full** |
| **NixOS** | ⚠️ system config | ✅ nix-env | ⚠️ system config | ⚠️ **system config** | 🟡 **Manual** |
| **macOS** | ✅ Homebrew | ✅ Homebrew | ✅ Homebrew | ⚠️ **Docker Desktop** | 🟡 **Mostly** |

### Coverage Summary:
- 🟢 **Full Support**: 12/15 systems (80%)
- 🟡 **Mostly/Manual**: 3/15 systems (20%)
- 🔴 **Limited**: 0/15 systems (0%)

The setup script is now enterprise-ready for deployment across diverse environments while maintaining ease of use for development setups. 