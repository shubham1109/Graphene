"""Mongo connection lifecycle and index setup."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.config import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


class DatabaseUnavailable(RuntimeError):
    """Raised with actionable guidance when MongoDB cannot be reached."""


def _redact(uri: str) -> str:
    """Hide credentials so the URI is safe to print in an error."""
    if "@" not in uri:
        return uri
    scheme, _, rest = uri.partition("://")
    _credentials, _, host = rest.partition("@")
    return f"{scheme}://***:***@{host}"


async def connect() -> AsyncIOMotorDatabase:
    global _client, _db
    settings = get_settings()
    _client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    _db = _client[settings.mongodb_db]
    try:
        await ensure_indexes(_db)
    except PyMongoError as exc:
        # The raw driver traceback is ~60 frames of internals and buries the one
        # thing that matters, which is almost always that MONGODB_URI is unset
        # or nothing is listening there.
        raise DatabaseUnavailable(
            f"Could not reach MongoDB at {_redact(settings.mongodb_uri)}\n"
            f"  ({type(exc).__name__}: {str(exc).splitlines()[0][:200]})\n\n"
            "Fix one of these:\n"
            "  1. Set MONGODB_URI in backend/.env to a running server\n"
            "     (Atlas: mongodb+srv://user:pass@cluster.mongodb.net/)\n"
            "  2. Start a local server:  brew services start mongodb-community\n"
            "  3. Run without any database, for development only:\n"
            "     python -m scripts.dev_mock_server --demo"
        ) from exc
    return _db


async def disconnect() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client, _db = None, None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialised; call connect() during startup.")
    return _db


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index("email", unique=True)
    await db.samples.create_index([("user_id", 1), ("created_at", -1)])
    await db.spectra.create_index([("sample_id", 1), ("technique", 1)])
    await db.analysis_reports.create_index([("sample_id", 1), ("created_at", -1)])
    await db.analysis_reports.create_index([("user_id", 1), ("created_at", -1)])
    await db.reference_spectra.create_index([("technique", 1), ("material_class", 1)])
    await db.reference_properties.create_index("material")
    await db.commercial_products.create_index([("form", 1), ("producer", 1)])
    await db.applications.create_index("key", unique=True)
    await db.application_taxonomy.create_index("key", unique=True)
    await db.application_products.create_index("key", unique=True)
    await db.application_products.create_index("application_tags")
    await db.market_reference.create_index("key", unique=True)
    await db.tds_matches.create_index([("user_id", 1), ("created_at", -1)])
