# adapters/macos/lib/handlers/docker.sh
# Docker Desktop handler.

docker_check() {
    local slug="$1" cfg="$2"
    /usr/bin/command -v docker >/dev/null 2>&1 || return 0
    docker desktop version 2>/dev/null \
        | /usr/bin/grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
        | /usr/bin/head -n 1
}

docker_apply() {
    local slug="$1" cfg="$2"
    /usr/bin/command -v docker >/dev/null 2>&1 || return 31
    docker desktop update --quiet
}
