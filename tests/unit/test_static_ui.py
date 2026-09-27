"""Static checks on the test UI (SPEC §13.2). Browser behavior is checked manually; see the M6 report."""

import re
from pathlib import Path

import pytest

STATIC = Path("src/sop_agent/web/static")
FILES = {name: (STATIC / name).read_text(encoding="utf-8") for name in ("index.html", "app.js", "styles.css")}
PALETTE = {"--paper": "#F4F7F8", "--ink": "#1C2B33", "--harbor": "#1F5C70", "--mist": "#DCE8EC", "--signal": "#B7791F", "--ok": "#2F7D5B", "--stop": "#B0413E"}  # fmt: skip


def test_no_build_tooling_and_plain_module() -> None:
    assert not list(STATIC.glob("package*.json"))
    assert '<script type="module" src="app.js"></script>' in FILES["index.html"]
    assert "import " not in FILES["app.js"]  # a single self-contained module


def test_palette_and_type_follow_the_design_direction() -> None:
    for name, value in PALETTE.items():
        assert f"{name}: {value};" in FILES["styles.css"]
    assert '"Public Sans"' in FILES["styles.css"] and "Public+Sans" in FILES["index.html"]


def test_reduced_motion_disables_the_only_animation() -> None:
    css = FILES["styles.css"]
    assert css.count("transition:") == 2  # the shackle, and its reduced-motion override
    assert re.search(r"prefers-reduced-motion: reduce\)\s*\{\s*\.lock \.shackle \{ transition: none; \}", css)


def test_layout_has_three_columns_and_a_narrow_screen_mode() -> None:
    css = FILES["styles.css"]
    assert "grid-template-columns: minmax(200px, 240px) minmax(0, 1fr) minmax(300px, 380px);" in css
    assert "@media (max-width: 1199px)" in css and "@media (max-width: 480px)" in css


def test_server_data_is_never_inserted_as_html() -> None:
    js = FILES["app.js"]
    assert not re.search(r"\.(innerHTML|outerHTML)\s*=", js) and "insertAdjacentHTML(" not in js
    assert "document.write" not in js


def test_api_key_is_never_persisted_in_the_browser() -> None:
    assert "localStorage" not in FILES["app.js"] and "sessionStorage" not in FILES["app.js"]
    assert 'type="password"' in FILES["index.html"]


def test_keyboard_and_accessibility_basics() -> None:
    html, css = FILES["index.html"], FILES["styles.css"]
    assert ":focus-visible" in css and "skip-link" in html
    assert 'role="log"' in html and 'aria-live="polite"' in html
    assert html.count('role="tab"') == 3 and "ArrowRight" in FILES["app.js"]
    assert '<label for="message"' in html and '<html lang="en">' in html


@pytest.mark.parametrize("name", list(FILES))
def test_copy_avoids_arrows_and_middle_dots(name: str) -> None:
    for forbidden in ("\N{RIGHTWARDS ARROW}", "\N{MIDDLE DOT}"):
        assert forbidden not in FILES[name]


@pytest.mark.parametrize("name", list(FILES))
def test_no_fixture_data_in_the_ui(name: str) -> None:
    for token in ("Margaret", "POL-9921", "CL-2048", "4472", "Morgan"):
        assert token not in FILES[name]
