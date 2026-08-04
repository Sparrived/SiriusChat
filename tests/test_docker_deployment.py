from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_dockerfile_reuses_the_complete_environment_before_application_source():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim AS environment" in dockerfile
    assert "ARG SIRIUS_BROWSER_CACHE_IMAGE=browser-empty" in dockerfile
    assert "FROM ${SIRIUS_BROWSER_CACHE_IMAGE} AS browser-cache" in dockerfile
    assert "COPY --from=browser-cache /ms-playwright/ /ms-playwright/" in dockerfile
    assert "FROM ${SIRIUS_ENV_CACHE_IMAGE} AS runtime" in dockerfile
    runtime = dockerfile[dockerfile.index("FROM ${SIRIUS_ENV_CACHE_IMAGE} AS runtime"):]
    assert "COPY pyproject.toml uv.lock README.md ./" in runtime
    assert "uv sync --frozen --no-dev --no-install-project" in runtime
    assert dockerfile.index("playwright install --with-deps chromium") < dockerfile.index(
        "sirius_pulse ./sirius_pulse"
    )
    assert "chown sirius:sirius /app" in dockerfile
    assert dockerfile.index(
        "rm -rf /app/sirius_pulse /app/sirius_pulse.egg-info"
    ) < dockerfile.index("sirius_pulse ./sirius_pulse")


def test_update_script_refuses_to_replace_an_unmigrated_container_data_directory():
    script = (ROOT / "scripts" / "update-container.sh").read_text(encoding="utf-8")

    assert "docker container inspect sirius-pulse-v2-test" in script
    assert "docker image inspect sirius-pulse:latest" in script
    assert "export SIRIUS_ENV_CACHE_KEY=" in script
    assert "export SIRIUS_BROWSER_CACHE_IMAGE=browser-empty" in script
    assert "export SIRIUS_BROWSER_CACHE_IMAGE=sirius-pulse:latest" in script
    assert "test -d /ms-playwright" in script
    assert "export SIRIUS_ENV_CACHE_IMAGE=sirius-pulse:latest" in script
    assert script.index("test -d /ms-playwright") < script.index("docker compose up -d")
    assert '\\"org.sirius-pulse.environment-cache-key\\"' not in script
    assert "exit 2" in script
    assert "systemctl restart sirius-container-admin" in script
    assert script.index("docker compose config -q") < script.index("docker compose up -d")


def test_update_script_restores_persistent_system_package_manifests():
    script = (ROOT / "scripts" / "update-container.sh").read_text(encoding="utf-8")

    assert "data/runtime-packages/apt.txt" in script
    assert "data/runtime-packages/yum.txt" in script
    assert "docker compose exec -T --user root sirius-pulse" in script
    assert "apt-get update" in script
    assert "apt-get install -y --no-install-recommends" in script
    assert "yum install -y" in script
    assert "无效的系统包名" in script
    assert script.index("docker compose up -d") < script.index("if ! restore_system_packages")


def test_deployment_guide_uses_the_single_update_script_path():
    guide = (ROOT / "docs" / "guide" / "docker-deployment.md").read_text(encoding="utf-8")

    assert "bash /root/SiriusPulse/scripts/update-container.sh" in guide
