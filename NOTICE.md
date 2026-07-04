# Third-Party Licenses

aidem depends on the following open-source packages. All are permissive licenses with no copyleft or commercial-use restrictions.

## Direct Dependencies

### click
- **License**: BSD-3-Clause
- **Copyright**: 2014 Pallets
- **URL**: https://click.palletsprojects.com/
- **Use**: CLI framework for command parsing and help generation.

### Jinja2
- **License**: BSD-3-Clause
- **Copyright**: 2007 Pallets
- **URL**: https://jinja.palletsprojects.com/
- **Use**: Template engine for configuration file generation.

### MarkupSafe (transitive, via Jinja2)
- **License**: BSD-3-Clause
- **Copyright**: 2010 Pallets
- **URL**: https://pypi.org/project/MarkupSafe/
- **Use**: String escaping for safe template rendering.

## Build Dependencies

### hatchling
- **License**: MIT
- **Copyright**: 2017-2024 Ofek Lev
- **URL**: https://hatch.pypa.io/
- **Use**: Build backend for packaging aidem.

## Runtime Tools (invoked via subprocess)

### uv
- **License**: MIT
- **Copyright**: 2024 Astral Software, Inc.
- **URL**: https://github.com/astral-sh/uv
- **Use**: Package and environment manager for Python tool isolation.

### git
- **License**: GPL v2
- **Copyright**: 2005-2024 Linus Torvalds et al.
- **URL**: https://git-scm.com/
- **Use**: Version control for managing tool submodules.

## Registry Tools

Tools added to aidem via `aidem registry add` are third-party software with
their own licenses. aidem does not distribute, modify, or relicense these
tools — it clones their repositories and, where applicable, installs them
into isolated `uv tool` environments.

When you register a tool, you are responsible for verifying that its license
is compatible with your project's licensing requirements before using it in
commercial or distribution contexts.

aidem does not ship skills or tools — it ships the infrastructure to manage
them.
