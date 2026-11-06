# Ubuntu Docker installation

From a fresh Ubuntu (22.04, 24.04 or 26.04) to a running Opal. You need about 25 GB of free disk space. Open a
terminal and copy the commands of each step.

## 1. Install Docker

```bash
sudo apt update && sudo apt install -y curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Restart the computer (or log out and back in) so that you can use Docker without `sudo`. Check it:

```bash
docker run hello-world
```

It prints `Hello from Docker!`.

This is Docker's official install script. For a production server you can also follow Docker's step-by-step method:
<https://docs.docker.com/engine/install/ubuntu/>.

## 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the terminal and open a new one.

## 3. Install easy-opal

```bash
uv tool install easy-opal
```

## 4. Set up Opal

```bash
easy-opal setup
```

Press Enter at every question to keep the default. At the end, easy-opal starts Opal and prints its address and the
`administrator` password.

Open that address in your browser (on this computer: <https://localhost>). The first time, the browser warns about the
certificate: choose *Advanced* and continue.

## 5. Add a profile (optional)

```bash
easy-opal profile add
```

Type the image, for example `datashield/rock-omics`, and press Enter at the other questions. Then apply it:

```bash
easy-opal restart
```

## Help and everyday commands

```bash
easy-opal --help                # the list of commands
easy-opal profile --help        # help for one command
easy-opal config show-password  # the administrator password
easy-opal status                # what is running
easy-opal down                  # stop Opal
easy-opal up                    # start Opal
easy-opal update                # update easy-opal
```

Everything else is in the [README](README.md).
