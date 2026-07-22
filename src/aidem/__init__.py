"""aidem: AI development environment manager.

Layers:
  0. Registry  -- git clone skill/tool repos and (optionally) install binaries via uv.
  1. Bridging  -- one-time dir symlinks from each IDE's skills dir into ~/.aidem/skills;
                  plus repo init (a single committed AGENTS.md).
  2. Execution -- pass-through run of registered tools in isolated uv environments.

User data (skills, registry, manifest) lives in ~/.aidem (overridable via
AIDEM_DATA_DIR). Shipped package assets (generators, overlays, canonical
AGENTS.md) travel with the install and are read-only.
"""

__version__ = "0.1.0"