"""Org MCP server config distribution.

Org content repos of kind "mcp" contribute MCP server definitions; the
client merges them into each tool's global MCP config under names prefixed
``<org>__`` so user-defined entries are never touched. Re-sync is
idempotent: delete every ``<org>__`` key, insert the current set, write
back. Logout is the same algorithm with an empty set.

Ownership rule: aidem only ever writes keys carrying its ``<org>__``
prefix. An unparseable existing config is skipped with an error — never
clobbered. A tool is skipped when its config dir is absent (tool not
installed), matching Generator._ensure_dir_symlink's convention.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import _strip_jsonc_comments


class McpBridge:
    """Merge/clear an org's MCP entries across every tool's global config."""

    def __init__(self, org: str):
        self.org = org
        self.prefix = f"{org}__"

    # -- per-tool target table -------------------------------------------
    # (tool, config path, top-level key, "installed" indicator)
    def _targets(self) -> list[tuple[str, Path, str, Path]]:
        home = Path.home()
        return [
            ("kilo", home / ".config" / "kilo" / "mcp_settings.json",
             "mcpServers", home / ".config" / "kilo"),
            ("cursor", home / ".cursor" / "mcp.json",
             "mcpServers", home / ".cursor"),
            ("claude", home / ".claude.json",
             "mcpServers", home / ".claude"),
            ("opencode", home / ".config" / "opencode" / "opencode.json",
             "mcp", home / ".config" / "opencode"),
        ]

    @staticmethod
    def _translate_opencode(entry: dict) -> dict:
        """Translate the canonical MCP entry shape into OpenCode's shape."""
        if "url" in entry:
            out = {"type": "remote", "url": entry["url"], "enabled": True}
            if entry.get("headers"):
                out["headers"] = entry["headers"]
            return out
        command = [entry.get("command", "")] + list(entry.get("args", []))
        out = {"type": "local", "command": command, "enabled": True}
        if entry.get("env"):
            out["environment"] = entry["env"]
        return out

    def _apply_to_file(self, tool: str, config_path: Path, top_key: str,
                       entries: dict[str, dict]) -> tuple[str, str]:
        config: dict = {}
        if config_path.exists():
            try:
                config = json.loads(_strip_jsonc_comments(config_path.read_text()))
                if not isinstance(config, dict):
                    raise ValueError("config root is not an object")
            except (json.JSONDecodeError, ValueError, OSError):
                return ("error", f"{tool}: {config_path} is unreadable (invalid "
                                 "JSON/JSONC); refusing to clobber it.")
        section = config.setdefault(top_key, {})
        if not isinstance(section, dict):
            return ("error", f"{tool}: {config_path} key '{top_key}' is not an "
                             "object; refusing to clobber it.")
        for key in [k for k in section if k.startswith(self.prefix)]:
            del section[key]
        for name, entry in entries.items():
            if tool == "opencode":
                section[f"{self.prefix}{name}"] = self._translate_opencode(entry)
            else:
                section[f"{self.prefix}{name}"] = entry
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        verb = "cleared" if not entries else "wrote"
        return ("ok", f"{tool}: {verb} {len(entries)} org MCP entrie(s) "
                      f"in {config_path}")

    def apply(self, entries: dict[str, dict]) -> list[tuple[str, str]]:
        """Write the org's MCP entries into every installed tool's config.

        `entries` maps unprefixed server names to their canonical definition
        ({"command", "args", "env"} or {"url", "headers"}); the <org>__
        prefix is added here. Env values (including ${...} references) pass
        through verbatim. Returns (status, message) per tool.
        """
        results: list[tuple[str, str]] = []
        for tool, config_path, top_key, indicator in self._targets():
            if not indicator.exists() and not config_path.exists():
                results.append(("skipped", f"{tool}: {indicator} not found "
                                           "(tool not installed)"))
                continue
            config_path.parent.mkdir(parents=True, exist_ok=True)
            results.append(self._apply_to_file(tool, config_path, top_key, entries))
        results.append(("skipped", "windsurf: MCP bridge not supported yet"))
        return results

    def clear(self) -> list[tuple[str, str]]:
        """Remove every <org>__ MCP entry from all tool configs."""
        return self.apply({})
