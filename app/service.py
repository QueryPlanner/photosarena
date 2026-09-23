"""Game and vote operations, kept independent of HTTP transport."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from itertools import combinations

from app.catalog import image_url
from app.database import connect
from app.ranking import calculate_ranking

TOTAL_ROUNDS = 5
BALLOT_TTL_SECONDS = 24 * 60 * 60


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class PhotosArenaService:
    def __init__(
        self,
        database_path: str,
        photo_base_url: str,
        ballot_secret: str,
    ) -> None:
        if len(ballot_secret.encode("utf-8")) < 32:
            raise ValueError("BALLOT_SECRET must contain at least 32 bytes.")
        self.database_path = database_path
        self.photo_base_url = photo_base_url
        self.ballot_secret = ballot_secret.encode("utf-8")

    def start_game(self) -> dict[str, object]:
        connection = connect(self.database_path)
        try:
            photos = connection.execute(
                "SELECT id, object_key FROM photos WHERE active=1 ORDER BY id"
            ).fetchall()
            if len(photos) < 2:
                raise ServiceError(503, "At least two photos are needed to play.")

            pairs = list(combinations(photos, 2))
            secrets.SystemRandom().shuffle(pairs)
            selected_pairs = [pairs[index % len(pairs)] for index in range(TOTAL_ROUNDS)]
            game_id = str(uuid.uuid4())
            now = int(time.time())
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO games(id, total_rounds, created_at) VALUES (?, ?, ?)",
                (game_id, TOTAL_ROUNDS, now),
            )
            for round_number, pair in enumerate(selected_pairs, start=1):
                first, second = pair
                if secrets.randbits(1):
                    first, second = second, first
                connection.execute(
                    "INSERT INTO game_rounds(game_id, round_number, "
                    "left_photo_id, right_photo_id) VALUES (?, ?, ?, ?)",
                    (game_id, round_number, first["id"], second["id"]),
                )
            first_token = secrets.token_urlsafe(32)
            self._insert_ballot(connection, game_id, 1, first_token, now)
            connection.commit()
            return self._game_payload(connection, game_id, 1, first_token)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def submit_vote(self, ballot_token: str, choice_id: str) -> dict[str, object]:
        if not isinstance(ballot_token, str) or not 32 <= len(ballot_token) <= 256:
            raise ServiceError(400, "The ballot is invalid.")
        if not isinstance(choice_id, str) or len(choice_id) > 128:
            raise ServiceError(400, "The selected photo is invalid.")

        token_hash = hashlib.sha256(ballot_token.encode("utf-8")).hexdigest()
        connection = connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            ballot = connection.execute(
                "SELECT b.id AS ballot_row_id, b.game_id, b.round_number, "
                "b.created_at, b.consumed_at, r.left_photo_id, r.right_photo_id, "
                "g.total_rounds FROM ballots b "
                "JOIN game_rounds r USING (game_id, round_number) "
                "JOIN games g ON g.id=b.game_id WHERE b.token_hash=?",
                (token_hash,),
            ).fetchone()
            if ballot is None:
                raise ServiceError(404, "This ballot is not valid.")
            if choice_id not in (ballot["left_photo_id"], ballot["right_photo_id"]):
                raise ServiceError(400, "Choose one of the two photos shown.")
            if ballot["consumed_at"] is not None:
                prior_vote = connection.execute(
                    "SELECT winner_photo_id FROM votes WHERE ballot_id=?",
                    (ballot["ballot_row_id"],),
                ).fetchone()
                if prior_vote is None or prior_vote["winner_photo_id"] != choice_id:
                    raise ServiceError(409, "This ballot has already been used.")
                result = self._response_after_round(
                    connection,
                    ballot["game_id"],
                    ballot["round_number"],
                    ballot["total_rounds"],
                    ballot_token,
                )
                connection.commit()
                return result
            if int(time.time()) - ballot["created_at"] > BALLOT_TTL_SECONDS:
                raise ServiceError(410, "This ballot has expired. Start a new game.")

            loser_id = (
                ballot["right_photo_id"]
                if choice_id == ballot["left_photo_id"]
                else ballot["left_photo_id"]
            )
            now = int(time.time())
            connection.execute(
                "UPDATE ballots SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (now, ballot["ballot_row_id"]),
            )
            connection.execute(
                "INSERT INTO votes(id, ballot_id, game_id, round_number, "
                "winner_photo_id, loser_photo_id, cast_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    ballot["ballot_row_id"],
                    ballot["game_id"],
                    ballot["round_number"],
                    choice_id,
                    loser_id,
                    now,
                ),
            )
            result = self._response_after_round(
                connection,
                ballot["game_id"],
                ballot["round_number"],
                ballot["total_rounds"],
                ballot_token,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ranking(self) -> list[dict[str, int | float | str]]:
        connection = connect(self.database_path)
        try:
            return calculate_ranking(connection, self.photo_base_url)
        finally:
            connection.close()

    @staticmethod
    def _insert_ballot(
        connection: sqlite3.Connection,
        game_id: str,
        round_number: int,
        token: str,
        now: int,
    ) -> None:
        connection.execute(
            "INSERT INTO ballots(id, token_hash, game_id, round_number, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                hashlib.sha256(token.encode("utf-8")).hexdigest(),
                game_id,
                round_number,
                now,
            ),
        )

    def _response_after_round(
        self,
        connection: sqlite3.Connection,
        game_id: str,
        round_number: int,
        total_rounds: int,
        parent_token: str,
    ) -> dict[str, object]:
        next_round = round_number + 1
        if next_round > total_rounds:
            return {
                "completed": True,
                "ranking": calculate_ranking(connection, self.photo_base_url),
            }

        next_token = self._derive_next_token(parent_token, game_id, next_round)
        existing = connection.execute(
            "SELECT token_hash FROM ballots WHERE game_id=? AND round_number=?",
            (game_id, next_round),
        ).fetchone()
        expected_hash = hashlib.sha256(next_token.encode("utf-8")).hexdigest()
        if existing is None:
            self._insert_ballot(connection, game_id, next_round, next_token, int(time.time()))
        elif not hmac.compare_digest(existing["token_hash"], expected_hash):
            raise RuntimeError("The next round ballot did not match its signed token.")
        return self._game_payload(connection, game_id, next_round, next_token)

    def _game_payload(
        self, connection: sqlite3.Connection, game_id: str, round_number: int, token: str
    ) -> dict[str, object]:
        row = connection.execute(
            "SELECT r.left_photo_id, r.right_photo_id, p1.object_key AS left_key, "
            "p2.object_key AS right_key FROM game_rounds r "
            "JOIN photos p1 ON p1.id=r.left_photo_id "
            "JOIN photos p2 ON p2.id=r.right_photo_id "
            "WHERE r.game_id=? AND r.round_number=?",
            (game_id, round_number),
        ).fetchone()
        return {
            "game_id": game_id,
            "round": round_number,
            "total_rounds": TOTAL_ROUNDS,
            "ballot_id": token,
            "choices": [
                {
                    "id": row["left_photo_id"],
                    "image_url": image_url(row["left_key"], self.photo_base_url),
                },
                {
                    "id": row["right_photo_id"],
                    "image_url": image_url(row["right_key"], self.photo_base_url),
                },
            ],
        }

    def _derive_next_token(self, previous_token: str, game_id: str, round_number: int) -> str:
        message = f"{previous_token}\0{game_id}\0{round_number}".encode()
        digest = hmac.new(self.ballot_secret, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
