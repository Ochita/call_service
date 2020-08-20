import argparse
import asyncio
import json
import logging
import os
import ssl
import uuid
import uvloop

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCRtpTransceiver
from aiortc.contrib.media import MediaRecorder
import time

ROOT = os.path.dirname(__file__)

logger = logging.getLogger('pc')
pcs = dict()
traks = dict()

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

events = [asyncio.Event(), asyncio.Event()]


class ConnectionGroup(object):
    def __init__(self, uid, users):
        self.uid = uid
        self.call_begin = None
        self.full = asyncio.Event()
        self.tracks = dict((k, []) for k in users)
        self.recorder = MediaRecorder(str(uid) + str(users) + '.mp3')
        self.future = None

    def check_user(self, user_id):
        return user_id in self.tracks.keys()

    async def add_track(self, user_id, track):
        if not self.call_begin:
            if asyncio.isfuture(self.future):
                self.future.cancel()
            self.tracks[user_id].append(track)
            if track.kind == 'audio':
                self.recorder.addTrack(track)
            for t in self.tracks:
                if len(t) < 1:
                    break
            else:
                self.future = asyncio.create_task(self.start_call())

    async def start_call(self):
            await asyncio.sleep(0.3)
            await self.recorder.start()
            self.call_begin = time.time()
            self.full.set()

    def get_tracks(self, user_id):
        result = list()
        for u, ts in self.tracks.items():
            if u != user_id:
                result.extend(ts)
        return result

    async def end_call(self):
        await self.recorder.stop()
        call_time = time.time() - self.call_begin
        # end all connections


async def index(request):
    content = open(os.path.join(ROOT, 'index.html'), 'r').read()
    return web.Response(content_type='text/html', text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, 'client.js'), 'r').read()
    return web.Response(content_type='application/javascript', text=content)


class CreateGroup(web.View):
    async def post(self):
        params = await self.request.json()
        self.request.app.groups[params['uid']] = ConnectionGroup(**params)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(
        sdp=params['sdp'],
        type=params['type'])

    pc = RTCPeerConnection()
    pc_id = 'PeerConnection(%s)' % uuid.uuid4()
    pcs[pc_id] = pc
    index = len(traks.keys())

    def log_info(msg, *args):
        logger.info(pc_id + ' ' + msg, *args)

    log_info('Created for %s', request.remote)

    @pc.on('iceconnectionstatechange')
    async def on_iceconnectionstatechange():
        log_info('ICE connection state is %s', pc.iceConnectionState)
        if pc.iceConnectionState == 'failed':
            await pc.close()
            pcs.pop(pc_id)

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            if message == "END_CALL":
                channel.send('pong' + message[4:])  # recieve message END_CALL for properly call ending by button

    @pc.on('track')
    async def on_track(track):
        log_info('Track %s received', track.kind)
        if track.kind == 'video':
            traks[pc_id] = track
            events[0 if index else 1].set()
        # if track.kind == 'audio':
        #     recorder.addTrack(track)

        @track.on('ended')
        async def on_ended():
            log_info('Track %s ended', track.kind)
            # await recorder.stop()

    # handle offer
    await pc.setRemoteDescription(offer)
    # await recorder.start()

    # for t in pc.getTransceivers():
    #     if t.kind == 'audio':
    #         pc.addTrack(AudioStreamTrack())
    #     elif t.kind == 'video':
    #         pc.addTrack(VideoStreamTrack())

    await events[index].wait()
    for k, t in traks.items():
        if k != pc_id:
            pc.addTrack(t)

    # send answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))


async def on_shutdown(app):
    coros = [pc.close() for pc in app.connections]
    await asyncio.gather(*coros)
    app.connections.clear()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WebRTC audio / video / data-channels demo')
    parser.add_argument('--cert-file', help='SSL certificate file (for HTTPS)')
    parser.add_argument('--key-file', help='SSL key file (for HTTPS)')
    parser.add_argument('--port', type=int, default=8080,
                        help='Port for HTTP server (default: 8080)')
    parser.add_argument('--verbose', '-v', action='count')
    parser.add_argument('--write-audio', help='Write received audio to a file')
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
    app.on_shutdown.append(on_shutdown)
    app.router.add_get('/', index)
    app.router.add_get('/client.js', javascript)
    app.router.add_post('/offer', offer)
    app.groups = dict()
    app.connections = list()
    web.run_app(app, access_log=None, port=args.port, ssl_context=ssl_context)

