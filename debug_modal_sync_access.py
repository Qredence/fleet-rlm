import asyncio
import modal

app = modal.App.lookup("test-app", create_if_missing=True)


async def main():
    app = await modal.App.lookup.aio("test-app", create_if_missing=True)
    sb = await modal.Sandbox.create.aio(app=app)
    proc = await sb.exec.aio(
        "python", "-u", "-c", "import sys; print(sys.stdin.read())", bufsize=1
    )

    import threading

    def writer():
        try:
            proc.stdin.write(b"hello via sync write\n")
            proc.stdin.drain()
            proc.stdin.write_eof()
        except Exception as e:
            print("ERROR IN WRITER:", e)

    t = threading.Thread(target=writer)
    t.start()
    t.join()

    out = ""
    async for line in proc.stdout:
        out += line
    print("STDOUT:", out.strip())

    await sb.terminate.aio()


asyncio.run(main())
