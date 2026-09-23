"""Deterministic Elo standings derived from immutable vote history."""

from __future__ import annotations

import math
import sqlite3

from app.catalog import image_url

INITIAL_RATING = 1000.0
K_FACTOR = 32.0


def calculate_ranking(
    connection: sqlite3.Connection, photo_base_url: str
) -> list[dict[str, int | float | str]]:
    photos = connection.execute("SELECT id, object_key, active FROM photos").fetchall()
    ratings = {row["id"]: INITIAL_RATING for row in photos}
    wins = dict.fromkeys(ratings, 0)
    losses = dict.fromkeys(ratings, 0)
    history = connection.execute(
        "SELECT winner_photo_id, loser_photo_id FROM votes "
        "ORDER BY cast_at, id"
    ).fetchall()

    for vote in history:
        winner = vote["winner_photo_id"]
        loser = vote["loser_photo_id"]
        winner_expected = 1 / (1 + math.pow(10, (ratings[loser] - ratings[winner]) / 400))
        loser_expected = 1 - winner_expected
        old_winner = ratings[winner]
        old_loser = ratings[loser]
        ratings[winner] = old_winner + K_FACTOR * (1 - winner_expected)
        ratings[loser] = old_loser + K_FACTOR * (0 - loser_expected)
        wins[winner] += 1
        losses[loser] += 1

    active_photos = [photo for photo in photos if photo["active"]]
    return [
        {
            "id": row["id"],
            "image_url": image_url(row["object_key"], photo_base_url),
            "rating": round(ratings[row["id"]]),
            "votes": wins[row["id"]],
            "comparisons": wins[row["id"]] + losses[row["id"]],
        }
        for row in sorted(
            active_photos,
            key=lambda photo: (
                -ratings[photo["id"]],
                -wins[photo["id"]],
                photo["id"],
            ),
        )
    ]
