from flask_sqlalchemy import SQLAlchemy
from flask import Flask
import os
from dotenv import load_dotenv
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
import urllib.parse

base_dir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(base_dir, ".env"))
load_dotenv()

app = Flask(__name__)

# Allow all origins for mobile apps, physical devices, local web, and Cloudflare tunnels
_cors_origins_env = os.getenv("CORS_ORIGINS", "*")
if _cors_origins_env == "*":
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
else:
    _allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    CORS(app, origins=_allowed_origins, supports_credentials=True)

# Rate limiting to prevent brute-force attacks on auth endpoints.
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],           # no default — apply explicitly per route
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)

# ─────────────────────────────────────────────
#  PRIMARY DB: PostgreSQL (if DATABASE_URL set) or SQLite fallback
# ─────────────────────────────────────────────
_sqlite_file = os.getenv("SQLITE_PATH", "aurestra.db")
if not os.path.isabs(_sqlite_file):
    _sqlite_file = os.path.join(base_dir, _sqlite_file)

SQLITE_URI = f"sqlite:///{_sqlite_file}"

env_db_url = os.getenv("DATABASE_URL")
if env_db_url:
    PRIMARY_DB_URI = env_db_url.replace("postgres://", "postgresql://", 1)
    print(f"✅ [Database] Primary: PostgreSQL ({PRIMARY_DB_URI.split('@')[-1]})")
else:
    PRIMARY_DB_URI = SQLITE_URI
    print(f"✅ [Database] Primary: SQLite ({_sqlite_file})")

# ─────────────────────────────────────────────
#  SECONDARY DB: PostgreSQL  (backup target)
# ─────────────────────────────────────────────
DB_ENGINE   = os.getenv("DB_ENGINE", "postgresql")
DB_USER     = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST     = os.getenv("DB_HOST", "")
DB_PORT     = os.getenv("DB_PORT", "5432")
DB_NAME     = os.getenv("DB_NAME", "finance")

# Also honour legacy MYSQL_* env names so nothing breaks
if not DB_USER:
    DB_USER = os.getenv("MYSQL_USER")
    if DB_USER:
        DB_PASSWORD = DB_PASSWORD or os.getenv("MYSQL_PASSWORD", "")
        DB_HOST     = os.getenv("MYSQL_HOST", "")
        DB_PORT     = os.getenv("MYSQL_PORT", "5432")
        DB_NAME     = os.getenv("MYSQL_DB", "finance")


def _build_postgres_uri() -> str | None:
    """Build the PostgreSQL URI for the secondary (backup) connection. Returns None if not configured."""
    database_url = os.getenv("DATABASE_URL", "")
    if database_url and "PASSWORD@" not in database_url:
        return database_url.replace("postgres://", "postgresql://", 1)

    if not DB_USER or not DB_PASSWORD or DB_PASSWORD == "PASSWORD" or DB_PASSWORD == "change_this":
        return None

    pw_safe = urllib.parse.quote_plus(DB_PASSWORD) if DB_PASSWORD else ""

    if DB_HOST:
        uri = f"postgresql+psycopg2://{DB_USER}:{pw_safe}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        print(f"✅ [Database] Secondary: PostgreSQL ({DB_HOST}:{DB_PORT}/{DB_NAME})")
    else:
        uri = f"postgresql+psycopg2://{DB_USER}@/{DB_NAME}"
        print(f"✅ [Database] Secondary: PostgreSQL (unix socket / peer auth → {DB_NAME})")
    return uri


POSTGRES_URI = _build_postgres_uri()

if not POSTGRES_URI:
    print("⚠️  [Database] No PostgreSQL config found — backup-to-PG will be skipped.")

# ─────────────────────────────────────────────
#  Flask / SQLAlchemy
# ─────────────────────────────────────────────
app.config["SQLALCHEMY_DATABASE_URI"] = PRIMARY_DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Expose PostgreSQL as a named bind so models can optionally target it
if POSTGRES_URI:
    app.config["SQLALCHEMY_BINDS"] = {"postgres": POSTGRES_URI}

db = SQLAlchemy(app)

# ─────────────────────────────────────────────
#  Raw engine helpers (used by backup_manager)
# ─────────────────────────────────────────────
_sqlite_engine = create_engine(
    SQLITE_URI,
    connect_args={"check_same_thread": False},
)
_SQLiteSession = scoped_session(sessionmaker(bind=_sqlite_engine))


def get_sqlite_engine():
    """Returns the raw SQLite engine (for pandas.read_sql, backups, etc.)."""
    return _sqlite_engine


def get_sqlite_session():
    """Scoped session bound to SQLite — useful in background workers."""
    return _SQLiteSession


_pg_engine = None
if POSTGRES_URI:
    try:
        _pg_engine = create_engine(POSTGRES_URI, pool_pre_ping=True)
        print("✅ [Database] PostgreSQL engine created (backup target).")
    except Exception as _e:
        print(f"⚠️  [Database] Could not create PostgreSQL engine: {_e}")


def get_postgres_engine():
    """Returns the raw PostgreSQL engine, or None if PG is not configured."""
    return _pg_engine
