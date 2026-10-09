"""Healthcheck контейнера: поллер должен недавно обновлять heartbeat-файл.

Веб-сервера у бота нет, поэтому «жив» определяется по времени изменения файла.
"""
import os
import sys
import time

path = os.environ.get("HEARTBEAT_FILE", "/tmp/bot-heartbeat")
max_age = float(os.environ.get("HEALTHCHECK_MAX_AGE", "300"))

try:
    age = time.time() - os.stat(path).st_mtime
except OSError:
    sys.exit(1)

sys.exit(0 if age <= max_age else 1)
