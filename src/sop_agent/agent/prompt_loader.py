"""Load prompt templates from agent/prompts/ and fill named placeholders.

Only the placeholders passed in are replaced, so JSON braces in few-shot examples stay intact.
"""

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptNotFoundError(FileNotFoundError):
    pass


class MissingPlaceholderError(KeyError):
    """A placeholder the caller supplied doesn't appear in the template (a template/code mismatch)."""


def load_prompt(name: str, directory: Path = PROMPTS_DIR) -> str:
    path = directory / name
    if not path.is_file():
        raise PromptNotFoundError(f"prompt template not found: {name}")
    return path.read_text(encoding="utf-8").strip() + "\n"


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def render(template: str, **values: str) -> str:
    """Replace each `{name}` for the given names only, in one pass.

    One pass means a value that itself contains `{phase}` (e.g. caller-influenced text) is never re-expanded.
    """
    present = set(_PLACEHOLDER.findall(template))
    missing = set(values) - present
    if missing:
        raise MissingPlaceholderError(", ".join(sorted(missing)))
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)
