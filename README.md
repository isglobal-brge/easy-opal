# easy-opal

A command-line tool to easily set up and manage OBiBa Opal environments using Docker and NGINX. This tool provides a simple wizard for initial setup, as well as commands for managing the stack, Rock profiles, and configuration.

## Prerequisites

Before you begin, ensure you have the following installed on your system:
- **Python 3.8+**
- **Docker**: Required to run the Opal and Rock containers.
- **Git**: Required for the `update` command.
- **mkcert**: Required for creating locally-trusted SSL certificates for development.

The setup script will automatically install and configure these dependencies across all major platforms.

> 📖 **For detailed platform support, troubleshooting, and advanced options, see:** [**Cross-Platform Setup Guide**](./docs/SETUP_CROSS_PLATFORM.md)

## Recommended Setup

For the smoothest experience, we recommend:

### **Operating System:**

**✅ Ubuntu 20.04+ / 22.04+** (Highly Recommended)
- Ships with Docker Compose V2 by default
- Excellent Docker integration
- All dependencies (curl, git, etc.) pre-installed
- Best networking compatibility

> **Note:** While easy-opal works on other Linux distributions and macOS, Ubuntu provides the most reliable experience with fewer setup issues.

### **Administrative Privileges:**

- **`sudo` access is required** for:
  - Installing system dependencies (Docker, git, curl, mkcert)
  - Docker daemon operations (if not in docker group)
  - SSL certificate installation (mkcert -install)

- **Add your user to the docker group** (recommended):
  ```bash
  sudo usermod -aG docker $USER
  # Log out and back in for changes to take effect
  ```

### **Docker Requirements:**
- **Docker Engine 17.06+** (20.10+ recommended)
- **Docker Compose V2** preferred (falls back to V1 automatically)
- **At least 4GB RAM** available for containers
- **At least 20GB free disk space** for images, data, and build cache

> **💡 Tip:** If you encounter issues, check our comprehensive [Troubleshooting Guide](./docs/TROUBLESHOOTING.md) for distribution-specific solutions.

## Installation

The installation process is streamlined using [uv](https://docs.astral.sh/uv/) for fast and robust dependency and environment management.

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/isglobal-brge/easy-opal.git
    cd easy-opal
    ```

2.  **Run the main setup script**:
    ```bash
    chmod +x setup
    ./setup
    ```
    
    **For systems with older Python versions (CentOS/RHEL/AlmaLinux):**
    ```bash
    ./setup --upgrade-python
    ```
    
    **For systems without Docker or with older Docker versions:**
    ```bash
    ./setup --upgrade-docker
    ```
    
    **For complete system upgrade (recommended for older systems):**
    ```bash
    ./setup --upgrade-python --upgrade-docker
    ```
    
    The setup script automatically handles:
    - ✅ **System dependency detection** (Python 3.8+, Docker CE 17.06+, Git, curl)
    - ✅ **Cross-platform installation** (Ubuntu, CentOS, Fedora, Arch, Alpine, Gentoo, FreeBSD, macOS, etc.)
    - ✅ **Python version upgrades** (ensures Python 3.8+ compatibility)
    - ✅ **Docker CE installation** (with Compose V2 support)
    - ✅ **uv installation** with virtual environment setup
    - ✅ **SSL certificates** via mkcert (skip with `--skip-mkcert`)
    - ✅ **Multi-tier fallback system** for maximum compatibility
    
    > 📋 **Setup Options:**
    > - `./setup` - Standard installation
    > - `./setup --upgrade-python` - Install Python 3.8+ if needed
    > - `./setup --upgrade-docker` - Install Docker CE with Compose V2
    > - `./setup --skip-mkcert` - Skip certificate tools (for reverse proxy setups)
    > - `./setup --upgrade-python --upgrade-docker` - Complete system upgrade

## Usage

All commands are run from the project root directory using the `./easy-opal` wrapper script.

### `setup`

The main command to configure or re-configure a stack. Running it without flags starts an interactive wizard.

-   `./easy-opal setup`

**Flags:**

-   `--stack-name TEXT`: The name of the Docker stack (e.g., `my-opal`).
-   `--host TEXT`: A hostname or IP for Opal. Can be used multiple times.
-   `--port INTEGER`: The external HTTPS port for Opal.
-   `--http-port INTEGER`: The local HTTP port for 'reverse-proxy' strategy.
-   `--password TEXT`: The Opal administrator password.
-   `--ssl-strategy [self-signed|letsencrypt|manual|reverse-proxy]`: The SSL strategy to use. See [SSL Configuration Guide](./docs/SSL_CONFIGURATION.md) for details.
-   `--ssl-cert-path TEXT`: Path to your certificate file (for 'manual' strategy).
-   `--ssl-key-path TEXT`: Path to your private key file (for 'manual' strategy).
-   `--ssl-email TEXT`: Email for Let's Encrypt renewal notices.
-   `--database TEXT`: Add a database instance. Format: `type:name:port:user:password` (e.g., `postgres:maindb:5432:opal:pass123`). Can be used multiple times to add multiple database instances, including multiple instances of the same type.
-   `--yes`: Bypasses all interactive prompts. Essential for scripting.
-   `--reset-containers`: Non-interactively stops and removes Docker containers.
-   `--reset-volumes`: Non-interactively deletes Docker volumes (all application data).
-   `--reset-configs`: Non-interactively deletes configuration files.
-   `--reset-certs`: Non-interactively deletes SSL certificates.
-   `--reset-secrets`: Non-interactively deletes the `.env` file.

**Example (Non-Interactive):**

```bash
# Basic setup
./easy-opal setup \
  --stack-name new-stack \
  --host localhost \
  --port 8443 \
  --password "newpass" \
  --ssl-strategy "self-signed" \
  --yes \
  --reset-containers

# Setup with multiple database instances
./easy-opal setup \
  --stack-name data-stack \
  --host localhost \
  --port 8443 \
  --password "mypass" \
  --ssl-strategy "self-signed" \
  --database postgres:analytics:5432:opal:pgpass1 \
  --database postgres:warehouse:5433:admin:pgpass2 \
  --database mysql:app:3306:opal:mysqlpass \
  --yes
```

---

### `reset`

Performs a factory reset of the environment. Running it without flags starts an interactive wizard.

-   `./easy-opal reset`

**Flags:**

-   `--containers`: Stop and remove Docker containers and networks.
-   `--volumes`: Delete Docker volumes (highly destructive).
-   `--configs`: Delete configuration files.
-   `--certs`: Delete SSL certificates.
-   `--secrets`: Delete the `.env` file.
-   `--all`: Selects all of the above options.
-   `--yes`: Bypasses the final confirmation prompt.

**Example (Non-Interactive):**

```bash
# Non-interactively delete everything
./easy-opal reset --all --yes
```

---

### Stack Lifecycle

-   `./easy-opal up`: Ensures the stack is running. If it's already running, it will be stopped and started again to apply any changes.
-   `./easy-opal down`: Stops the stack's containers.
-   `./easy-opal status`: Shows the status of the running containers.

---

### `diagnose`

Run comprehensive health diagnostics on your easy-opal installation. This command performs thorough testing of infrastructure, network connectivity, external access, security configurations, and service endpoints.

-   `./easy-opal diagnose`: Full diagnostic report with detailed results and troubleshooting guidance.
-   `./easy-opal diagnose --quiet`: Summary-only output, perfect for CI/CD systems and automated monitoring.
-   `./easy-opal diagnose --verbose`: Detailed output with additional debugging information.
-   `./easy-opal diagnose --no-auto-start`: Prevent interactive prompts to start the stack (for automated scenarios).

**What it tests:**

-   **🐳 Infrastructure**: Docker Compose configuration and container status
-   **🔗 Network Connectivity**: Inter-container communication (Opal↔MongoDB, Nginx↔Opal, Rock connections) with 2-minute retry logic for startup delays
-   **🌐 External Access**: Port accessibility from host system with 2-minute retry logic for service startup
-   **🔒 Security & Certificates**: SSL certificate validation with 2-minute retry logic for SSL service startup
-   **💾 Service Health**: HTTP/HTTPS endpoint responses with 2-minute retry logic for web service initialization
-   **🛡️ Firewall & Protection**: Firewall rules (UFW, iptables), WAF detection (Cloudflare, AWS, etc.), rate limiting, and Docker network integration

**Output formats:**

```bash
# Full diagnostic with troubleshooting guidance
./easy-opal diagnose

# Quick health check (perfect for monitoring scripts)
./easy-opal diagnose --quiet

# When everything is healthy:
🎉 SYSTEM HEALTHY
   ✅ All 8 tests passed - your easy-opal installation is working perfectly!

# When issues are detected:
🚨 CRITICAL ISSUES DETECTED
   ❌ 2 failed, ⚠️ 1 warnings, ✅ 3 passed
   Run './easy-opal diagnose' for detailed troubleshooting info
```

**Smart Stack Detection**: If the stack is not running, the diagnostic tool will:
-   Clearly indicate the stack is down instead of showing misleading test failures
-   Offer to automatically start the stack and then run diagnostics (interactive mode)
-   Provide clear next steps for manual stack startup (quiet/automated modes)

**Exit codes**: Returns the number of failed tests (0 = success), making it perfect for automated monitoring and CI/CD pipelines.

**Use cases:**
-   **Troubleshooting**: Quickly identify connectivity or configuration issues
-   **Health Monitoring**: Regular system health checks in production
-   **Post-Setup Validation**: Verify everything works after initial setup or changes
-   **CI/CD Integration**: Automated testing in deployment pipelines

---

### `config`

Manage the stack's configuration and snapshots.

-   `./easy-opal config show`: Displays the current `config.json`.
-   `./easy-opal config change-password [PASSWORD]`: Changes the Opal administrator password.
-   `./easy-opal config change-port [PORT]`: Changes the external port for NGINX.
-   `./easy-opal config restore [SNAPSHOT_ID]`: Restore a configuration from a snapshot.
    -   `--yes`: Bypasses confirmation prompts.
-   `./easy-opal config export`: Generates a compressed, shareable string of your current configuration.
-   `./easy-opal config import [STRING]`: Imports a configuration from an exported string.
    -   `--yes`: Bypasses confirmation prompts.

---

### `profile`

Manage the Rock server profiles in your stack.

-   `./easy-opal profile list`: Lists all configured profiles.
-   `./easy-opal profile add`: Interactively add a new Rock profile.
    -   `--repository TEXT`: The Docker Hub repository (e.g., `datashield`).
    -   `--image TEXT`: The image name (e.g., `rock-base`).
    -   `--tag TEXT`: The image tag (default: `latest`).
    -   `--name TEXT`: The service name for this profile.
    -   `--yes`: Bypasses confirmation prompts.
-   `./easy-opal profile remove [NAME]`: Remove a profile by name or interactively.
    -   `--yes`: Bypasses confirmation prompts.

---

### `cert`

-   `./easy-opal cert regenerate`: Manually regenerates self-signed certificates.

---

### `update`

-   `./easy-opal update`: Checks for and pulls the latest version of the tool from Git.

---

## Database Configuration

Easy-Opal supports deploying additional database containers alongside the default MongoDB instance, which serves as the primary metadata store for Opal. You can configure multiple database instances of different types (PostgreSQL, MySQL, MariaDB) or even multiple instances of the same type.

### Supported Database Types

-   **PostgreSQL** (port 5432 by default)
-   **MySQL** (port 3306 by default)
-   **MariaDB** (port 3307 by default)
-   **MongoDB** (always enabled as the primary metadata database)

### Interactive Configuration

When running `./easy-opal setup` interactively, you'll be prompted to add database instances:

1. Select the database type (postgres, mysql, mariadb)
2. Provide a unique instance name (e.g., `analytics`, `warehouse-1`)
3. Configure the port (automatically suggests a free port)
4. Set the username (defaults to `opal`)
5. Provide a password
6. Repeat to add more instances or select `done` to continue

### Non-Interactive Configuration

Use the `--database` flag with the format: `type:name:port:user:password`

**Examples:**

```bash
# Single PostgreSQL instance
./easy-opal setup --database postgres:maindb:5432:opal:mypassword --yes

# Multiple instances of different types
./easy-opal setup \
  --database postgres:analytics:5432:opal:pass1 \
  --database mysql:app:3306:opal:pass2 \
  --database mariadb:archive:3307:opal:pass3 \
  --yes

# Multiple instances of the same type
./easy-opal setup \
  --database postgres:analytics:5432:opal:pass1 \
  --database postgres:warehouse:5433:admin:pass2 \
  --database postgres:staging:5434:developer:pass3 \
  --yes
```

### Environment Variables

Each database instance is automatically configured in Opal's environment using uppercase variable names based on the instance name:

For a database named `analytics`:
-   `ANALYTICS_HOST`: Service hostname
-   `ANALYTICS_PORT`: Internal port
-   `ANALYTICS_DATABASE`: Database name
-   `ANALYTICS_USER`: Username
-   `ANALYTICS_PASSWORD`: Password

For a database named `warehouse-1`:
-   `WAREHOUSE_1_HOST`: Service hostname (hyphens converted to underscores)
-   `WAREHOUSE_1_PORT`: Internal port
-   `WAREHOUSE_1_DATABASE`: Database name
-   `WAREHOUSE_1_USER`: Username
-   `WAREHOUSE_1_PASSWORD`: Password

### Port Selection

-   The wizard automatically detects and suggests free ports
-   Default ports: PostgreSQL (5432), MySQL (3306), MariaDB (3307)
-   For multiple instances of the same type, the wizard will suggest incremental ports (5432, 5433, 5434, etc.)

### Data Persistence

Each database instance gets its own named Docker volume:
-   `{instance-name}_data`: Stores all database data

These volumes persist across container restarts and can be managed with standard Docker volume commands.

---

## Data Persistence: Volumes vs. Local Directories

This tool uses a hybrid approach for data persistence to balance ease of management and Docker best practices.

-   **Local Directories (`./data`)**:
    -   **What**: Holds NGINX configuration, SSL certificates, and Let's Encrypt challenge data.
    -   **Why**: These are critical configuration files that you might need to inspect, modify, back up, or provide yourself (e.g., custom SSL certificates). Storing them in a local directory makes them transparent and easily accessible.

-   **Named Docker Volumes**:
    -   **What**: Used for all application-generated data, including the MongoDB database, Opal server data, and all Rock profile data.
    -   **Why**: This is the recommended Docker approach for managing the state of stateful applications. Docker manages the lifecycle of this data, which abstracts it from the host machine's filesystem, improves I/O performance, and simplifies data management across different environments.
