# Changelog

All notable changes to easy-opal are documented in this file.

## [2.4.0] - 2026-07-31

### Added

- Interactive and non-interactive runtime selection through
  `easy-opal runtime select`, plus `easy-opal runtime status` for inspecting
  the effective Docker or Podman choice.
- Runtime selection in the interactive setup flow for new, unbound instances.
- A real Docker-to-Podman-to-Docker backup and restore portability smoke test
  for the documented core application and database state.

### Changed

- The saved runtime preference is now a soft, host-local default: explicit
  invocation options take precedence, while existing instance bindings remain
  authoritative and cannot be changed accidentally.
- Runtime detection for configured legacy instances avoids rebinding data to a
  different engine when both Docker and Podman are available.
- Registry updates now use cross-process locking and atomic replacement.
- Instance list and info output report the runtime for unconfigured instances.

### Compatibility and migration

- `runtime select` changes the default for future unbound work only; it does not
  migrate or rebind existing instances.
- Built-in cross-runtime backup and restore covers the documented core state,
  not every ancillary service volume. Review those volumes separately before a
  production migration.

## [2.3.0] - 2026-07-31

### Added

- First-class Docker and Podman runtime selection through `--runtime`,
  `EASY_OPAL_RUNTIME`, and per-instance runtime bindings. Only the selected
  engine and its Compose provider are required.
- Host-native automatic image updates with health verification, rollback, and
  optional image cleanup, without mounting an engine socket in a container.
- Host-native `systemd` and `launchd` scheduling for image updates, backups,
  and profile image pulls.
- Docker and isolated Podman Compose smoke tests in CI.

### Changed

- Replaced the Watchtower, backup, and profile-updater sidecars with commands
  that operate through the runtime bound to each instance.
- Hardened backup and restore preflight, archive validation, ownership repair,
  database handling, and component rollback.
- Hardened ACME/Nginx state restoration, atomic secret writes, support-bundle
  redaction, configuration rollback, and generated-file permissions.
- Qualified public images with their registry so Podman does not depend on
  host-specific short-name resolution.

### Compatibility and migration

- Docker remains supported; existing instances keep their recorded runtime and
  are not silently moved when another engine is installed.
- Podman requires Podman 4.6 or newer and the independent `podman-compose`
  provider 1.6.0 or newer. Rootless privileged-port restrictions still apply.
- Existing Watchtower configuration names remain accepted for compatibility,
  while unattended production updates remain opt-in.
- Scheduled jobs require the selected Docker daemon or Podman machine to be
  available; easy-opal does not start the engine automatically.
- Backups cover the documented application and database state, not every
  ancillary service volume; review the migration scope before moving a
  production deployment between runtimes.

[2.4.0]: https://github.com/isglobal-brge/easy-opal/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/isglobal-brge/easy-opal/compare/v2.2.0...v2.3.0
