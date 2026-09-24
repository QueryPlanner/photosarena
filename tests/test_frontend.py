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
        self.assertIn("gap: 0;", choices)
        self.assertIn("body.arena-active .site-header", stylesheet)
        self.assertIn("body.arena-active .site-footer", stylesheet)

    def test_full_photo_stays_uncropped_over_a_full_bleed_backdrop(self) -> None:
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        image = css_rule(stylesheet, "body.arena-active .photo-choice__image")
        backdrop = css_rule(stylesheet, "body.arena-active .photo-choice__backdrop")
        progress = css_rule(stylesheet, "body.arena-active .progress-fill")
        self.assertIn("object-fit: contain;", image)
        self.assertIn("object-fit: cover;", backdrop)
        self.assertIn('backdrop.className = "photo-choice__backdrop";', javascript)
        self.assertIn("background: #54e68a;", progress)

    def test_borderless_photos_keep_a_visible_pressed_and_focus_state(self) -> None:
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        face = css_rule(stylesheet, "body.arena-active .photo-choice__face")
        pressed = css_rule(
            stylesheet,
            "body.arena-active .photo-choice.is-selected .photo-choice__face",
        )
        focus = css_rule(stylesheet, "body.arena-active .photo-choice:focus-visible")
        self.assertNotIn("photo-choice__frame", javascript)
        self.assertNotIn("photo-choice__frame", stylesheet)
        self.assertIn("border: 0;", face)
        self.assertIn("transform: scale(0.985);", pressed)
        self.assertIn("filter: brightness(0.78)", pressed)
        self.assertIn("outline: 4px solid #54e68a;", focus)

    def test_lightning_divider_is_decorative_and_does_not_split_the_grid(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        svg = re.search(r'<svg\s+class="arena-lightning".*?</svg>', html, flags=re.DOTALL)
        self.assertIsNotNone(svg, "The arena needs a lightning divider.")
        assert svg is not None
        self.assertIn('aria-hidden="true"', svg.group(0))
        self.assertIn('focusable="false"', svg.group(0))
        self.assertIn('id="arena-lightning-trace"', svg.group(0))
        self.assertEqual(svg.group(0).count('href="#arena-lightning-trace"'), 3)
        lightning = css_rule(stylesheet, "body.arena-active .arena-lightning")
        choices = css_rule(stylesheet, "body.arena-active .choices")
        self.assertIn("position: absolute;", lightning)
        self.assertIn("pointer-events: none;", lightning)
        self.assertIn("gap: 0;", choices)

    def test_confetti_runs_only_when_results_render_and_respects_reduced_motion(self) -> None:
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        render_results = re.search(
            r"function renderResults\(results\) \{.*?\n\}", javascript, flags=re.DOTALL
        )
        render_game = re.search(
            r"function renderGame\(game\) \{.*?\n\}", javascript, flags=re.DOTALL
        )
        start_game = re.search(
            r"async function startGame\(\) \{.*?\n\}", javascript, flags=re.DOTALL
        )
        self.assertIsNotNone(render_results)
        self.assertIsNotNone(render_game)
        self.assertIsNotNone(start_game)
        assert render_results is not None and render_game is not None and start_game is not None
        self.assertIn("celebrateRanking();", render_results.group(0))
        self.assertNotIn("celebrateRanking();", render_game.group(0))
        self.assertIn("clearCelebration();", start_game.group(0))
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)")', javascript)
        self.assertIn('piece.addEventListener("animationend"', javascript)
        self.assertIn("window.setTimeout(clearCelebration, 1900)", javascript)
        self.assertIn("@keyframes confetti-fall", stylesheet)
        self.assertIn("@keyframes confetti-reduced", stylesheet)

    def test_vote_loader_waits_briefly_for_photo_press_feedback(self) -> None:
        javascript = JAVASCRIPT.read_text(encoding="utf-8")
        submit_vote = re.search(
            r"async function submitPendingVote\(\) \{.*?\n\}", javascript, flags=re.DOTALL
        )
        self.assertIsNotNone(submit_vote)
        assert submit_vote is not None
        body = submit_vote.group(0)
        self.assertIn("}, 150);", body)
        self.assertIn("const pressFeedback = new Promise", body)
        self.assertEqual(body.count("await pressFeedback;"), 2)
        self.assertIn("if (requestInFlight) show(loadingView);", body)
        self.assertIn("window.clearTimeout(loadingTimer);", body)
        self.assertLess(body.index("window.setTimeout"), body.index('requestJson("/api/votes"'))


if __name__ == "__main__":
    unittest.main()
