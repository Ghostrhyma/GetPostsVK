from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv
import asyncio, os, logging, sys
from aiohttp import web
from aiogram.types.bot_command_scope_all_chat_administrators import BotCommandScopeAllChatAdministrators

from app.handlers import router

from app.background_task import post_checker
from app.database.models import async_main

    
load_dotenv()

bot = Bot(token=str(os.getenv("BOT_TOKEN")))
dp = Dispatcher()
dp.include_router(router)

async def commands_list():
    commands = [
        BotCommand(command="start", description="Подключение бота к чату"),
        BotCommand(command="push_domain", description="Подключение группы по ссылке"),
        BotCommand(command="delete_domain", description="Отключение группы от чата")
    ]
    await bot.set_my_commands(commands=commands, scope=BotCommandScopeAllChatAdministrators())


async def on_startup(bot):
    # Запуск фоновой задачи
    asyncio.create_task(post_checker(bot))
    print("-- Фоновая задача запущена вместе с ботом")


async def handle(request):
    return web.Response(text="Bot is alive")

async def main():
    await async_main()
    app = web.Application()
    app.router.add_get("/", handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    print("✅ Bot started and server is running...")
    
    await commands_list()
    
    await on_startup(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.warning("Program killed")