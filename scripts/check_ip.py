"""Quick check: what's the public IP that Upstox sees from this machine?"""
import asyncio
import httpx


async def main() -> None:
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in services:
            try:
                r = await client.get(url)
                print(f"{url}: {r.text.strip()}")
            except Exception as e:
                print(f"{url}: ERROR {e}")


if __name__ == "__main__":
    asyncio.run(main())
