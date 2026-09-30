"""本地无 Docker 时跑测试用：内存 SQLite。生产仍使用 settings.py 的 PostgreSQL。"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
