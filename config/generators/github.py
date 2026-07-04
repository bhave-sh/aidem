from .base import Generator


class GitHubGenerator(Generator):
    """GitHub Copilot has no global skills path (repo-level only).

    Skills doc: https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills
    Repo-level: .github/copilot-instructions.md, .github/instructions/*.instructions.md
    """

    name = "github"
    passthrough = True

    # global_path stays None -> ensure_bridge reports skipped.
