# Provider upload settings

Use **Preferences → Providers → select a provider → File Transcription** to
control how Kikoe uploads audio. The settings apply to imports, microphone audio
transcribed after Stop, retries and re-transcription. Live microphone sessions
use their realtime connection settings instead.

## Controls and when changes apply

| Setting | Meaning | Default and range |
| --- | --- | --- |
| Parallel Requests | Maximum concurrent uploads for this configured provider | 2; range 1–32; 1 processes chunks sequentially |
| Chunk Length (Minutes) | Maximum duration of an uploaded chunk | API default, or 0 for automatic sizing; custom values up to 24 hours where the API permits, with 0.1-minute precision |
| Chunk Size (MB) | Maximum encoded size of an uploaded chunk | API default; minimum 0.1 decimal MB |
| Audio Format | Encoding used for uploads | Ogg Opus, FLAC or WAV, subject to API support |

Existing-provider changes apply immediately. A new provider saves them when Add
is pressed. Reset Transcription Defaults restores all four values. Running jobs
retain the configuration snapshot taken at their start.

Parallelism is an application setting, not a service quota. Account and model
rate limits still apply, with existing retry handling for HTTP 429 responses.
Simultaneous jobs share request slots for the same configured provider; other
providers have independent queues.

## API defaults and limits

Sizes below use decimal MB and refer to encoded audio before upload, except where
the MiMo Base64 limit is called out. Kikoe uses the smaller of the configured
limit and the applicable endpoint or model limit.

| API | Default encoded-size limit | Default duration limit | Default format |
| --- | --- | --- | --- |
| OpenAI | 25 MB | Automatic, governed by size | WAV |
| Groq multipart speech-to-text | 25 MB attachment cap | Automatic, governed by size | Ogg Opus |
| OpenAI-compatible | 25 MB compatibility default | Automatic, governed by size | Ogg Opus |
| OpenRouter multipart speech-to-text | 25 MB | Automatic, governed by size | Ogg Opus |
| Deepgram pre-recorded | 2,000 MB | Automatic, governed by size | Ogg Opus |
| Gemini Files + Transcribe | 2,000 MB | 60 minutes; 30 with timestamps or speakers | Ogg Opus |
| Mistral transcription | 500 MB | 60 minutes | Ogg Opus |
| MiMo ASR | 7.5 MB audio, corresponding to 10 MB Base64 | Automatic, governed by size | WAV |

Defaults and endpoint limits were checked against official documentation on
13 October 2026, with Groq and Mistral checked on 14 October 2026. These dates
record the source review; provider policies and account quotas can change.

### How to interpret provider limits

- **OpenAI-compatible:** this is a protocol, not a single service. The 25 MB
  default follows Kikoe's adapter and is not a universal server maximum. Lower
  it when a custom endpoint requires smaller uploads.
- **Groq:** local multipart attachments use the 25 MB cap. Larger development-tier
  limits for URL-based audio do not apply to this upload path.
- **OpenRouter:** Kikoe uses multipart transcription. Larger limits for JSON or
  URL inputs do not apply.
- **Gemini:** Kikoe uses uploaded files, so inline-request limits and general
  audio-understanding duration limits do not apply. Transcribe permits 60 minutes,
  reduced to 30 when requesting timestamps or speakers.
- **Mistral:** the limits page specifies 500 MB and 60 minutes, while the audio
  overview describes recordings up to three hours. Kikoe uses the stricter
  60-minute limit, splitting longer audio as needed.
- **Deepgram:** a processing timeout is not an audio-duration cap.
- **MiMo:** the 10 MB limit applies to Base64, leaving a 7.5 MB audio budget.
  Its API accepts WAV and MP3; Kikoe offers WAV because the bundled ffmpeg does
  not encode MP3.

OpenAI defaults to WAV, while its API also accepts Ogg and FLAC. Available formats
use Kikoe's bundled encoders at 16 kHz mono. Upload conversion leaves retained
original audio unchanged.

## Chunking, ordering and failure behavior

Kikoe combines size and duration restrictions before encoding. The stricter
budget wins. Chunk planning prefers silence boundaries and reserves a 10% size
margin for bitrate variation. Every encoded part is checked again and split
further if necessary. A budget too small to produce valid audio reports an error
before upload.

Parallel workers can finish out of order. The final transcript, timestamps and
speaker scopes still follow source order. Streaming previews expose only the
contiguous completed prefix, preventing later speech from appearing before earlier
chunks. Progress counts completed chunks.

Failure or cancellation stops and drains workers before deleting temporary files.
Database changes commit only after all chunks succeed. Failed imports remove
their copied originals; failed microphone uploads retain recorded FLAC for retry;
failed re-transcription preserves the previous transcript and recording state.
See [Architecture](architecture.md#file-transcription) for the commit lifecycle.

Settings store limits as positive integer bytes and seconds. Missing values mean
provider defaults, so older settings remain compatible without losing models,
preferences or credentials.

## Official references

| API | Source |
| --- | --- |
| OpenAI | [Speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text), [transcription API and file types](https://platform.openai.com/docs/api-reference/audio/createTranscription) |
| Groq | [Speech-to-text](https://console.groq.com/docs/speech-to-text) |
| OpenRouter | [Multipart speech-to-text](https://openrouter.ai/docs/guides/overview/multimodal/stt) |
| Deepgram | [Pre-recorded audio](https://developers.deepgram.com/docs/pre-recorded-audio) |
| Gemini | [Files API](https://ai.google.dev/gemini-api/docs/files?hl=en), [Transcribe](https://ai.google.dev/gemini-api/docs/transcribe) |
| Mistral | [Known limitations](https://docs.mistral.ai/resources/known-limitations), [offline transcription](https://docs.mistral.ai/studio/audio/speech_to_text/offline_transcription) |
| MiMo | [Speech recognition](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/audio/Speech-Recognition) |
