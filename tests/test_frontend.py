from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
HTML = STATIC / "index.html"
JAVASCRIPT = STATIC / "app.js"
STYLESHEET = STATIC / "app.css"


def css_rule(stylesheet: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", stylesheet)
    if match is None:
        raise AssertionError(f"Missing CSS rule for {selector}")
    return re.sub(r"\s+", " ", match.group(1))


class FrontendStyleTests(unittest.TestCase):
    def test_intro_waits_for_start_and_replay_skips_intro(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        welcome = re.search(
            r'<section id="welcome-view".*?</section>',
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(welcome, "The initial welcome screen must exist.")
        assert welcome is not None
        self.assertIn("Which photo do you prefer?", welcome.group(0))
        self.assertIn(
            "Choose one. Your vote helps shape the crowd ranking.",
            welcome.group(0),
        )
        self.assertIn('id="start-button"', welcome.group(0))
        self.assertIn('startButton.addEventListener("click", startGame)', javascript)
        self.assertIn('playAgainButton.addEventListener("click", startGame)', javascript)
        self.assertNotRegex(javascript, r"^\s*startGame\(\);\s*$")
        self.assertIn("if (requestInFlight) return;", javascript)

    def test_game_view_shows_only_progress_and_photo_choices(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        game = re.search(
            r'<section id="game-view".*?</section>',
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(game, "The arena view must exist.")
        assert game is not None
        self.assertIn('role="progressbar"', game.group(0))
        self.assertIn('aria-valuetext="Ready for round 1"', game.group(0))
        self.assertIn('id="choices"', game.group(0))
        self.assertNotIn("<h1", game.group(0))
        self.assertNotIn("supporting-copy", game.group(0))

    def test_arena_fills_viewport_with_top_and_bottom_choices(self) -> None:
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        shell = css_rule(stylesheet, "body.arena-active .app-shell")
        choices = css_rule(stylesheet, "body.arena-active .choices")
        self.assertIn("height: 100dvh;", shell)
        self.assertIn("overflow: hidden;", shell)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", choices)
        self.assertIn("grid-template-rows: repeat(2, minmax(0, 1fr));", choices)
        self.assertIn("gap: 2px;", choices)
        self.assertIn("body.arena-active .site-header", stylesheet)
        self.assertIn("body.arena-active .site-footer", stylesheet)

    def test_full_photo_stays_uncropped_over_a_full_bleed_backdrop(self) -> None:
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        image = css_rule(stylesheet, "body.arena-active .photo-choice__image")
        backdrop = css_rule(stylesheet, "body.arena-active .photo-choice__backdrop")
        frame = css_rule(stylesheet, "body.arena-active .photo-choice__frame")
        progress = css_rule(stylesheet, "body.arena-active .progress-fill")
        self.assertIn("object-fit: contain;", image)
        self.assertIn("object-fit: cover;", backdrop)
        self.assertIn('backdrop.className = "photo-choice__backdrop";', javascript)
        self.assertIn("border: 1px solid", frame)
        self.assertIn("background: #54e68a;", progress)


if __name__ == "__main__":
    unittest.main()
