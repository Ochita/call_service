import argparse
import asyncio
import json
import logging
import os
import ssl
import uuid
import uvloop
import multiprocessing
import queue

from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder

ROOT = os.path.dirname(__file__)

logger = logging.getLogger("pc")
pcs = set()


async def index(request):
    content = open(os.path.join(ROOT, "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    params = await request.json()

    tx, rx = multiprocessing.Queue(), multiprocessing.Queue()

    tx.put_nowait(params)

    p = multiprocessing.Process(
        target=spawn_pc,
        args=(tx, rx)
    )

    pcs.add(p)

    p.start()

    # todo change
    while True:
        try:
            result = rx.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.1)
        else:
            break

    asyncio.get_event_loop().create_task(wait_kill(rx, p))

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            result
        ),
    )


async def wait_kill(rx, p):
    while True:
        try:
            result = rx.get_nowait()
            if result == "kill":
                await asyncio.sleep(0.1)
                p.terminate()
                p.join()
                print("killed")
                break
        except queue.Empty:
            await asyncio.sleep(0.1)


async def on_shutdown(app):
    # kill processes
    [pc.terminate() for pc in pcs]
    [pc.join() for pc in pcs]


async def config_pc(tx, rx, end_event):
    params = tx.get_nowait()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pc_id = "PeerConnection(%s)" % uuid.uuid4()

    def log_info(msg, *args):
        logger.info(pc_id + " " + msg, *args)

    # prepare local media
    player = MediaPlayer(os.path.join(ROOT, "Space Unicorn.mp3"))
    if args.write_audio:
        recorder = MediaRecorder(args.write_audio)
    else:
        recorder = MediaBlackhole()

    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            if isinstance(message, str) and message.startswith("ping"):
                channel.send("pong" + message[4:])

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        log_info("ICE connection state is %s", pc.iceConnectionState)
        if pc.iceConnectionState == "failed":
            await pc.close()

    @pc.on("track")
    def on_track(track):
        log_info("Track %s received", track.kind)

        if track.kind == "audio":
            pc.addTrack(player.audio)
            recorder.addTrack(track)
        elif track.kind == "video":
            local_video = track
            pc.addTrack(local_video)

        @track.on("ended")
        async def on_ended():
            log_info("Track %s ended", track.kind)
            await recorder.stop()
            await pc.close()
            rx.put_nowait("kill")
            end_event.set()

    # handle offer
    await pc.setRemoteDescription(offer)
    await recorder.start()

    # send answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    rx.put_nowait({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def waiter(event):
    print('waiting for it ...')
    await event.wait()
    print('... got it!')


def spawn_pc(tx, rx):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    end_event = asyncio.Event()
    loop.create_task(config_pc(tx, rx, end_event))
    loop.run_until_complete(waiter(end_event))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="WebRTC audio / video / data-channels demo"
    )
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for HTTP server (default: 8080)"
    )
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument("--write-audio", help="Write received audio to a file")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.cert_file:
        ssl_context = ssl.SSLContext()
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    else:
        ssl_context = None

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    web.run_app(app, access_log=None, port=args.port, ssl_context=ssl_context)
