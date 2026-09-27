"""Render the summary email to text and HTML with Jinja2. HTML is autoescaped."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEXT_TEMPLATE = "summary.txt.j2"
HTML_TEMPLATE = "summary.html.j2"

_ENV = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def render_email(content: dict[str, Any], claim_numbers: list[str]) -> tuple[str, str]:
    """(text, html) for the email content; unknown template variables raise instead of rendering blank."""
    values = {**content, "claim_numbers": claim_numbers}
    return _ENV.get_template(TEXT_TEMPLATE).render(values), _ENV.get_template(HTML_TEMPLATE).render(values)
