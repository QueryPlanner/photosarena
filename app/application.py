"""Application construction and persistent image catalog initialization."""

from app.catalog import synchronize_catalog
from app.database import connect, migrate
from app.service import PhotosArenaService
from app.settings import Settings


def create_service(settings: Settings) -> PhotosArenaService:
    connection = connect(settings.database_path)
    try:
        migrate(connection)
        synchronize_catalog(connection, settings.manifest_path)
    finally:
        connection.close()
    return PhotosArenaService(
        settings.database_path,
        settings.photo_base_url,
        settings.ballot_secret,
    )
