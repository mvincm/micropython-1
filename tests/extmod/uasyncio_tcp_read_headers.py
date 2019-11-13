try:
    import uasyncio as asyncio
except ImportError:
    try:
        import asyncio
    except ImportError:
        print('SKIP')
        raise SystemExit

async def http_get_headers(url):
    reader, writer = await asyncio.open_connection(url, 80)

    print('Request GET')
    writer.write(b'GET / HTTP/1.0\r\n\r\n')
    await writer.drain()

    while True:
        line = await reader.readline()
        if not line:
            break
        if line.find(b'Date') == -1 and line.find(b'Modified') == -1:
            print(line.strip())

    print('Close the connection')
    writer.close()
    await writer.wait_closed()

asyncio.run(http_get_headers('micropython.org'))
