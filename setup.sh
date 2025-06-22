#!/bin/bash
# A simple script to set up the virtual environment and install dependencies.

VENV_NAME="venv"
PYTHON_CMD="python3"

# Function to install system dependencies
install_system_deps() {
    echo "Checking for system dependencies..."
    OS="$(uname -s)"

    case "${OS}" in
        Linux*)
            echo "Detected Linux OS."
            if ! command -v mkcert &> /dev/null; then
                if command -v apt-get &> /dev/null; then
                    echo "mkcert not found. Attempting to install with apt-get..."
                    sudo apt-get update && sudo apt-get install -y mkcert
                elif command -v dnf &> /dev/null; then
                    echo "mkcert not found. Attempting to install with dnf..."
                    sudo dnf install -y mkcert
                else
                    echo "Could not determine package manager. Please install 'mkcert' manually."
                    exit 1
                fi
            fi
            ;;
        Darwin*)
            echo "Detected macOS."
            if ! command -v brew &> /dev/null; then
                echo "'brew' (Homebrew) is not installed. Please install it from https://brew.sh/"
                exit 1
            fi
            if ! command -v mkcert &> /dev/null; then
                echo "mkcert not found. Installing with Homebrew..."
                brew install mkcert
            fi
            ;;
        *)
            echo "Unsupported OS: ${OS}. Please install 'mkcert' manually."
            exit 1
            ;;
    esac
    
    # After installing mkcert, ensure its local CA is trusted.
    if command -v mkcert &> /dev/null; then
        echo "Ensuring the mkcert local CA is installed in your trust store..."
        # This command might prompt for the user's password.
        mkcert -install
    else
        echo "[ERROR] mkcert installation failed. Please try installing it manually."
        exit 1
    fi
}

# --- Main script ---

# 1. Install system dependencies
install_system_deps

# 2. Check for python3
if ! command -v $PYTHON_CMD &> /dev/null
then
    echo "$PYTHON_CMD could not be found. Please install Python 3."
    exit 1
fi

# 3. Create virtual environment
if [ ! -d "$VENV_NAME" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv $VENV_NAME
else
    echo "Virtual environment already exists."
fi

# 4. Activate virtual environment and install python dependencies
echo "Installing Python dependencies..."
source $VENV_NAME/bin/activate
pip install -r requirements.txt

echo -e "\nSetup complete. To activate the virtual environment, run:"
echo "source $VENV_NAME/bin/activate"
echo "Then you can run the application with:"
echo "python3 easy-opal.py setup" 