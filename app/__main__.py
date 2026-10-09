import asyncio
import sys

from .config import ConfigError
from .runner import run
from dotenv import load_dotenv

load_dotenv()  # Загружает переменные из .env в os.environ

def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
