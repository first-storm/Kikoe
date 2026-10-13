#!/usr/bin/env bash
# Configure an ffmpeg source tree with only what Kikoe needs.
#
# Kikoe reads audio from audio or video containers (always with -vn) and writes Ogg
# Opus, FLAC and raw PCM. Video decoders, other encoders, devices and network
# protocols are left out. The lavfi input stays for generating test audio.
# Shared by build.sh and the Flatpak manifest.
#
# Usage, from the ffmpeg source directory:
#   configure.sh --prefix=<dir> [extra ./configure options]
set -euo pipefail

# Every audio decoder: imported files may use any of them.
audio_decoders=$(./configure --list-decoders | tr -s ' \t' '\n' | grep -E \
    '^(aac.*|ac3.*|adpcm_.*|ahx|alac.*|als|amr.*|apac|ape|aptx.*|atrac.*|binkaudio_.*|bonk|cook|dca|dfpwm|dolby_e|dsd_.*|dsicinaudio|dss_sp|dst|eac3.*|evrc|fastaudio|flac|ftr|g723_1|g728|g729|gsm.*|hca|hcom|iac|ilbc|imc|.*_dpcm|lc3|mace3|mace6|metasound|misc4|mlp|mp1.*|mp2.*|mp3.*|mpc7|mpc8|mpegh_3d_audio|msnsiren|nellymoser|on2avc|opus|osq|paf_audio|pcm_.*|qcelp|qdm2|qdmc|qoa|ra_144|ra_288|ralf|rka|s302m|sbc|shorten|sipr|siren|smackaud|sonic|speex|tak|truehd|truespeech|tta|twinvq|vmdaudio|vorbis|wavarc|wavesynth|wavpack|westwood_snd1|wmalossless|wmapro|wmav1|wmav2|wmavoice|xma1|xma2)$' \
    | paste -sd, -)

exec ./configure \
    --enable-static --disable-shared \
    --disable-autodetect \
    --disable-debug --disable-doc \
    --disable-ffplay --disable-ffprobe \
    --disable-network --disable-devices --enable-indev=lavfi \
    --disable-protocols --enable-protocol=file,pipe \
    --disable-decoders --enable-decoder="$audio_decoders" \
    --disable-encoders --enable-encoder=libopus,flac,pcm_s16le \
    --disable-muxers --enable-muxer=ogg,opus,flac,wav,pcm_s16le,null \
    --enable-libopus \
    "$@"
