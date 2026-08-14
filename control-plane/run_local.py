"""Local host-run launcher: load deploy/.env with python-dotenv (quotes intact),
override DATABASE_URL/ports, then exec uvicorn. Avoids bash sourcing mangling the
CONTROL_PLANE_ROLE_TOKENS_JSON quoting."""
import os
import sys

from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = dotenv_values(os.path.join(ROOT, "deploy", ".env"))
os.environ.update({k: v for k, v in env.items() if v is not None})

os.environ["DATABASE_URL"] = "postgresql+psycopg://" + os.environ["POSTGRES_USER"] + ":" + os.environ["POSTGRES_PASSWORD"] + "@127.0.0.1:5433/control_plane"
os.environ.setdefault("QUALITY_API_BASE_URL", "http://127.0.0.1:8080")
os.environ.setdefault("NOTIFICATION_ADAPTER", "disabled")
os.environ.setdefault("REQUIRE_MCP_ROLE_TOKENS", "true")

args = [".venv/bin/uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18090"] + sys.argv[1:]
os.execvp(args[0], args)
