# easy-opal

A command-line tool to deploy and manage [OBiBa Opal](https://www.obiba.org/pages/products/opal/) environments with Docker or Podman. The two runtimes are alternatives: lifecycle operations, health-checked image updates, backups, diagnostics, and optional Agate/Mica integration use the runtime selected for each instance.

## Requirements

- One container runtime (you do not need both):
  - **Docker** with Compose V2 supporting `up --wait` and `--wait-timeout`, or
  - **Podman >= 4.6** with the independent `podman-compose >= 1.6.0` provider

The Python package does not install or import either runtime. The selected CLI and
its Compose provider must be available on the host.
Image references such as `docker.io/obiba/opal` name a registry; Podman pulls
them directly and they do not imply a Docker CLI or daemon dependency.
Unattended maintenance additionally needs `systemd` on Linux or `launchd` on
macOS. Manual maintenance commands work without enabling a scheduler.

On Linux with rootless Podman, new HTTPS deployments default to port `8443`
when the host reserves ports below `1024`. Explicit privileged ports are
validated before configuration is saved, including internal database,
Mailpit, and Keycloak host ports. Let's Encrypt HTTP-01 also needs host port
`80`, so it requires an adjusted host threshold or rootful Podman. Behind an
external reverse proxy, use the `none` or `manual` SSL strategy instead. For a
remote Podman connection, easy-opal cannot read the remote kernel threshold;
validate that host's port policy separately.

### Choosing Docker or Podman

Use `--runtime` before the command when you want an explicit runtime:

```bash
easy-opal --runtime podman setup
easy-opal --runtime docker up
```

`EASY_OPAL_RUNTIME=podman` provides the same selection for scripts. With the
default `auto` mode, easy-opal reuses the runtime already bound to an instance;
for a new instance it checks complete runtime + Compose pairs. Installing the
other runtime later does not silently move an existing instance to it.

The binding records the engine family, not a remote endpoint or storage
fingerprint. Keep `DOCKER_CONTEXT`/`DOCKER_HOST` or the active Podman connection
and rootless/rootful mode stable for an existing instance. To change them,
create a new instance on the target context and transfer the required data; do
not rebind the old instance in place. The built-in backup/restore covers the
main application and configured databases, but not every ancillary service
volume, so review the backup scope below before a production migration.

For production Podman hosts, reboot persistence is a host responsibility.
`restart: always` needs Podman's restart integration to run after boot. A
typical rootless systemd setup is:

```bash
systemctl --user enable --now podman-restart.service
loginctl enable-linger "$USER"
```

For rootful Podman, enable the system `podman-restart.service` instead. Unit
availability varies by distribution; `easy-opal doctor` keeps this as an
explicit warning rather than modifying host login or systemd policy.

## Installation

```bash
# With uv (recommended)
uv tool install easy-opal

# With pipx
pipx install easy-opal

# With pip
pip install easy-opal
```

After installation, the `easy-opal` command is available from anywhere in your terminal.

## Your first deployment

The easiest way to get started is the interactive setup wizard. It walks you through every step:

```bash
easy-opal setup
```

The wizard will ask you for:

1. **Stack name** — identifies this deployment in the selected runtime (e.g., `my-opal`)
2. **Service versions** — Opal and MongoDB image tags (default: `latest`)
3. **SSL strategy** — how to handle HTTPS (self-signed for dev, Let's Encrypt for production)
4. **Databases** — optional PostgreSQL, MySQL, or MariaDB instances
5. **Automatic updates** — optional host-scheduled, health-aware image updates
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

`up` is convergent — it only recreates containers whose configuration has changed. It also waits for all Compose healthchecks to pass before returning, so when the command finishes, your services are ready to use.

To completely wipe everything and start fresh:

```bash
easy-opal reset --volumes --yes
```

## Automatic container image updates

Run an image update check manually with either supported runtime:

```bash
easy-opal -i production auto-update
easy-opal --runtime podman -i production auto-update --cleanup
```

The updater locks the instance and discovers the images used by its running
Compose services. It pulls mutable references before changing containers;
digest-pinned references remain unchanged. If the resolved image IDs are
unchanged, it leaves the containers running untouched. Otherwise it recreates
only the services that were already running, without starting their stopped
dependencies, and verifies both their image IDs and health state.
The complete generated Compose file is snapshotted before pulling. Apply and
rollback run against that private snapshot and abort if the live file changes,
so a concurrent configuration edit is never folded into an image update.

On a failed pull, easy-opal attempts to restore the previous local tags without
recreating containers. On a failed apply or verification, it retags the
previous image IDs, recreates the same running-service set, and verifies that
recovery. This is a compensating rollback of container image state, not an
atomic deployment or a data rollback: it cannot undo application writes,
database/schema migrations, external side effects, configuration changes, or
volume contents. Keep tested backups before enabling unattended production
updates. Cleanup only removes superseded image IDs after a successful update.

Unattended updates use a host-native timer, not a privileged container and not
a mounted engine socket:

```bash
easy-opal -i production config auto-updates enable --interval 24 --cleanup
easy-opal -i production config auto-updates status
easy-opal -i production config auto-updates disable
```

The `watchtower` configuration key and `config watchtower` alias are retained
for existing installations. Both CLI names control this easy-opal updater and
do not run the former
[`containrrr/watchtower`](https://github.com/containrrr/watchtower) container;
that integration was removed because the upstream project is archived and no
longer maintained. easy-opal installs a per-instance `systemd` timer on Linux
or `launchd` agent on macOS, pinned to the instance's Docker or Podman binding
and current runtime context. A scheduled run skips an intentionally stopped
stack rather than starting it.

The selected engine must be available when the timer runs; easy-opal does not
start Docker Desktop or a Podman machine on behalf of a scheduled job.
For a root-owned systemd schedule, preflight accepts only root-controlled
absolute executables/configuration paths and a local root-owned Unix engine
socket. Install rootless or remote-engine schedules as the owning user instead.

## Changing configuration

You don't need to re-run the setup wizard to change settings. Every aspect of the configuration can be modified individually. Container-facing changes regenerate the Compose file, NGINX config, SSL certificates, and proxy settings as needed; maintenance settings reconcile their host schedules.

```bash
# View current state
easy-opal config show
easy-opal config show-version
easy-opal config show-password

# Change service versions
easy-opal config change-version 7.0 --service mongo

# Change network and proxy settings
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

easy-opal can manage multiple independent Opal deployments on the same machine. Each instance has its own configuration, secrets, data, and Compose stack — completely isolated from each other.

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

Removing an instance always deletes its local instance directory, including
configuration, certificates, and backups stored there. Named container volumes
are preserved unless `--delete-data` is also supplied. Copy any needed backups
outside the instance directory first.

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

MongoDB is always included as Opal's metadata store. You can add additional databases for your data sources — PostgreSQL, MySQL, or MariaDB. They can be local (containers managed by easy-opal) or external (pointing to your own servers).

```bash
# Add during setup
easy-opal setup --database postgres:analytics:5432:opal --yes

# Add a specific version
easy-opal setup --database postgres:warehouse:5433:admin:16 --yes

# Remove a database and its container volume
easy-opal config remove-database analytics --delete-volume --yes
```

Format: `type:name:port:user[:version]`. All default to `latest`. Each database automatically gets its own named volume, healthcheck, and environment variables injected into Opal.

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

## Armadillo

Armadillo is a lightweight DataSHIELD server, an alternative to Opal. It stores data as Parquet files (no database needed) and optionally uses Keycloak for OIDC authentication.

```bash
# Interactive
easy-opal setup    # Choose "armadillo" as deployment type

# Non-interactive
easy-opal setup --flavor armadillo --stack-name my-armadillo --host localhost --yes

# With preset
easy-opal setup --preset armadillo-prod --host armadillo.example.com --yes

# Enable Keycloak authentication
easy-opal config keycloak enable
```

Armadillo uses the same Rock R server containers as Opal, so profile management works the same way.

## Application and database backup

Backups use native database tools inside the containers (`mongodump`,
`pg_dump`, `mysqldump`, or `mariadb-dump`). Each backup is a private `.tar.gz`
archive with a manifest describing its contents. A failed component makes the
command fail and no partial archive is published.

```bash
# Create an application/database backup
easy-opal backup create

# Save to a specific path
easy-opal backup create -o /backups/opal-2024-01.tar.gz

# List available backups
easy-opal backup list

# Restore from a backup
easy-opal backup restore backup.tar.gz
```

**What's included in a backup:**
- MongoDB dump and Opal server data (`/srv`) for the Opal flavor
- Armadillo application data (`/data`) for the Armadillo flavor
- PostgreSQL/MySQL/MariaDB dumps for internal containers managed by easy-opal
- Configuration file (`config.json`) as a reference; restore does not apply it
- Manifest with metadata (stack name, Opal version, timestamp)

**What's NOT included (by design):**
- Passwords and secrets (`secrets.env`) — never shipped in backups
- SSL certificates — regenerated on the target machine
- Generated Compose file — regenerated from config
- External PostgreSQL/MySQL/MariaDB databases
- Ancillary state such as Rock volumes, the Agate bind directory, or an
  Elasticsearch index

**Transferring to another machine:**

```bash
# On source machine
easy-opal backup create -o /tmp/my-backup.tar.gz

# Copy to target
scp /tmp/my-backup.tar.gz user@target:/tmp/

# On target machine (with easy-opal installed)
# First create the same flavor, databases, and relevant services as the source.
easy-opal --runtime podman setup --flavor opal --yes
easy-opal --runtime podman backup restore /tmp/my-backup.tar.gz
easy-opal --runtime podman restart
```

Use the archived `config.json` as a reference when preparing the target. The
target keeps its own stack name, ports, secrets, certificates, and runtime
binding. Migrate any ancillary state listed above separately when it matters to
your deployment.

Database dumps are made consistently by each native tool, but application data
and the different databases are captured one component after another; a backup
is not a globally coordinated point-in-time snapshot. Restore is destructive
and component-wise, with no global rollback if a later component fails. Before
the first mutation, easy-opal validates every payload and target mapping, then
checks that all target containers are running and provide the required tools;
archives exceeding 200,000 members or 50 GiB of declared payload are rejected
before extraction. This cannot prove that SQL or Mongo data is semantically
compatible. During recovery, keep those containers running but block external
traffic or put the applications in maintenance/read-only mode, then verify
every restored service before reopening access.

**Automated backups:** easy-opal schedules the same `backup create` operation
with the host's `systemd` or `launchd` manager. It invokes the supported Docker
or Podman runtime bound to the instance, runs as its selected user/context, and
does not mount an engine socket or add a sidecar to the Compose stack. A
scheduled run skips a stopped stack.

```bash
# Enable automated backups (every 24h, keep last 7)
easy-opal config backup enable --every 24 --keep 7

# Keep unlimited backups (use with caution)
easy-opal config backup enable --every 24 --keep 0

# Check status
easy-opal config backup status

# Change settings
easy-opal config backup --every 12 --keep 14

# Disable
easy-opal config backup disable
```

Retention is applied after a successful scheduled backup. Disabling the feature
removes its per-instance host schedule; existing archives are left intact.
Dump, copy, and restore duration is delegated to the selected engine and native
database tools; easy-opal does not impose an arbitrary timeout that could cut
off a legitimate large backup. Monitor long-running scheduled jobs and resolve
a hung runtime before retrying, because the instance lock remains held.

## Health and diagnostics

```bash
# Check stack health (containers, SSL, endpoints, databases)
easy-opal diagnose
easy-opal diagnose --quiet   # summary only

# Check easy-opal itself (runtime, config, secrets, permissions)
easy-opal doctor

# Validate config without starting anything
easy-opal validate

# Preview the generated Compose configuration
easy-opal plan

# Collect redacted diagnostics for sharing
easy-opal support-bundle
```

## Presets

Presets are named configuration templates for common deployment patterns. They set sensible defaults so you don't have to configure everything manually.

| Preset | What it configures |
|--------|-------------------|
| `opal-dev` | Self-signed SSL — for local development |
| `opal-prod` | Let's Encrypt SSL — for production servers |
| `opal-proxy` | No SSL — for deployments behind an external reverse proxy |
| `opal-agate` | Opal + Agate authentication + Mailpit |
| `obiba-full` | Opal + Agate + Mica + Elasticsearch — the full OBiBa stack |
| `armadillo-dev` | Armadillo DataSHIELD server for development |
| `armadillo-prod` | Armadillo + Keycloak OIDC for production |

```bash
easy-opal setup --preset opal-prod --host opal.example.com --yes
```

You can still override individual settings after applying a preset. Automatic
image updates remain opt-in in production presets so that enabling unattended
rollouts is an explicit operational decision.

## Rock profiles

Rock is the R server that Opal uses for statistical analysis and DataSHIELD operations. By default, one Rock profile is included with the base R packages. You can add more profiles with different R packages for specific use cases.

```bash
# List current profiles
easy-opal profile list

# Add a new profile
easy-opal profile add --image datashield/rock-omics --tag 2.0 --name rock-omics

# Remove a profile
easy-opal profile remove rock-omics --yes

# Change a profile's image tag (pulls new image and recreates container)
easy-opal profile change-version rock 6.3.4

# Refresh mutable tags like :latest (pull + recreate, no easy-opal restart needed)
easy-opal profile pull            # all profiles
easy-opal profile pull rock       # just one
easy-opal profile pull --no-apply # pull only, apply later with restart
```

### Scheduled background pre-pulling

If profiles use `:latest` and you want them refreshed automatically without
restarting your stack on every upstream push, enable the profile-updates
schedule. It pre-pulls new images into the selected Docker or Podman image store;
they become active only on your next `easy-opal restart`.

```bash
easy-opal config profile-updates enable --every 24
easy-opal config profile-updates status
easy-opal config profile-updates disable
```

This differs from `auto-update`: profile pre-pulling never recreates the stack.
Like automatic image updates and backups, it uses a per-instance `systemd` or
`launchd` job and no engine socket. All three schedules invoke the supported
Docker or Podman runtime bound to the instance and preserve its captured
runtime context; they fail rather than starting an unavailable engine or
Podman machine.

## Volumes

All named volumes are prefixed with the stack name to prevent collisions between instances. You can inspect and clean them up:

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

## Global runtime option

| Option | Description |
|--------|-------------|
| `--runtime auto\|docker\|podman` | Select the runtime before the command (default: `auto`) |

## All setup flags

| Flag | Description |
|------|-------------|
| `--stack-name TEXT` | Compose project name |
| `--host TEXT` | Hostname or IP address (repeatable) |
| `--port INT` | HTTPS port (443, or 8443 for a new restricted rootless Podman setup) |
| `--http-port INT` | HTTP port for `none` SSL strategy |
| `--ssl-strategy` | `self-signed`, `letsencrypt`, `manual`, `none` |
| `--password TEXT` | Admin password (auto-generated if not set) |
| `--opal-version TEXT` | Opal container image tag (default: `latest`) |
| `--mongo-version TEXT` | MongoDB container image tag (default: `latest`) |
| `--database TEXT` | `type:name:port:user[:version]` (repeatable) |
| `--flavor` | `opal` or `armadillo` |
| `--preset` | `opal-dev`, `opal-prod`, `opal-proxy`, `opal-agate`, `obiba-full`, `armadillo-dev`, `armadillo-prod` |
| `--auto-updates` | Enable host-scheduled automatic container updates |
| `--no-auto-updates` | Disable host-scheduled automatic updates explicitly |
| `--auto-update-interval INT` | Automatic update interval in hours (default: 24) |
| `--watchtower`, `--no-watchtower`, `--watchtower-interval` | Legacy aliases for the three automatic-update flags above |
| `--with-agate` | Enable Agate authentication server |
| `--with-mica` | Enable Mica data portal (implies Agate) |
| `--yes` | Skip all interactive prompts |

## Source code

easy-opal is open source under the MIT license. The codebase is modular: each service (Opal, MongoDB, NGINX, Rock, Agate, Mica, etc.) is a self-contained module. Contributions, issues, and feature requests are welcome.

**GitHub:** [https://github.com/isglobal-brge/easy-opal](https://github.com/isglobal-brge/easy-opal)

## Authors

- [David Sarrat González](https://davidsarratgonzalez.github.io)
- Juan R González

[Bioinformatic Research Group in Epidemiology (BRGE)](https://brge.isglobal.org), [Barcelona Institute for Global Health (ISGlobal)](https://www.isglobal.org)
