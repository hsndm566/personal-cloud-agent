"""Minimal version-controlled skill selection with fail-closed permissions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    keywords: frozenset[str]
    allowed_tools: frozenset[str]


class SkillRouter(Protocol):
    def select(self, goal: str, project_context: str = "") -> list[Skill]: ...


class MarkdownSkillRouter:
    """Load small Markdown skill cards and select by explicit keyword overlap."""

    def __init__(self, directory: Path):
        self._skills = tuple(self._read_skill(path) for path in sorted(directory.glob("*.md")))

    def select(self, goal: str, project_context: str = "") -> list[Skill]:
        tokens = _tokens(f"{goal} {project_context}")
        ranked = sorted(
            ((len(tokens & skill.keywords), skill.name, skill) for skill in self._skills),
            key=lambda item: (-item[0], item[1]),
        )
        return [skill for score, _, skill in ranked if score > 0]

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    @staticmethod
    def _read_skill(path: Path) -> Skill:
        lines = path.read_text(encoding="utf-8").splitlines()
        metadata: dict[str, str] = {}
        body: list[str] = []
        in_frontmatter = False
        for line in lines:
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter and ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
            elif not in_frontmatter:
                body.append(line)
        name = metadata.get("name", path.stem)
        description = metadata.get("description", " ".join(body).strip())
        keywords = frozenset(_tokens(metadata.get("keywords", f"{name} {description}")))
        allowed_tools = frozenset(
            item.strip() for item in metadata.get("allowed_tools", "").split(",") if item.strip()
        )
        return Skill(name, description, keywords, allowed_tools)


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in value.replace("/", " ").split() if len(token) > 2}
