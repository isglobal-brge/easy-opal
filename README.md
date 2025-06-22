# easy-opal

A command-line tool to easily set up and manage OBiBa Opal environments using Docker and NGINX. This tool provides a simple wizard for initial setup, as well as commands for managing the stack, Rock profiles, and configuration.

## Prerequisites

Before you begin, ensure you have the following installed on your system:
- **Docker**: Required to run the Opal and Rock containers. [Install Docker](https://docs.docker.com/get-docker/).
- **mkcert**: Required for creating locally-trusted SSL certificates for development. The setup script will attempt to install this for you.

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

To configure your Opal stack for the first time, run the interactive setup wizard:
```bash
python3 easy-opal.py setup
```
The wizard will guide you through:
- Naming your stack.
- Configuring the SSL certificate strategy (`self-signed`, `letsencrypt`, or `manual`).
- Setting hostnames/IPs for the certificate.
- Setting the external port for Opal.
- Setting the Opal administrator password.

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
- **Factory Reset**: Stop all services, remove all data (volumes, certs), and delete all configuration files. This returns the project to a clean state, requiring you to run `setup` again.
  ```bash
  python3 easy-opal.py reset
  ```

### 4. Managing Rock Profiles

- **Add a new profile**: An interactive wizard will guide you through adding a new Rock service. The new service will be started automatically.
  ```bash
  python3 easy-opal.py profile add
  ```
- **Remove a profile**: Lists the current profiles and prompts you to select one to remove. The corresponding container will be stopped and removed automatically.
  ```bash
  python3 easy-opal.py profile remove
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
  python3 easy-opal.py config change-password
  ```
- **Change the external port**:
  ```bash
  python3 easy-opal.py config change-port
  ```

### 6. Certificate Management

- **Regenerate SSL certificates**: This will regenerate your certificate based on the strategy defined in your configuration.
  ```bash
  python3 easy-opal.py cert regenerate
  ```

---
Created by David Sarrat González
GitHub: [davidsarratgonzalez/easy-opal](https://github.com/davidsarratgonzalez/easy-opal) 