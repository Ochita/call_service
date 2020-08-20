import argparse
import json
import logging
import os
import ssl

from aiohttp import web
from classes import Connection
from aiortc.contrib.media import MediaPlayer

ROOT = os.path.dirname(__file__)

logger = logging.getLogger("pc")
managers = set()


async def index(request):
    content = open(os.path.join(ROOT, "test.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "test.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    params = await request.json()
    manager = Connection()
    managers.add(manager)
    answer = await manager.get_answer(sdp=params["sdp"], type=params["type"])

    return web.Response(
        content_type="application/json",
        text=json.dumps(answer),
    )


async def mix(request):
    for man1 in managers:
        for man2 in managers:
            if man1 != man2:
                for track in man2.tracks:
                    print('tracks')
                    if track.kind == 'audio':
                        man1.audio.add_track(track)
                        print('add audio track')
                    if track.kind == 'video':
                        man1.video.add_track(track)
                        print('add video track')
    return web.Response(
        content_type="application/json",
        text=json.dumps({"success": "ok"}),
    )


async def play(request):
    player = MediaPlayer(os.path.join(ROOT, "savoy.mp3"))
    for man in managers:
        man.audio.add_track(player.audio)
    return web.Response(
        content_type="application/json",
        text=json.dumps({"success": "ok"}),
    )

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

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/test.js", javascript)
    app.router.add_post("/offer", offer)
    app.router.add_get("/mix", mix)
    app.router.add_get("/play", play)
    web.run_app(app, access_log=None, port=args.port, ssl_context=ssl_context)
