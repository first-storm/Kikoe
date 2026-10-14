# Provider upload settings

Preferences → Providers → a provider → File Transcription sets parallel requests,
maximum chunk length in minutes, maximum encoded chunk size in decimal MB, and audio
format. These settings apply to imports, batch microphone transcription, retry, and
re-transcription. Live microphone transcription is unchanged. Changes apply immediately;
new providers keep them when Add is pressed. Reset Transcription Defaults restores all
four values. Running jobs keep the snapshot taken when they started.

Defaults and endpoint limits were checked against official documentation on 2026-10-13:

| API | Default / maximum encoded chunk size | Default / maximum chunk length | Default format |
| --- | --- | --- | --- |
| OpenAI | 25 MB | Automatic, governed by size | WAV |
| Groq multipart STT | 25 MB attachment cap | Automatic, governed by size | Ogg Opus |
| OpenAI-compatible | 25 MB compatibility default | Automatic, governed by size | Ogg Opus |
| OpenRouter multipart STT | 25 MB | Automatic, governed by size | Ogg Opus |
| Deepgram pre-recorded | 2,000 MB | Automatic, governed by size | Ogg Opus |
| Gemini Files + Transcribe | 2,000 MB | 60 minutes; effectively 30 with timestamps or speakers | Ogg Opus |
| Mistral transcription | 500 MB | 60 minutes | Ogg Opus |
| MiMo ASR | 7.5 MB raw audio = 10 MB Base64 | Automatic, governed by size | WAV |

Groq limits were checked on 2026-10-14. Its multipart attachment cap is 25 MB;
the larger dev-tier URL limit does not apply to local file uploads.

Mistral limits were checked on 2026-10-14. Its limits page lists 500 MB and 60 minutes
for audio, while the audio overview says recordings up to three hours; Kikoe uses the
stricter limits page, which only costs an extra split.

Sources: [Mistral known limitations](https://docs.mistral.ai/resources/known-limitations),
[Mistral speech to text](https://docs.mistral.ai/studio/audio/speech_to_text/offline_transcription),
[Groq speech to text](https://console.groq.com/docs/speech-to-text),
[OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text),
[OpenAI supported file types](https://platform.openai.com/docs/api-reference/audio/createTranscription),
[OpenRouter multipart STT](https://openrouter.ai/docs/guides/overview/multimodal/stt),
[Deepgram pre-recorded limits](https://developers.deepgram.com/docs/pre-recorded-audio),
[Gemini Files API](https://ai.google.dev/gemini-api/docs/files?hl=en),
[Gemini Transcribe limits](https://ai.google.dev/gemini-api/docs/transcribe), and
[MiMo ASR](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/audio/Speech-Recognition).

OpenAI-compatible is a protocol, so no single official maximum applies to all custom
servers. Its 25 MB default follows the existing adapter. Lower it when a server requires
smaller files. OpenRouter uses multipart, so larger JSON/URL limits do not apply. Gemini
uses uploaded files, so inline request size limits and general audio-understanding
length limits do not apply. Deepgram's processing timeout is not an audio-duration cap.

Parallelism defaults to **2**, with an application range of **1–32**; this is an app
choice, not a claimed universal service quota. Account/model rate limits still apply;
429 errors keep the existing retry behavior. A value of 1 is sequential. Request slots
are shared across simultaneous jobs for the same configured provider. Other providers
have independent queues. Without a documented audio-duration cap, length defaults to 0
(automatic); custom length is limited to 24 hours by the application. The UI permits
0.1 MB minimum sizes and 0.1-minute increments. Persisted limits are positive integers in
bytes/seconds; missing values mean provider defaults, so existing settings files load
without migration or losing models, preferences, or credentials.

Ogg Opus, FLAC, and WAV use existing bundled encoders, at 16 kHz mono. MiMo offers WAV
because its API only accepts WAV/MP3 and the bundled ffmpeg does not encode MP3. OpenAI
uses WAV by default; its API reference also accepts Ogg and FLAC. Original audio is kept
unchanged. Chunk planning takes the smaller user/API size and duration bounds, prefers
silence boundaries, and leaves a 10% margin when splitting. Every encoded part is checked
again and split further if it still exceeds a bound. An impossibly small budget reports
an error before uploading.

Workers may finish out of order; final text, times, and speaker scopes follow source
order. Streaming previews only expose the contiguous prefix, so later completed chunks
cannot appear ahead of earlier speech. Progress counts completed chunks. Failure or
cancellation stops and drains workers before temporary files are deleted; SQLite only
receives a recording after every chunk succeeds.
