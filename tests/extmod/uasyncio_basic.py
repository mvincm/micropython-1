try:
    import uasyncio as asyncio
except ImportError:
    try:
        import asyncio
    except ImportError:
        print('SKIP')
        raise SystemExit

import time
def ticks():
    return int(time.time() * 1000)

async def delay_print(t, s):
    await asyncio.sleep(t)
    print(s)

async def main():
    print('start')

    await asyncio.sleep(0.001)
    print('after sleep')

    t0 = ticks()
    await delay_print(0.02, 'short')
    t1 = ticks()
    await delay_print(0.04, 'long')
    t2 = ticks()

    print('took {} {}'.format(round(t1 - t0, -1), round(t2 - t1, -1)))

asyncio.run(main())
