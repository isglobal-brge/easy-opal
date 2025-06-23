# easy-opal

A command-line tool to easily set up and manage OBiBa Opal environments using Docker and NGINX. This tool provides a simple wizard for initial setup, as well as commands for managing the stack, Rock profiles, and configuration.

## Prerequisites

Before you begin, ensure you have the following installed on your system:
- **Docker**: Required to run the Opal and Rock containers.
- **Git**: Required for the `update` command.
- **mkcert**: Required for creating locally-trusted SSL certificates for development.

The setup script will attempt to install these for you if they are not found.

## Installation

The installation is handled by a single setup script.

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/davidsarratgonzalez/easy-opal.git
    cd easy-opal
    ```

2.  **Run the setup script**:
    Make the script executable and run it.
    ```bash
    chmod +x setup.sh
    ./setup.sh
    ```
    This script will:
    - Detect your OS and install `mkcert` if it's not present.
    - Run `mkcert -install` to create a local Certificate Authority (CA).
    - Create a Python virtual environment in a `venv` directory.
    - Install all the necessary Python packages from `requirements.txt`.

## Usage

All commands should be run from the root of the project directory.

### 1. Activate the Environment

Before running the application, activate the Python virtual environment:
```bash
source venv/bin/activate
```

### 2. Initial Setup

To configure your Opal stack for the first time, you can run the interactive setup wizard:
```bash
python3 easy-opal.py setup
```
Alternatively, you can provide all setup options as flags for non-interactive or scripted setups:
```bash
# Example for a self-signed certificate
python3 easy-opal.py setup \
  --stack-name my-opal \
  --host localhost --host 192.168.1.100 \
  --port 443 \
  --password "supersecret" \
  --ssl-strategy "self-signed"

# Example for a manual certificate
python3 easy-opal.py setup \
  --stack-name my-opal \
  --host my-opal.domain.com \
  --port 443 \
  --password "supersecret" \
  --ssl-strategy "manual" \
  --ssl-cert-path /path/to/cert.crt \
  --ssl-key-path /path/to/key.key
```

### 3. Stack Lifecycle Commands

- **Start the stack**:
  ```bash
  python3 easy-opal.py up
  ```
- **Stop the stack**:
  ```bash
  python3 easy-opal.py down
  ```
- **Check the status of the services**:
  ```bash
  python3 easy-opal.py status
  ```
- **Factory Reset**: Stop all services and remove data. Use `--help` to see all options.
  ```bash
  # Interactive wizard
  python3 easy-opal.py reset

  # Non-interactive: Delete everything and bypass confirmation
  python3 easy-opal.py reset --all --yes
  ```

### 4. Managing Rock Profiles

- **Add a new profile**:
  ```bash
  # Interactive wizard
  python3 easy-opal.py profile add

  # Non-interactive, with automatic restart
  python3 easy-opal.py profile add \
    --repository datashield \
    --image rock-mediation \
    --tag latest \
    --name rock-mediation-beta \
    --yes
  ```
- **Remove a profile**:
  ```bash
  # Interactive wizard
  python3 easy-opal.py profile remove

  # Non-interactive, with automatic removal
  python3 easy-opal.py profile remove rock-mediation-beta --yes
  ```
- **List configured profiles**:
  ```bash
  python3 easy-opal.py profile list
  ```

### 5. Configuration Management

- **Show current configuration**:
  ```bash
  python3 easy-opal.py config show
  ```
- **Change the Opal admin password**:
  ```bash
  python3 easy-opal.py config change-password "new_password"
  ```
- **Change the external port**:
  ```bash
  python3 easy-opal.py config change-port 8443
  ```

### 6. Certificate Management

- **Regenerate SSL certificates**: This will regenerate your certificate based on the strategy defined in your configuration.
  ```bash
  python3 easy-opal.py cert regenerate
  ```

### 7. Updating the Tool

- **Check for and apply updates**: This command will check the official repository for new versions of `easy-opal` and guide you through the update process.
  ```bash
  python3 easy-opal.py update
  ```
