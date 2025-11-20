import json, os
import aiofiles
import requests
from dotenv import load_dotenv


load_dotenv()

def get_last_post_from_group():
    response = requests.get("https://api.vk.ru/method/wall.get",params={
        "access_token": os.getenv("TOKEN"),
        "v": os.getenv("VERSION"),
        "domain": os.getenv("DOMAIN"),
        "count": 1
    })

    return response.json()["response"]["items"][0]



async def read_from_json(filename: str) -> list:
    try:
        async with aiofiles.open(filename, "r", encoding="utf-8") as r_json:
            data = await r_json.read()
            # print(data)
            return json.loads(data)
    except FileNotFoundError:
        await write_in_json(filename, [])
        return []
    except Exception as e:
        print(f"read_from_json --- Ошибка: {e}")
        return []


async def write_in_json(filename: str, data):
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        if not os.path.exists(filename):
            async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                await f.write("[]") 

        async with aiofiles.open(filename, "w", encoding="utf-8") as w_json:
            await w_json.write(json.dumps(data, ensure_ascii=False, indent=4))
    except Exception as e:
        print(f"write_in_json --- Ошибка: {e}")


async def delete_chat_from_json(filename: str, key: str, value: str):
    try:
        from_json = await read_from_json(filename=filename)
        for index, item in enumerate(from_json):
            if item[f"{key}"] == value:
                del from_json[index]
            else:
                pass

        await write_in_json(filename=filename, data=from_json)
    except Exception as e:
        print(f"delete_from_json --- Ошибка: {e}")


async def get_last_post_from_json(filename, last_post):
    from_file = await read_from_json(filename=filename)
    last_data = last_post
    flag = False
    if not from_file:
        await add_to_json(filename, last_data)
        flag = True
        print("Возвращаем пост из паблика")
        return (last_data, flag) 
    elif from_file[-1]["id"] != last_data["id"]:
        checkPostInFile = False
        for item_file in from_file:
            if item_file["id"] == last_data["id"]:
                checkPostInFile = True
        
        if not checkPostInFile:
            await add_to_json(filename, last_data)
            flag = True
            print("Возвращаем пост из паблика")
            return (last_data, flag)
        else:
            print("Пост был найден в истории постов")
            return (from_file[-1], flag)
                
    elif from_file[-1]["id"] == last_data["id"]:
        print("Возвращаем пост из файла")
        return (from_file[-1], flag)
    # else:
    #     add_to_json("C:/Projects/VK_API_ProvCom/bot/app/forapi/posts.json", last_data)
    #     flag = True
    #     return (last_data, flag)    

async def add_to_json(filename: str, data: dict):
    try:
        if os.path.getsize(filename) != 0:
            from_file_data = await read_from_json(filename)
            if data not in from_file_data:
                from_file_data.append(data)
            
            await write_in_json(filename, from_file_data)
        else:
            # new_data = []
            # new_data.append(data)
            await write_in_json(filename, data)
    except Exception as e:
        print(f"Ошибка: {e}")     