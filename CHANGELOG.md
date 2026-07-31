# Changelog

All notable changes to easy-opal are documented in this file.

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

[2.3.0]: https://github.com/isglobal-brge/easy-opal/compare/v2.2.0...v2.3.0
