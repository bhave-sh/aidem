from pathlib import Path
import tomllib


ROOT = Path(__file__).parents[1]


def _packages():
    with (ROOT / "uv.lock").open("rb") as lockfile:
        return {package["name"]: package for package in tomllib.load(lockfile)["package"]}


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".") if part.isdigit())


def test_lockfile_project_version_matches_pyproject():
    with (ROOT / "pyproject.toml").open("rb") as project:
        project_version = tomllib.load(project)["project"]["version"]

    assert _packages()["aidem"]["version"] == project_version


def test_cryptography_is_patched_for_pkcs7_advisory():
    cryptography = _packages()["cryptography"]

    assert _version(cryptography["version"]) >= (50, 0, 0)
