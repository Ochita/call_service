from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack, AudioStreamTrack, VideoStreamTrack
import json
import numpy as np
from av import VideoFrame, AudioFrame, AudioResampler
from aiortc.contrib.media import MediaPlayer, MediaStreamError, MediaBlackhole
import os
from asyncio import gather, wait, sleep, ensure_future, wait_for, Future
import fractions
import time

ROOT = os.path.dirname(__file__)


class MulticastStreamTrack(MediaStreamTrack):  # base class for streams multiple recived
    def __init__(self, track):
        super().__init__()
        self._track = track
        self.kind = track.kind
        self.recv_future = None
        self.futures = list()

    @property
    def id(self):
        return self._track.id

    @property
    def readyState(self):
        return self._track.readyState

    def stop(self):
        self._track.stop()

    def resolve(self, future):
        frame = future.result()
        for fut in self.futures:
            fut.set_result(frame)
        self.futures = list()

    def recv(self):
        if self.recv_future is None or self.recv_future.done():
            self.recv_future = ensure_future(self._track.recv())
            self.recv_future.add_done_callback(self.resolve)
        fut = Future()
        self.futures.append(fut)
        return fut


class MuxStreamTrack(MediaStreamTrack):
    def __init__(self):
        super().__init__()  # don't forget this!
        self._tracks = set()

    def add_track(self, track):
        self._tracks.add(track)

    def remove_track(self, track):
        self._tracks.remove(track)

    async def recv(self):
        dead = set()
        for track in self._tracks:
            if track.readyState == 'ended':
                dead.add(track)
        for track in dead:
            self.remove_track(track)
        frames = await gather(*[track.recv() for track in self._tracks],
                              return_exceptions=True)
        return self.process_frames(frames)

    def process_frames(self, frames):
        raise NotImplementedError()


class MuxVideoStreamTrack(MuxStreamTrack):
    kind = 'video'

    # def __init__(self, width, height):
    #     super().__init__()
    #     self.width = width
    #     self.height = height

    def process_frames(self, frames):
        ars = list()
        for frame in frames:
            frame = frame.reformat(width=640, height=360)
            ar = frame.to_ndarray(format="rgb24")
            ars.append(ar)
        try:
            res = np.hstack(tuple(ars))
        except Exception as e:
            print(e)
        new_frame = VideoFrame.from_ndarray(res, format="rgb24")
        new_frame.pts = frame.pts
        new_frame.time_base = frame.time_base
        return new_frame


class ReSampledAudioStreamTrack(AudioStreamTrack):
    def __init__(self, track):
        super().__init__()
        self._track = track
        self.recv_future = None
        self.futures = list()
        self.re_sampler = AudioResampler(
                                        format='s16',
                                        layout='mono',
                                        rate=32000)

    def resolve(self, future):
        frame = future.result()
        frame = self.re_sampler.resample(frame)
        for fut in self.futures:
            fut.set_result(frame)
        self.futures = list()

    def recv(self):
        if self.recv_future is None or self.recv_future.done():
            self.recv_future = ensure_future(self._track.recv())
            self.recv_future.add_done_callback(self.resolve)
        fut = Future()
        self.futures.append(fut)
        return fut


class MuxAudioStreamTrack(MuxStreamTrack):
    kind = 'audio'

    def __init__(self):
        super().__init__()
        self.pts = 0
        self.last_time = time.time()
        self.pending = ()

    # async def recv(self):
    #     dead = set()
    #     for track in self._tracks:
    #         if track.readyState == 'ended':
    #             dead.add(track)
    #     for track in dead:
    #         self.remove_track(track)
    #     done, pending = await wait({track.recv() for track in self._tracks},
    #                                 timeout=0.021)
    #     done.update(self.pending)
    #     self.pending = pending
    #     frames = [await coro for coro in done]
    #     if not frames:
    #         frame = AudioFrame(format='s16', layout='mono', samples=int(0.020 * 32000))
    #         for p in frame.planes:
    #             p.update(bytes(p.buffer_size))
    #         frames = [frame]
    #     return self.process_frames(frames)

    def add_track(self, track):
        self._tracks.add(track)

    def process_frames(self, frames):
        samples = int(0.020 * 32000)
        res = None
        mul = 0.9
        # pts = 0
        sz = 0
        frame = None
        for fr in frames:
            if isinstance(fr, AudioFrame):
                # pts = max(pts, fr.pts)
                frame = fr
                ar = fr.to_ndarray()
                if res is not None:
                    if ar.shape == sz:
                        res += mul * ar
                else:
                    res = mul * ar
                    sz = ar.shape
        np.clip(res, -32767, 32767, res)
        res = res.astype('int16')
        new_frame = AudioFrame.from_ndarray(res, format='s16', layout='mono')
        new_frame.pts = self.pts
        self.last_time = time.time()
        self.pts += samples
        new_frame.time_base = fractions.Fraction(1, 32000)
        new_frame.sample_rate = 32000
        return new_frame


class ConnectionManager(object):
    def __init__(self):
        self.pc = RTCPeerConnection()
        self.tracks = set()
        self.datachannel = None

        @self.pc.on("datachannel")
        def on_datachannel(channel):
            self.datachannel = channel

            @channel.on("message")
            async def on_message(message):
                try:
                    data = json.loads(message)
                except Exception:
                    pass
                else:
                    if data.get('offer'):
                        offer = data['offer']
                        answer = await self.get_answer(offer['sdp'], offer['type'])
                        channel.send(json.dumps({'answer': answer}))
                    elif data.get('answer'):
                        answer = data['answer']
                        desc_answer = RTCSessionDescription(sdp=answer['sdp'], type=answer['type'])
                        await self.pc.setRemoteDescription(desc_answer)

        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            if self.pc.iceConnectionState == "failed":
                await self.pc.close()

        @self.pc.on("track")
        def on_track(track):
            if track.kind == 'audio':
                self.tracks.add(ReSampledAudioStreamTrack(track))
            else:
                self.tracks.add(track)

    async def get_answer(self, sdp, type):
        request = RTCSessionDescription(sdp=sdp, type=type)
        await self.pc.setRemoteDescription(request)
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        return {"sdp": self.pc.localDescription.sdp,
                "type": self.pc.localDescription.type}

    async def get_offer(self):
        request = await self.pc.createOffer()
        await self.pc.setLocalDescription(request)
        request = {"sdp": self.pc.localDescription.sdp,
                   "type": self.pc.localDescription.type}
        self.datachannel.send(json.dumps({'offer': request}))

    async def add_tracks(self, tracks):
        for tr in tracks:
            try:
                self.pc.addTrack(tr)
            except Exception:
                pass
        await self.get_offer()


class Connection(object):
    def __init__(self):
        self.pc = RTCPeerConnection()
        self.tracks = set()
        self.bhs = set()

        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            if self.pc.iceConnectionState == "failed":
                await self.pc.close()

        @self.pc.on("track")
        async def on_track(track):
            bh = MediaBlackhole()
            if track.kind == 'audio':
                rst = ReSampledAudioStreamTrack(track)
                self.tracks.add(rst)
                bh.addTrack(rst)
            else:
                self.tracks.add(track)
                bh.addTrack(track)
            await sleep(1)
            await bh.start()
            self.bhs.add(bh)

        self.video = MuxVideoStreamTrack()
        self.video.add_track(VideoStreamTrack())

        self.audio = MuxAudioStreamTrack()
        player = MediaPlayer(os.path.join(ROOT, "Space Unicorn.mp3"))
        self.audio.add_track(ReSampledAudioStreamTrack(player.audio))

        self.pc.addTrack(self.video)
        self.pc.addTrack(self.audio)

    async def get_answer(self, sdp, type):
        request = RTCSessionDescription(sdp=sdp, type=type)
        await self.pc.setRemoteDescription(request)
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        return {"sdp": self.pc.localDescription.sdp,
                "type": self.pc.localDescription.type}

    async def get_offer(self):
        request = await self.pc.createOffer()
        await self.pc.setLocalDescription(request)
        request = {"sdp": self.pc.localDescription.sdp,
                   "type": self.pc.localDescription.type}
        # self.datachannel.send(json.dumps({'offer': request}))

    async def replace_track(self, track):
        for s in self.pc.getSenders():
            if s.kind == track.kind:
                s.replaceTrack(track)
