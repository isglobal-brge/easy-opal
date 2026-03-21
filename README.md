# easy-opal

A CLI tool to deploy and manage [OBiBa Opal](https://www.obiba.org/pages/products/opal/) environments with Docker. Setup wizard, multi-instance management, SSL certificates, Rock profiles, database support, backups, and health diagnostics — all from one command.

## Requirements

- **Docker** with Compose V2
- **curl** or **wget** (for first-time setup only)

That's it. Python, dependencies, and SSL certificates are handled automatically.

## Install

```bash
curl -sSf https://easy-opal.github.io | sh
```

This installs [uv](https://docs.astral.sh/uv/) (if needed) and easy-opal globally. Then:

```bash
easy-opal setup
```

**Update:**

```bash
easy-opal update
```

## Quick Start

```bash
# Create and configure a new deployment
./easy-opal setup

# Or non-interactive
./easy-opal setup \
  --stack-name my-opal \
  --host localhost \
  --port 8443 \
  --ssl-strategy self-signed \
  --yes
```

The wizard walks you through: stack name, service versions, SSL strategy, databases, and Watchtower.

---

## Commands

### Instance Management

easy-opal supports multiple independent deployments. Each instance has its own config, secrets, data, and Docker stack.

```bash
easy-opal instance create prod              # Create instance
easy-opal instance create staging --path /opt/opals  # Custom location
easy-opal instance list                     # List all instances
easy-opal instance info prod                # Show instance details
easy-opal instance remove staging --yes     # Remove instance
```

When only one instance exists, it's auto-selected. With multiple instances, specify with `-i`:

```bash
easy-opal -i prod up
easy-opal -i staging setup
```

### Setup

```bash
easy-opal setup                    # Interactive wizard
easy-opal setup --yes [flags]      # Non-interactive
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--stack-name TEXT` | Docker project name |
| `--host TEXT` | Hostname/IP (repeatable) |
| `--port INT` | HTTPS port (default: 443) |
| `--http-port INT` | HTTP port for `none` strategy |
| `--ssl-strategy` | `self-signed`, `letsencrypt`, `manual`, `none` |
| `--ssl-email TEXT` | Let's Encrypt email |
| `--ssl-cert TEXT` | Certificate path (manual strategy) |
| `--ssl-key TEXT` | Key path (manual strategy) |
| `--opal-version TEXT` | Opal image tag (default: `latest`) |
| `--mongo-version TEXT` | MongoDB image tag (default: `latest`) |
| `--database TEXT` | Add database: `type:name:port:user[:version]` (repeatable) |
| `--watchtower` | Enable Watchtower auto-updates |
| `--watchtower-interval INT` | Check interval in hours (default: 24) |
| `--yes` | Skip all prompts |

### Stack Lifecycle

```bash
easy-opal up          # Start (convergent — only recreates changed services)
easy-opal down        # Stop
easy-opal restart     # Full stop + start cycle
easy-opal status      # Show container status
easy-opal reset       # Stop stack
easy-opal reset --volumes --yes  # Stop and delete all data
```

`up` waits for Docker healthchecks to pass before returning.

### Configuration

All changes auto-regenerate the Docker Compose file, NGINX config, and certificates as needed.

```bash
easy-opal config show                              # Display config
easy-opal config show-version                      # Show all service versions
easy-opal config change-version 7.0 --service mongo  # Change MongoDB version
easy-opal config change-port 9443                  # Change HTTPS port (updates CSRF)
easy-opal config change-hosts opal.dev 10.0.0.1    # Change hosts (regenerates certs + CSRF)
easy-opal config change-ssl self-signed            # Switch SSL strategy
easy-opal config change-ssl manual --ssl-cert /path/to/cert --ssl-key /path/to/key
easy-opal config change-password                   # Change admin password
easy-opal config remove-database analytics --yes   # Remove a database
easy-opal config watchtower enable --interval 6    # Enable Watchtower (6h checks)
easy-opal config watchtower disable                # Disable Watchtower
easy-opal config watchtower status                 # Show Watchtower config
```

### SSL Certificates

```bash
easy-opal cert regenerate      # Regenerate server cert (preserves CA)
easy-opal cert info            # Show certificate details (SANs, expiry)
easy-opal cert ca-regenerate   # Force regenerate CA (breaks existing trust)
```

**SSL Strategies:**

| Strategy | NGINX | Certificates | Use case |
|----------|-------|-------------|----------|
| `self-signed` | Yes | Auto-generated CA + server cert | Development |
| `letsencrypt` | Yes | ACME HTTP-01 challenge | Production with public domain |
| `manual` | Yes | User-provided cert + key | Corporate/custom CA |
| `none` | No | None — Opal on HTTP directly | Behind reverse proxy |

Self-signed certs are generated with Python's `cryptography` library — no external tools needed. The CA is persistent; regenerating certs doesn't invalidate browser trust. Import `data/certs/ca.crt` to avoid browser warnings.

### Rock Profiles

```bash
easy-opal profile list                    # List profiles
easy-opal profile add --image datashield/rock-omics --tag 2.0 --name rock-omics
easy-opal profile remove rock-omics --yes
```

### Databases

Additional databases (PostgreSQL, MySQL, MariaDB) alongside the default MongoDB:

```bash
# Interactive (during setup)
easy-opal setup   # Wizard prompts for databases

# Non-interactive
easy-opal setup --database postgres:analytics:5432:opal --database mysql:warehouse:3306:opal --yes

# Remove
easy-opal config remove-database analytics --delete-volume --yes
```

Format: `type:name:port:user[:version]`. All default to `latest`.

Each database gets its own Docker volume (`{stack}-{name}-data`), healthcheck, and Opal environment variables (`{NAME}_HOST`, `{NAME}_PORT`, `{NAME}_DATABASE`, `{NAME}_USER`, `{NAME}_PASSWORD`).

### Backup & Restore

```bash
easy-opal backup create                    # Full backup (MongoDB + databases + Opal data)
easy-opal backup create -o /path/backup.tar.gz  # Custom output path
easy-opal backup list                      # List backups with size and date
easy-opal backup restore backup.tar.gz     # Restore from backup
```

Backups use native dump tools inside containers (mongodump, pg_dump, mysqldump) for consistency. Includes a manifest with metadata.

### Volume Management

```bash
easy-opal volumes list    # Show Docker volumes for this stack
easy-opal volumes prune   # Remove unused volumes (stops stack first)
```

All volumes are prefixed with the stack name to prevent collisions between instances.

### Health Checks

```bash
easy-opal diagnose           # Stack health: containers, SSL, endpoints
easy-opal diagnose --quiet   # Summary only
easy-opal doctor             # easy-opal itself: Docker, config, secrets, permissions
```

### Self-Update

```bash
easy-opal update    # Pull latest from git + update dependencies
```

---

## Architecture

```
~/.easy-opal/
  registry.json                  # Tracks all instances
  instances/
    prod/
      config.json                # Pydantic-validated config (source of truth)
      secrets.env                # Passwords (auto-generated, 0o600 permissions)
      docker-compose.yml         # Generated from config (never edit manually)
      data/
        certs/                   # SSL certificates (CA + server)
        nginx/                   # NGINX config
        html/                    # Maintenance page
        letsencrypt/             # Let's Encrypt data
      backups/                   # Backup archives
```

**Key design decisions:**
- `config.json` is the single source of truth. `docker-compose.yml` is always regenerated from it.
- Secrets are never stored in config. They live in `secrets.env` with strict file permissions.
- Each service (MongoDB, Opal, NGINX, Rock, databases, Watchtower) is a modular plugin in `src/services/`.
- All passwords are randomly generated with `secrets.token_urlsafe(24)`.
- CSRF origins are computed from configured hosts + port (not `*`).
- The CA is persistent — regenerating server certs doesn't break browser trust.
- Docker healthchecks on all services with proper dependency ordering (`mongo → opal → nginx/rock`).
- Schema versioning (`schema_version: 2`) with automatic migration from older configs.
- Instance lock files prevent concurrent operations.

## Security

- Admin and Rock passwords: randomly generated, stored in `secrets.env` (0o600)
- SSL private keys: 0o600 permissions
- CSRF: computed from configured hosts with port, not wildcard
- No hardcoded default passwords anywhere
- Persistent CA for self-signed certs
- Docker healthchecks prevent premature access

## License

[MIT](LICENSE)

## Authors

- [David Sarrat González](https://davidsarratgonzalez.github.io) — [ISGlobal](https://www.isglobal.org)
- Xavier Escribà Montagut — ISGlobal
- Juan R González — ISGlobal

[Bioinformatics Research Group in Epidemiology (BRGE)](https://brge.isglobal.org) — [Barcelona Institute for Global Health (ISGlobal)](https://www.isglobal.org)
