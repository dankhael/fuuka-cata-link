import os

# Ensure a dummy bot token is set for tests so config.py doesn't fail
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
