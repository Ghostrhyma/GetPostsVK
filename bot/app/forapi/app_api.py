import aiohttp
from dotenv import load_dotenv
import os
import ssl, certifi
load_dotenv()

ssl_context = ssl.create_default_context(cafile=certifi.where())


async def get_last_post_from_group(domain: str):
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            async with session.get("https://api.vk.com/method/wall.get", params={
                "access_token": str(os.getenv("TOKEN")),
                "v": os.getenv("VERSION"),
                "domain": domain,
                "count": 2
            }) as response:
                data = await response.json()

                if data["response"]["items"][0].get("is_pinned") == None:
                    return data["response"]["items"][0]
                else:
                    return data["response"]["items"][1]
    except KeyError:
        return False


async def get_name_of_group(group_id: str):
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            async with session.get("https://api.vk.com/method/groups.getById", params={
                "access_token": str(os.getenv("TOKEN")),
                "v": os.getenv("VERSION"),
                "group_id": group_id
            }) as response:
                data = await response.json()
                return data["response"]["groups"][0]["name"]
    except Exception as e:
        print(f"Ошибка: {e}")