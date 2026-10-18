"""Stream durable microphone audio without allowing the network to stop capture."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from kikoe.core.choice import ModelChoice
from kikoe.core.chunking import offset_segments
from kikoe.core.models import Segment
from kikoe.core.recording import CaptureState
from kikoe.core.transcription import (
    CheckpointStrategy,
    LiveSnapshot,
    LiveState,
    LiveTranscript,
    RealtimeProfile,
    scope_speakers,
)
from kikoe.media.ffmpeg import PCMStream
from kikoe.providers.base import (
    RealtimeSession,
    TranscriptionOptions,
    TranscriptionProvider,
)
from kikoe.providers.errors import sdk_error
from kikoe.services.recording import RecordingSession

log = logging.getLogger(__name__)


class RealtimeRecording:
    """Replay unconfirmed disk ranges and replace their provisional recognition results."""

    def __init__(
        self,
        capture: RecordingSession,
        choice: ModelChoice,
        client: TranscriptionProvider,
        options: TranscriptionOptions,
        on_update: Callable[[LiveSnapshot], None],
    ) -> None:
        self.capture = capture
        self.choice = choice
        self.client = client
        self.options = options
        self.on_update = on_update
        self.complete = False
        self.segments: tuple[Segment, ...] = ()
        self._stable: tuple[Segment, ...] = ()
        self._text = LiveTranscript()
        self._confirmed = 0
        self._sent = 0
        self._generation = 0
        self._replay_until = 0
        self._seen: set[str] = set()
        self._state = LiveState.CONNECTING
        self._exhausted = False
        self._profile = RealtimeProfile()
        self._persist_lock = asyncio.Lock()

    def snapshot(self) -> LiveSnapshot:
        return LiveSnapshot(self.segments, self._text.preview, self._state)

    def _notify(self, state: LiveState | None = None) -> None:
        if state is not None:
            self._state = state
        self.on_update(self.snapshot())

    async def run(self) -> None:
        failures = 0
        while True:
            while self.capture.state is CaptureState.PAUSED and not self.capture.finished:
                self.capture.state_changed.clear()
                self._notify(LiveState.PAUSED)
                await self.capture.state_changed.wait()
            if self.capture.finished and self._confirmed == self._sent and self._sent > 0:
                # A cleanly flushed pause followed by stop needs no new connection.
                journal = self.capture.journal
                size = (
                    journal.sample_rate * self.capture.elapsed.total_seconds() * 2 if journal else 0
                )
                if self._confirmed >= size:
                    self.complete = True
                    return
            try:
                await self._connection()
                failures = 0
                if self.capture.finished and self._exhausted:
                    self.complete = True
                    return
            except Exception as error:
                failure = sdk_error(error)
                self._replay_until = max(self._replay_until, self._sent)
                if not failure.retryable:
                    # Reconnecting cannot fix this, so the user must learn the reason.
                    self._notify(LiveState.INCOMPLETE)
                    if failure is error:
                        raise
                    raise failure from error
                log.warning("Realtime connection failed (%s)", type(error).__name__)
                self._notify(LiveState.RECONNECTING)
                if self.capture.finished:
                    return
                # Waiting ends immediately when capture stops; it never delays audio saving.
                delay = min(15, 2 ** min(failures, 4))
                failures += 1
                try:
                    async with asyncio.timeout(delay):
                        await self.capture.stopped.wait()
                    return
                except TimeoutError:
                    continue

    async def _connection(self) -> None:
        journal = self.capture.require_journal()
        start = self._confirmed
        self._generation += 1
        self._sent = start
        self._exhausted = False
        self._text = LiveTranscript()
        self._seen = set()
        self._profile = self.client.realtime_profile(self.choice.model)
        rate = self._profile.sample_rate
        session: RealtimeSession | None = None
        converter: PCMStream | None = None
        tasks: list[asyncio.Task[None]] = []
        try:
            async with asyncio.timeout(30):
                session = await self.client.open_realtime(self.choice.model, self.options)
            converter = await PCMStream.start(journal.sample_rate, rate)
            self._notify(LiveState.CATCHING_UP if self._replay_until > start else LiveState.LIVE)
            receiver = asyncio.create_task(self._receive(session, start))
            feeder = asyncio.create_task(self._feed(converter, start))
            sender = asyncio.create_task(self._send(session, converter, start, rate))
            keeper = asyncio.create_task(self._keep_alive(session))
            tasks = [receiver, feeder, sender, keeper]
            while True:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if sender in done:
                    return
                if receiver in done:
                    if self.capture.finished or self.capture.state is CaptureState.PAUSED:
                        async with asyncio.timeout(10):
                            await sender
                        return
                    raise ConnectionError("The provider closed the realtime connection")
                # A finished feeder is expected after stop; wait for converter output to drain.
                tasks.remove(feeder)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if converter is not None:
                await converter.close()
            if session is not None:
                await session.close()

    async def _feed(self, converter: PCMStream, start: int) -> None:
        journal = self.capture.require_journal()
        offset = start
        while True:
            self.capture.audio_changed.clear()
            block = await asyncio.to_thread(self.capture.store.read_pcm, journal, offset, 16384)
            if block:
                offset += len(block)
                await converter.feed(block)
                continue
            if self.capture.finished or self.capture.state is CaptureState.PAUSED:
                await converter.finish()
                return
            await self.capture.audio_changed.wait()

    async def _send(
        self, session: RealtimeSession, converter: PCMStream, start: int, rate: int
    ) -> None:
        journal = self.capture.require_journal()
        loop = asyncio.get_running_loop()
        connected = loop.time()
        checkpoint = connected
        output_bytes = 0
        async for block in converter.chunks():
            async with asyncio.timeout(30):
                await session.send(block)
            output_bytes += len(block)
            self._sent = start + (output_bytes * journal.sample_rate // rate // 2) * 2
            # Catch-up is paced rather than uploading an unbounded WebSocket burst.
            await asyncio.sleep(len(block) / (rate * 2 * 1.25))
            if self._profile.checkpoint is CheckpointStrategy.FLUSH and (
                loop.time() - checkpoint >= 15
                or (self._replay_until and self._sent >= self._replay_until)
            ):
                await self._checkpoint(session)
                checkpoint = loop.time()
            lifetime = self._profile.max_session
            if lifetime is not None and loop.time() - connected >= lifetime.total_seconds():
                await self._checkpoint(session)
                return
        await self._checkpoint(session)
        self._exhausted = True

    async def _checkpoint(self, session: RealtimeSession) -> None:
        async with asyncio.timeout(10):
            await session.flush()
        self._stable += self._text.segments
        self._text = LiveTranscript()
        self._confirmed = self._sent
        if self._replay_until and self._sent < self._replay_until:
            return
        self.segments = self._stable
        self._replay_until = 0
        await self._persist()
        self._notify(LiveState.LIVE)

    async def _receive(self, session: RealtimeSession, start: int) -> None:
        journal = self.capture.require_journal()
        offset = timedelta(seconds=start / (journal.sample_rate * 2))
        scope = f"{journal.recording_id}:{self._generation}"
        async for event in session.events():
            if event.id in self._seen:
                continue
            normalized = offset_segments(scope_speakers(event.segments, scope), offset)
            self._text.apply(replace(event, segments=normalized))
            if event.final:
                self._seen.add(event.id)
                if not self._replay_until:
                    self.segments = self._stable + self._text.segments
                    await self._persist()
                if (
                    self._profile.checkpoint is CheckpointStrategy.AUDIO_END
                    and event.audio_end is not None
                ):
                    covered = (
                        start + round(event.audio_end.total_seconds() * journal.sample_rate) * 2
                    )
                    self._confirmed = covered
                    self._stable += self._text.segments
                    self._text = LiveTranscript()
                    if covered >= self._replay_until:
                        self._replay_until = 0
                        self.segments = self._stable
                        await self._persist()
                        self._state = LiveState.LIVE
            self._notify()

    async def _persist(self) -> None:
        journal = self.capture.journal
        if journal is None:
            return
        # The receiver and the sender both persist, and the file is replaced via one temporary.
        async with self._persist_lock:
            await asyncio.to_thread(self.capture.store.checkpoint, journal, self.segments)

    async def _keep_alive(self, session: RealtimeSession) -> None:
        while True:
            await asyncio.sleep(3)
            await session.keep_alive()
