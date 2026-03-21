# easy-opal

A command-line tool to deploy and manage [OBiBa Opal](https://www.obiba.org/pages/products/opal/) environments with Docker. It handles everything: setup wizard, multi-instance management, SSL certificates, Rock R server profiles, additional databases, backups, health diagnostics, and optional Agate/Mica integration.

## Requirements

- **Docker** with Compose V2 (or Podman with Compose)
- **curl** (for first-time install only)

That's it. Python, all dependencies, and SSL certificates are handled automatically by easy-opal. You don't need to install anything else.

## Installation

Open a terminal and run:

```bash
curl -sSf https://easy-opal.github.io | sh
```

This installs [uv](https://docs.astral.sh/uv/) (a fast Python package manager) if you don't have it, then installs easy-opal globally. After installation, the `easy-opal` command is available from anywhere in your terminal.

## Your first deployment

The easiest way to get started is the interactive setup wizard. It walks you through every step:

```bash
easy-opal setup
```

The wizard will ask you for:

1. **Stack name** — identifies this deployment in Docker (e.g., `my-opal`)
2. **Service versions** — Opal and MongoDB image tags (default: `latest`)
3. **SSL strategy** — how to handle HTTPS (self-signed for dev, Let's Encrypt for production)
4. **Databases** — optional PostgreSQL, MySQL, or MariaDB instances
5. **Watchtower** — optional automatic container updates
6. **Agate / Mica** — optional authentication server and data portal
7. **Admin password** — enter your own or let it generate a secure one

For scripting or CI/CD, pass everything as flags to skip the wizard entirely:

```bash
easy-opal setup \
  --stack-name my-opal \
  --host localhost \
  --port 8443 \
  --ssl-strategy self-signed \
  --password "MyPassword123" \
  --yes
```

If you don't pass `--password`, a secure random password is generated and displayed once. You can always retrieve it later:

```bash
easy-opal config show-password
```

## Managing your stack

Once configured, these commands control your Opal deployment:

```bash
easy-opal up          # Start (only recreates changed containers)
easy-opal down        # Stop all containers
easy-opal restart     # Full stop + start cycle
easy-opal status      # Show container status
```

`up` is convergent — it only recreates containers whose configuration has changed. It also waits for all Docker healthchecks to pass before returning, so when the command finishes, your services are ready to use.

To completely wipe everything and start fresh:

```bash
easy-opal reset --volumes --yes
```

## Changing configuration

You don't need to re-run the setup wizard to change settings. Every aspect of the configuration can be modified individually. All changes automatically regenerate the Docker Compose file, NGINX config, SSL certificates, and CSRF settings as needed.

```bash
# View current state
easy-opal config show
easy-opal config show-version
easy-opal config show-password

# Change service versions
easy-opal config change-version 7.0 --service mongo

# Change network settings (CSRF auto-updates)
easy-opal config change-port 9443
easy-opal config change-hosts opal.dev 10.0.0.1

# Switch SSL strategy
easy-opal config change-ssl letsencrypt --ssl-email admin@example.com
easy-opal config change-ssl manual --ssl-cert /path/to/cert --ssl-key /path/to/key

# Change admin password
easy-opal config change-password
```

After making changes, apply them with:

```bash
easy-opal restart
```

To preview what would change without applying:

```bash
easy-opal config change-port 9443 --dry-run
```

## Multiple deployments

easy-opal can manage multiple independent Opal deployments on the same machine. Each instance has its own configuration, secrets, data, and Docker stack — completely isolated from each other.

```bash
# Create named instances
easy-opal instance create production
easy-opal instance create staging

# List all instances with their status
easy-opal instance list

# Show detailed info (config, containers, certificates)
easy-opal instance info production

# Operate on a specific instance
easy-opal -i production up
easy-opal -i staging setup

# Remove an instance
easy-opal instance remove staging --yes
```

When only one instance exists, it's auto-selected. With multiple instances, use `-i <name>` to specify which one.

## SSL certificates

easy-opal supports four SSL strategies:

| Strategy | NGINX | Certificates | Best for |
|----------|-------|-------------|----------|
| `self-signed` | Yes | Auto-generated local CA + server cert | Development and testing |
| `letsencrypt` | Yes | Free trusted cert via ACME HTTP-01 | Production with a public domain |
| `manual` | Yes | Your own cert + key files | Corporate or custom CA |
| `none` | No | Opal exposed on HTTP directly | Behind an external reverse proxy |

Self-signed certificates are generated with Python's `cryptography` library — no external tools needed. The local CA is persistent: regenerating the server certificate does not invalidate browser trust. You can import `data/certs/ca.crt` into your browser to avoid warnings.

```bash
# Regenerate server cert (keeps the CA)
easy-opal cert regenerate

# Show certificate details (SANs, expiry date)
easy-opal cert info

# Force regenerate the CA (breaks existing browser trust)
easy-opal cert ca-regenerate
```

## Databases

MongoDB is always included as Opal's metadata store. You can add additional databases for your data sources — PostgreSQL, MySQL, or MariaDB. They can be local (Docker containers managed by easy-opal) or external (pointing to your own servers).

```bash
# Add during setup
easy-opal setup --database postgres:analytics:5432:opal --yes

# Add a specific version
easy-opal setup --database postgres:warehouse:5433:admin:16 --yes

# Remove a database and its Docker volume
easy-opal config remove-database analytics --delete-volume --yes
```

Format: `type:name:port:user[:version]`. All default to `latest`. Each database automatically gets its own Docker volume, healthcheck, and environment variables injected into Opal.

## Agate and Mica

**Agate** is OBiBa's authentication server — it handles user registration, password resets, and email notifications. **Mica** is the data portal for publishing metadata catalogs. Both are optional and can be enabled at any time.

```bash
# Enable during setup
easy-opal setup --with-agate --yes
easy-opal setup --with-mica --yes   # auto-enables Agate

# Or enable later
easy-opal config agate enable
easy-opal config mica enable

# Configure email (for Agate notifications)
easy-opal config agate --mail-mode smtp \
  --smtp-host smtp.gmail.com \
  --smtp-port 587 \
  --smtp-user me@gmail.com \
  --smtp-password "app-password" \
  --smtp-from me@gmail.com

# Switch to Mailpit for local development
easy-opal config agate --mail-mode mailpit

# Check current status
easy-opal config agate status
easy-opal config mica status

# Disable
easy-opal config agate disable
easy-opal config mica disable
```

For development, Agate uses [Mailpit](https://mailpit.axllent.org/) by default — a local mail server that captures all emails without sending them. Access its web UI at `http://localhost:8025`. For production, configure a real SMTP server.

## Backup and restore

Backups use native database tools inside the containers (mongodump, pg_dump, mysqldump) for consistency. Each backup is a `.tar.gz` archive with a manifest describing its contents.

```bash
# Create a full backup
easy-opal backup create

# Save to a specific path
easy-opal backup create -o /backups/opal-2024-01.tar.gz

# List available backups
easy-opal backup list

# Restore from a backup
easy-opal backup restore backup.tar.gz
```

## Health and diagnostics

```bash
# Check stack health (containers, SSL, endpoints, databases)
easy-opal diagnose
easy-opal diagnose --quiet   # summary only

# Check easy-opal itself (Docker, config, secrets, permissions)
easy-opal doctor

# Validate config without starting anything
easy-opal validate

# Preview the generated docker-compose.yml
easy-opal plan

# Collect redacted diagnostics for sharing
easy-opal support-bundle
```

## Presets

Presets are named configuration templates for common deployment patterns. They set sensible defaults so you don't have to configure everything manually.

| Preset | What it configures |
|--------|-------------------|
| `opal-dev` | Self-signed SSL, Watchtower off — for local development |
| `opal-prod` | Let's Encrypt SSL, Watchtower on (24h) — for production servers |
| `opal-proxy` | No SSL — for deployments behind an external reverse proxy |
| `opal-agate` | Opal + Agate authentication + Mailpit |
| `obiba-full` | Opal + Agate + Mica + Elasticsearch — the full OBiBa stack |

```bash
easy-opal setup --preset opal-prod --host opal.example.com --yes
```

You can still override individual settings after applying a preset.

## Rock profiles

Rock is the R server that Opal uses for statistical analysis and DataSHIELD operations. By default, one Rock profile is included with the base R packages. You can add more profiles with different R packages for specific use cases.

```bash
# List current profiles
easy-opal profile list

# Add a new profile
easy-opal profile add --image datashield/rock-omics --tag 2.0 --name rock-omics

# Remove a profile
easy-opal profile remove rock-omics --yes
```

## Volumes

All Docker volumes are prefixed with the stack name to prevent collisions between instances. You can inspect and clean them up:

```bash
# Show volumes for this stack
easy-opal volumes list

# Remove unused volumes (stops the stack first)
easy-opal volumes prune
```

## Updating easy-opal

To update easy-opal to the latest version:

```bash
easy-opal update
```

This auto-detects how easy-opal was installed and uses the appropriate update method.

## All setup flags

| Flag | Description |
|------|-------------|
| `--stack-name TEXT` | Docker project name |
| `--host TEXT` | Hostname or IP address (repeatable) |
| `--port INT` | HTTPS port (default: 443) |
| `--http-port INT` | HTTP port for `none` SSL strategy |
| `--ssl-strategy` | `self-signed`, `letsencrypt`, `manual`, `none` |
| `--password TEXT` | Admin password (auto-generated if not set) |
| `--opal-version TEXT` | Opal Docker image tag (default: `latest`) |
| `--mongo-version TEXT` | MongoDB Docker image tag (default: `latest`) |
| `--database TEXT` | `type:name:port:user[:version]` (repeatable) |
| `--preset` | `opal-dev`, `opal-prod`, `opal-proxy`, `opal-agate`, `obiba-full` |
| `--watchtower` | Enable automatic container updates |
| `--watchtower-interval INT` | Update check interval in hours (default: 24) |
| `--with-agate` | Enable Agate authentication server |
| `--with-mica` | Enable Mica data portal (implies Agate) |
| `--yes` | Skip all interactive prompts |

## Source code

easy-opal is open source under the MIT license. The codebase is modular: each service (Opal, MongoDB, NGINX, Rock, Agate, Mica, etc.) is a self-contained module. Contributions, issues, and feature requests are welcome.

**GitHub:** [https://github.com/isglobal-brge/easy-opal](https://github.com/isglobal-brge/easy-opal)

## Authors

- [David Sarrat Gonzalez](https://davidsarratgonzalez.github.io)
- Juan R Gonzalez

[Bioinformatic Research Group in Epidemiology (BRGE)](https://brge.isglobal.org), [Barcelona Institute for Global Health (ISGlobal)](https://www.isglobal.org)
