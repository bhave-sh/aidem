from __future__ import annotations

import hashlib
import json
import platform
import shutil
import stat
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .base import Runtime


class BinaryRuntime(Runtime):
    """Prebuilt-binary tool env: fetch a release asset into ``envs/<n>/bin``.

    For tools like rtk that ship single-binary releases, aidem downloads the
    platform-matching asset from GitHub Releases, extracts it, and drops the
    binary at ``~/.aidem/envs/<name>/bin/<binary>``. No host toolchain
    (Rust/Go/Node) is required; the env is fully isolated.

    Asset selection auto-heuristic: query the repo's latest release via the
    GitHub API and pick the asset whose name matches ``{os}-{arch}`` (rtk uses
    ``rtk-<arch>-<os>.<ext>``). Override with a manifest ``asset`` glob/substring.
    """

    name = "binary"

    # Map platform.system()/machine() to the substrings tool release assets use.
    _OS_TOKENS = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}
    _ARCH_TOKENS = {
        "arm64": "aarch64", "aarch64": "aarch64",
        "x86_64": "x86_64", "amd64": "x86_64",
        "x64": "x86_64",
    }

    def _platform_tokens(self) -> tuple[str, str]:
        os_tok = self._OS_TOKENS.get(platform.system(), platform.system().lower())
        machine = platform.machine().lower()
        arch_tok = self._ARCH_TOKENS.get(machine, machine)
        return os_tok, arch_tok

    def _latest_release_assets(self, source: str) -> list[dict]:
        """Assets from the latest release of the GitHub repo in ``source``."""
        owner_repo = self._owner_repo(source)
        if not owner_repo:
            return []
        url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "aidem", "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return data.get("assets", [])
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, ValueError):
            return []

    @staticmethod
    def _owner_repo(source: str) -> str | None:
        for host in ("github.com/", "github.com:"):
            if host in source:
                rest = source.split(host, 1)[1]
                rest = rest.split("/archive")[0].split(".git")[0]
                parts = rest.split("/")
                if len(parts) >= 2:
                    return f"{parts[0]}/{parts[1]}"
        return None

    def _pick_asset(self, assets: list[dict]) -> dict | None:
        os_tok, arch_tok = self._platform_tokens()
        asset_glob = self.meta.get("asset")
        candidates = []
        for a in assets:
            name = a.get("name", "")
            if asset_glob and asset_glob in name:
                return a
            if os_tok in name.lower() and arch_tok in name.lower():
                candidates.append(a)
        if candidates:
            # Prefer the shortest name (usually the canonical binary build).
            return sorted(candidates, key=lambda a: len(a.get("name", "")))[0]
        # Fallback: any single .tar.gz/.zip asset (some repos ship one universal asset).
        for a in assets:
            name = a.get("name", "").lower()
            if name.endswith((".tar.gz", ".tgz", ".zip")) and ".sig" not in name:
                return a
        return None

    def _download(self, url: str, dest: Path) -> None:
        # Only fetch over HTTPS from the same host the release API served
        # (github.com / objects.githubusercontent.com). Reject anything else so
        # a tampered API response can't redirect to an arbitrary host.
        parsed = urllib.request.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in (
            "github.com", "objects.githubusercontent.com", "codeload.github.com"
        ):
            raise RuntimeError(
                f"binary runtime: refusing to download from non-GitHub/non-HTTPS URL {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "aidem"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)

    @staticmethod
    def _safe_member_path(base: Path, member_name: str) -> Path | None:
        """Resolve a member name under base; None if it escapes (traversal/symlink)."""
        try:
            resolved = (base / member_name).resolve()
        except (OSError, ValueError):
            return None
        try:
            resolved.relative_to(base.resolve())
        except ValueError:
            return None
        return resolved

    def _extract(self, archive: Path, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        if archive.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive, "r:gz") as tf:
                # Prefer the data filter (3.12+) which strips unsafe members;
                # fall back to manual filtering on older runtimes.
                members = []
                for m in tf.getmembers():
                    # Reject absolute paths, traversal, and symlinks/hardlinks
                    # that escape the extraction dir.
                    if m.isdev() or m.issym() or m.islnk():
                        continue
                    if self._safe_member_path(out_dir, m.name) is None:
                        continue
                    members.append(m)
                tf.extractall(out_dir, members=members)
        elif archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if self._safe_member_path(out_dir, info.filename) is None:
                        continue
                    zf.extract(info, out_dir)
        else:
            # Treat as a raw binary; move it into out_dir.
            shutil.copy2(archive, out_dir / archive.name)

    def _find_binary(self, root: Path) -> Path | None:
        """Locate the executable inside the extracted tree by binary name.

        Matches by name only (not exec bit): the extracted file may not carry
        the executable bit yet; aidem chmods the final destination after move.
        """
        target = self.binary_name
        if not target:
            return None
        for p in root.rglob(target):
            if p.is_file():
                return p
        if platform.system() == "Windows":
            for p in root.rglob(f"{target}.exe"):
                if p.is_file():
                    return p
        return None

    def _verify_checksum_if_available(self, assets: list[dict], asset: dict,
                                      archive: Path) -> bool | None:
        """Verify the archive hash against a checksums asset if one ships.

        Returns True if verified, False on mismatch, None if no checksums asset
        was found (best-effort — not all releases ship one). Looks for a sibling
        asset named like ``checksums.txt`` / ``*.sha256`` / ``SHA256SUMS``.
        """
        checksums_text = None
        for a in assets:
            name = a.get("name", "").lower()
            if name in ("checksums.txt", "sha256sums", "sha256sums.txt") \
               or name.endswith(".sha256") or name.endswith(".sha256sums"):
                try:
                    buf = Path(archive.parent / a["name"])
                    self._download(a["browser_download_url"], buf)
                    checksums_text = buf.read_text()
                    buf.unlink(missing_ok=True)
                    break
                except Exception:
                    continue
        if not checksums_text:
            return None
        expected = self._expected_hash(checksums_text, asset["name"])
        if expected is None:
            return None
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        return actual.lower() == expected.lower()

    @staticmethod
    def _expected_hash(checksums_text: str, asset_name: str) -> str | None:
        """Extract the sha256 hash for asset_name from a checksums file."""
        for line in checksums_text.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                h, n = parts[0], parts[-1]
                if Path(n).name == asset_name and len(h) == 64:
                    return h
        return None

    def install(self, source: str | None = None) -> str:
        source = source or self.meta.get("source", "")
        assets = self._latest_release_assets(source)
        if not assets:
            raise RuntimeError(
                f"binary runtime: no release assets found for {source}. "
                f"Pass --asset <glob> or install the binary manually."
            )
        asset = self._pick_asset(assets)
        if asset is None:
            raise RuntimeError(
                f"binary runtime: no asset matching {self._platform_tokens()} "
                f"for {source}. Pass --asset <glob>."
            )
        bin_dir = self.env_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        archive = self.env_path / asset["name"]
        self._download(asset["browser_download_url"], archive)
        # Best-effort integrity check: if the release ships a checksums asset,
        # verify the downloaded archive against it before extracting+executing.
        checksum_ok = self._verify_checksum_if_available(assets, asset, archive)
        if checksum_ok is False:
            archive.unlink(missing_ok=True)
            raise RuntimeError(
                f"binary runtime: checksum verification failed for {asset['name']}. "
                f"Refusing to install a tampered asset.")
        self._extract(archive, self.env_path / "_extracted")
        found = self._find_binary(self.env_path / "_extracted")
        if found is None:
            raise RuntimeError(
                f"binary runtime: executable '{self.binary_name}' not found "
                f"in asset {asset['name']}."
            )
        dest = bin_dir / self.binary_name
        if dest.exists():
            dest.unlink()
        shutil.move(str(found), str(dest))
        if platform.system() != "Windows":
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        # Clean up archive + extraction scratch.
        shutil.rmtree(self.env_path / "_extracted", ignore_errors=True)
        archive.unlink(missing_ok=True)
        return f"downloaded {asset['name']} -> {dest}"

    def is_installed(self) -> bool:
        return self.resolve_binary() is not None
