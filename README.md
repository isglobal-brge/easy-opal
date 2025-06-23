# easy-opal

A command-line tool to easily set up and manage OBiBa Opal environments using Docker and NGINX. This tool provides a simple wizard for initial setup, as well as commands for managing the stack, Rock profiles, and configuration.

## Prerequisites

Before you begin, ensure you have the following installed on your system:
- **Python 3.8+**
- **Docker**: Required to run the Opal and Rock containers.
- **Git**: Required for the `update` command.
- **mkcert**: Required for creating locally-trusted SSL certificates for development.

The setup script will check for these and guide you if they are missing.

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

All commands should be run from the root of the project directory using the `./easy-opal` wrapper script.

### 1. Initial Setup

To configure your Opal stack for the first time, you can run the interactive setup wizard:
```bash
./easy-opal setup
```
The wizard will guide you through the rest. For non-interactive setups, you can use flags:
```bash
# Example for a self-signed certificate
./easy-opal setup \
  --stack-name my-opal \
  --host localhost --host 192.168.1.100 \
  --port 443 \
  --password "supersecret" \
  --ssl-strategy "self-signed"

# Example for a manual certificate
./easy-opal setup \
  --stack-name my-opal \
  --host my-opal.domain.com \
  --port 443 \
  --password "supersecret" \
  --ssl-strategy "manual" \
  --ssl-cert-path /path/to/cert.crt \
  --ssl-key-path /path/to/key.key
```

### 2. Stack Lifecycle Commands

- **Start the stack**:
  ```bash
  ./easy-opal up
  ```
- **Stop the stack**:
  ```bash
  ./easy-opal down
  ```
- **Get stack status**:
  ```bash
  ./easy-opal status
  ```
- **Factory Reset**: An interactive wizard to selectively delete components.
  ```bash
  # Interactive wizard
  ./easy-opal reset

  # Non-interactive: Delete everything
  ./easy-opal reset --all --yes
  ```

### 3. Managing Rock Profiles

- **Add a profile**:
  ```bash
  # Interactive wizard
  ./easy-opal profile add

  # Non-interactive
  ./easy-opal profile add --repository datashield --image rock-mediation --name rock-beta --yes
  ```
- **Remove a profile**:
  ```bash
  # Interactive wizard
  ./easy-opal profile remove

  # Non-interactive
  ./easy-opal profile remove rock-beta --yes
  ```
- **List profiles**:
  ```bash
  ./easy-opal profile list
  ```

### 4. Configuration Management

- **Show configuration**:
  ```bash
  ./easy-opal config show
  ```
- **Change password**:
  ```bash
  ./easy-opal config change-password "new-secret"
  ```
- **Change port**:
  ```bash
  ./easy-opal config change-port 8443
  ```

### 5. Certificate Management

- **Regenerate certificates**:
  ```bash
  ./easy-opal cert regenerate
  ```

### 6. Updating the Tool

- **Check for and apply updates**:
  ```bash
  ./easy-opal update
  ```

## Data Persistence: Volumes vs. Local Directories

This tool uses a hybrid approach for data persistence to balance ease of management and Docker best practices.

-   **Local Directories (`./data`)**:
    -   **What**: Holds NGINX configuration, SSL certificates, and Let's Encrypt challenge data.
    -   **Why**: These are critical configuration files that you might need to inspect, modify, back up, or provide yourself (e.g., custom SSL certificates). Storing them in a local directory makes them transparent and easily accessible.

-   **Named Docker Volumes**:
    -   **What**: Used for all application-generated data, including the MongoDB database, Opal server data, and all Rock profile data.
    -   **Why**: This is the recommended Docker approach for managing the state of stateful applications. Docker manages the lifecycle of this data, which abstracts it from the host machine's filesystem, improves I/O performance (especially on Docker Desktop for Mac and Windows), and simplifies data management across different environments.
