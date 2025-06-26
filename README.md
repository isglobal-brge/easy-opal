# easy-opal

A command-line tool to easily set up and manage OBiBa Opal environments using Docker and NGINX. This tool provides a simple wizard for initial setup, as well as commands for managing the stack, Rock profiles, and configuration.

## Prerequisites

Before you begin, ensure you have the following installed on your system:
- **Python 3.8+**
- **Docker**: Required to run the Opal and Rock containers.
- **Git**: Required for the `update` command.
- **mkcert**: Required for creating locally-trusted SSL certificates for development.

The setup script will check for these and guide you if they are missing.

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

The installation process is streamlined using Poetry for robust dependency and environment management.

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/davidsarratgonzalez/easy-opal.git
    cd easy-opal
    ```

2.  **Run the main setup script**:
    Make the script executable and run it.
    ```bash
    chmod +x setup
    ./setup
    ```
    This script will handle everything:
    - Check for system-level dependencies like Docker and Git.
    - Install **Poetry** (the Python package manager) if it's not present.
    - Use Poetry to automatically create a virtual environment and install all Python dependencies.
    - Install `mkcert` and set up its local Certificate Authority (CA).
    
    > **Note:** After the setup script finishes, you may need to **open a new terminal** or reload your shell's configuration (e.g., `source ~/.zshrc`) for the `poetry` command to become available.

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
-   `--yes`: Bypasses all interactive prompts. Essential for scripting.
-   `--reset-containers`: Non-interactively stops and removes Docker containers.
-   `--reset-volumes`: Non-interactively deletes Docker volumes (all application data).
-   `--reset-configs`: Non-interactively deletes configuration files.
-   `--reset-certs`: Non-interactively deletes SSL certificates.
-   `--reset-secrets`: Non-interactively deletes the `.env` file.

**Example (Non-Interactive):**

```bash
./easy-opal setup \
  --stack-name new-stack \
  --host localhost \
  --port 8443 \
  --password "newpass" \
  --ssl-strategy "self-signed" \
  --yes \
  --reset-containers
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

## Data Persistence: Volumes vs. Local Directories

This tool uses a hybrid approach for data persistence to balance ease of management and Docker best practices.

-   **Local Directories (`./data`)**:
    -   **What**: Holds NGINX configuration, SSL certificates, and Let's Encrypt challenge data.
    -   **Why**: These are critical configuration files that you might need to inspect, modify, back up, or provide yourself (e.g., custom SSL certificates). Storing them in a local directory makes them transparent and easily accessible.

-   **Named Docker Volumes**:
    -   **What**: Used for all application-generated data, including the MongoDB database, Opal server data, and all Rock profile data.
    -   **Why**: This is the recommended Docker approach for managing the state of stateful applications. Docker manages the lifecycle of this data, which abstracts it from the host machine's filesystem, improves I/O performance, and simplifies data management across different environments.
