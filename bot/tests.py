import aiohttp, ssl, certifi, os, asyncio

ssl_context = ssl.create_default_context(cafile=certifi.where())

async def get_video_info(owner_id, video_id, access_key, token):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        async with session.get("https://api.vk.com/method/video.get", params={
            "videos": f"{owner_id}_{video_id}_{access_key}",
            "access_token": token,
            "v": "5.199"
        }) as resp:
            data = await resp.json()
            print(data)
            if "response" in data:
                item = data["response"]["items"][0]
                return {
                    "title": item.get("title"),
                    "player": item.get("player"),
                    "views": item.get("views"),
                    "duration": item.get("duration")
                }


# video_url = f"https://vk.com/video{video['owner_id']}_{video['id']}"
# https://vk.com/video{owner_id}_{video_id}?access_key={access_key}

async def main():
    resp = await get_video_info(video_id=456302001, owner_id=-167127847, access_key="1b5e36e6286ba517b6", token="2d697bfc2d697bfc2d697bfccd2e52b9f022d692d697bfc459b1d260b559457501b08c8")
    print(resp)

if __name__ == "__main__":
    asyncio.run(main())