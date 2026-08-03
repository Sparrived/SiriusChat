#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -d data ]]; then
  if docker container inspect sirius-pulse-v2-test >/dev/null 2>&1; then
    echo "检测到旧容器但缺少 ./data，拒绝更新以保护持久化数据。请先按部署指南完成迁移。" >&2
    exit 2
  fi
  install -d -m 700 -o 10001 -g 10001 data
fi
install -d -m 700 -o 10001 -g 10001 data/runtime-packages

restore_system_packages() {
  local package_manager manifest install_command
  if [[ -s data/runtime-packages/apt.txt || -s data/runtime-packages/yum.txt ]]; then
    package_manager="$(docker compose exec -T --user root sirius-pulse sh -c '
      if command -v apt-get >/dev/null 2>&1; then
        printf apt
      elif command -v yum >/dev/null 2>&1; then
        printf yum
      else
        printf none
      fi
    ')"
    case "$package_manager" in
      apt)
        manifest=data/runtime-packages/apt.txt
        install_command='DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends'
        ;;
      yum)
        manifest=data/runtime-packages/yum.txt
        install_command='yum install -y'
        ;;
      *)
        echo "容器没有可用的 apt-get 或 yum，无法恢复系统包。" >&2
        return 1
        ;;
    esac
    [[ -s "$manifest" ]] || return 0

    echo "正在恢复系统包清单: $manifest"
    docker compose exec -T --user root sirius-pulse sh -c "
      set -eu
      packages=\$(mktemp)
      trap 'rm -f \"\$packages\"' EXIT
      while IFS= read -r package || [ -n \"\$package\" ]; do
        case \"\$package\" in
          ''|\#*) continue ;;
          -*|*[!A-Za-z0-9._+:+~=]*)
            echo \"无效的系统包名: \$package\" >&2
            exit 2
            ;;
        esac
        printf '%s\\n' \"\$package\" >> \"\$packages\"
      done
      [ -s \"\$packages\" ] || exit 0
      $([[ "$package_manager" == apt ]] && printf '%s' 'apt-get update' || printf '%s' ':')
      set -- \$(cat \"\$packages\")
      $install_command \"\$@\"
    " < "$manifest"
  fi
}

git pull --ff-only origin master
git submodule update --init --recursive
docker compose config -q
export SIRIUS_ENV_CACHE_KEY="$(sha256sum Dockerfile | awk '{print $1}')"
unset SIRIUS_ENV_CACHE_IMAGE
if docker image inspect sirius-pulse:latest >/dev/null 2>&1; then
  current_environment_key="$(docker image inspect --format '{{ index .Config.Labels "org.sirius-pulse.environment-cache-key" }}' sirius-pulse:latest)"
  current_lock_hash="$(docker run --rm --entrypoint sha256sum sirius-pulse:latest /app/uv.lock 2>/dev/null | awk '{print $1}' || true)"
  if [[ ( -z "$current_environment_key" || "$current_environment_key" == "<no value>" || "$current_environment_key" == "$SIRIUS_ENV_CACHE_KEY" ) \
    && "$(sha256sum uv.lock | awk '{print $1}')" == "$current_lock_hash" ]]; then
    export SIRIUS_ENV_CACHE_IMAGE=sirius-pulse:latest
  fi
fi
docker compose up -d --build --force-recreate --remove-orphans

for _ in {1..60}; do
  if curl -fsS http://127.0.0.1:8080/ >/dev/null \
    && curl -fsS http://127.0.0.1:18900/health >/dev/null; then
    if ! restore_system_packages; then
      docker compose ps
      docker compose logs --tail=100
      exit 1
    fi
    docker compose ps
    exit 0
  fi
  sleep 2
done

docker compose ps
docker compose logs --tail=100
exit 1
