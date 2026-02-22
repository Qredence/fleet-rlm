import asyncio
import modal

app = modal.App.lookup("test-app", create_if_missing=True)


async def main():
    app = await modal.App.lookup.aio("test-app", create_if_missing=True)
    sb = await modal.Sandbox.create.aio(app=app)
    proc = await sb.exec.aio("python", "-u", "-c", "print('hello')", bufsize=1)

    # Check if we can use sync iter inside thread
    import threading
    import queue

    q = queue.Queue()
    iterator = iter(proc.stdout)

    def reader():
        try:
            for line in iterator:
                q.put(line)
        except Exception as e:
            q.put(e)

    t = threading.Thread(target=reader)
    t.start()
    t.join()

    while not q.empty():
        print("QUEUE:", q.get())

    await sb.terminate.aio()


asyncio.run(main())
