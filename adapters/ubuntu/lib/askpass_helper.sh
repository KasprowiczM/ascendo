#!/usr/bin/env bash
# askpass_helper.sh — SUDO_ASKPASS helper for the Ubuntu adapter.
#
# When sudo is invoked with `-A` (or with `SUDO_ASKPASS` set in the
# environment), it spawns this script and reads the password from its
# stdout. We echo the value of the ephemeral environment variable
# `_ASCENDO_SUDO_PW`, which the Ubuntu adapter's LinuxElevation
# explicitly injects into the child's env only for the apply phase
# (and only when a password has been registered via the dashboard's
# /elevation/auth endpoint).
#
# Security:
#   - The password lives in process memory only — never written to disk
#     (this script is static; the secret is the env var, which is
#     exposed only to the sudo subprocess and torn down with it).
#   - We never log the password; we never echo prompts to stderr.
#   - Returns exit 1 when the env var is missing/empty so sudo treats
#     the attempt as a failed authentication instead of accepting an
#     empty password.
#
# Cross-OS note: this mirrors the macOS adapter's
# `_create_askpass_helper` pattern but takes a different shape — macOS
# embeds the password directly in a per-instance temp script; Ubuntu
# uses a single static script + per-spawn env var. Both endpoints
# satisfy the IElevation contract; the dashboard wires SUDO_ASKPASS
# regardless of which technique the OS uses.

set -u

if [ -z "${_ASCENDO_SUDO_PW:-}" ]; then
    # Nothing to feed sudo. Exit non-zero so sudo treats this as a
    # failed authentication attempt instead of accepting an empty pw.
    exit 1
fi

printf '%s\n' "${_ASCENDO_SUDO_PW}"
