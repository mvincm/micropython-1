try:
    import uasyncio as asyncio
except ImportError:
    try:
        import asyncio
    except ImportError:
        print('SKIP')
        raise SystemExit

import micropython

async def task(id, n, t):
    for i in range(n):
        print(id, i)
        await asyncio.sleep_ms(t)

async def main():
    t1 = asyncio.create_task(task(1, 4, 10))
    t2 = asyncio.create_task(task(2, 4, 25))

    micropython.heap_lock()

    print('start')
    await asyncio.sleep_ms(1)
    print('sleep')
    await asyncio.sleep_ms(100)
    print('finish')

    micropython.heap_unlock()

asyncio.run(main())
