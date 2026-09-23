from __future__ import annotations

import sqlite3
import unittest

from app.service import PhotosArenaService, ServiceError

from tests.helpers import PHOTO_IDS, AppFixture


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = AppFixture()
        self.service = self.fixture.service

    def tearDown(self) -> None:
        self.fixture.close()

    def test_five_round_flow_records_history_and_returns_global_ranking(self) -> None:
        game = self.service.start_game()
        self.assertEqual((game["round"], game["total_rounds"]), (1, 5))
        for expected_round in range(1, 6):
            self.assertEqual(game["round"], expected_round)
            selected = game["choices"][0]["id"]
            response = self.service.submit_vote(game["ballot_id"], selected)
            if expected_round < 5:
                game = response
                self.assertEqual(game["round"], expected_round + 1)
            else:
                self.assertTrue(response["completed"])
                self.assertEqual(len(response["ranking"]), 5)
        connection = sqlite3.connect(self.fixture.database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM votes").fetchone()[0], 5)
        finally:
            connection.close()

    def test_same_choice_retry_returns_same_round_without_duplicate_vote(self) -> None:
        game = self.service.start_game()
        choice_id = game["choices"][0]["id"]
        next_round = self.service.submit_vote(game["ballot_id"], choice_id)
        retry_result = self.service.submit_vote(game["ballot_id"], choice_id)
        self.assertEqual(retry_result, next_round)

        connection = sqlite3.connect(self.fixture.database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM votes").fetchone()[0], 1)
        finally:
            connection.close()

    def test_consumed_ballot_cannot_change_vote(self) -> None:
        game = self.service.start_game()
        winner, alternate = (choice["id"] for choice in game["choices"])
        self.service.submit_vote(game["ballot_id"], winner)
        with self.assertRaises(ServiceError) as raised:
            self.service.submit_vote(game["ballot_id"], alternate)
        self.assertEqual(raised.exception.status, 409)

    def test_rejects_choice_not_shown_for_ballot(self) -> None:
        game = self.service.start_game()
        with self.assertRaises(ServiceError) as raised:
            self.service.submit_vote(game["ballot_id"], "f" * 64)
        self.assertEqual(raised.exception.status, 400)

    def test_rating_is_recomputed_from_vote_history_and_persists(self) -> None:
        first, second, third = PHOTO_IDS[:3]
        connection = sqlite3.connect(self.fixture.database_path)
        try:
            connection.execute(
                "INSERT INTO games(id,total_rounds,created_at) VALUES ('g',5,0)"
            )
            connection.execute(
                "INSERT INTO game_rounds(game_id,round_number,left_photo_id,right_photo_id) "
                "VALUES ('g',1,?,?)",
                (first, second),
            )
            connection.execute(
                "INSERT INTO ballots(id,token_hash,game_id,round_number,created_at,consumed_at) "
                "VALUES ('b1','hash','g',1,0,1)"
            )
            connection.execute(
                "INSERT INTO votes("
                "id, ballot_id, game_id, round_number, winner_photo_id, loser_photo_id, cast_at) "
                "VALUES ('v1','b1','g',1,?,?,1)",
                (first, second),
            )
            connection.execute(
                "INSERT INTO game_rounds(game_id,round_number,left_photo_id,right_photo_id) "
                "VALUES ('g',2,?,?)",
                (first, third),
            )
            connection.execute(
                "INSERT INTO ballots(id,token_hash,game_id,round_number,created_at,consumed_at) "
                "VALUES ('b2','hash2','g',2,0,1)"
            )
            connection.execute(
                "INSERT INTO votes("
                "id, ballot_id, game_id, round_number, winner_photo_id, loser_photo_id, cast_at) "
                "VALUES ('v2','b2','g',2,?,?,2)",
                (first, third),
            )
            connection.commit()
        finally:
            connection.close()

        ranking = self.service.ranking()
        self.assertEqual(ranking[0]["id"], first)
        self.assertEqual(ranking[0]["votes"], 2)
        reopened = PhotosArenaService(
            self.fixture.database_path,
            "https://photos.example.test",
            "ballot-secret-for-tests-at-least-32-chars",
        )
        self.assertEqual(reopened.ranking(), ranking)

    def test_ballot_secret_must_be_strong_enough(self) -> None:
        with self.assertRaises(ValueError):
            PhotosArenaService(self.fixture.database_path, "https://photos.example.test", "short")


if __name__ == "__main__":
    unittest.main()
