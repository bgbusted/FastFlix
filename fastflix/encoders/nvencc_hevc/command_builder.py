# -*- coding: utf-8 -*-
import re
import secrets

from fastflix.encoders.common.helpers import Command, generate_all, generate_color_details, null
from fastflix.models.encode import NVEncCSettings
from fastflix.models.fastflix import FastFlix
from fastflix.flix import unixy

lossless = ["flac", "truehd", "alac", "tta", "wavpack", "mlp"]


def build_audio(audio_tracks, audio_file_index=0):
    # TODO figure out copy and downmix
    # https://github.com/rigaya/NVEnc/blob/master/NVEncC_Options.en.md#--audio-stream-intorstringstring1string2
    command_list = []
    copies = []

    for track in audio_tracks:
        command_list.append(
            f'--audio-metadata {track.outdex}?title="{track.title}" '
            f'--audio-metadata {track.outdex}?handler="{track.title}" '
        )
        if track.language:
            command_list.append(f"--audio-metadata {track.outdex}?language={track.language}")
        if not track.conversion_codec or track.conversion_codec == "none":
            copies.append(str(track.outdex))
        elif track.conversion_codec:
            # downmix = f"-ac:{track.outdex} {track.downmix}" if track.downmix > 0 else ""
            bitrate = ""
            if track.conversion_codec not in lossless:
                bitrate = f"--audio-bitrate {track.outdex}?{track.conversion_bitrate.rstrip('k')} "
            command_list.append(f"--audio-codec {track.outdex}?{track.conversion_codec} {bitrate}")

    return f" --audio-copy {','.join(copies)} {' '.join(command_list)}" if copies else f" {' '.join(command_list)}"


def build(fastflix: FastFlix):
    settings: NVEncCSettings = fastflix.current_video.video_settings.video_encoder_settings

    # beginning, ending = generate_all(fastflix, "hevc_nvenc")

    # beginning += f'{f"-tune:v {settings.tune}" if settings.tune else ""} {generate_color_details(fastflix)} -spatial_aq:v {settings.spatial_aq} -tier:v {settings.tier} -rc-lookahead:v {settings.rc_lookahead} -gpu {settings.gpu} -b_ref_mode {settings.b_ref_mode} '

    # --profile main10 --tier main
    master_display = None
    if fastflix.current_video.master_display:
        master_display = (
            f'--master-display "G{fastflix.current_video.master_display.green}'
            f"B{fastflix.current_video.master_display.blue}"
            f"R{fastflix.current_video.master_display.red}"
            f"WP{fastflix.current_video.master_display.white}"
            f'L{fastflix.current_video.master_display.luminance}"'
        )

    max_cll = None
    if fastflix.current_video.cll:
        max_cll = f'--max-cll "{fastflix.current_video.cll}"'

    dhdr = None
    if settings.hdr10plus_metadata:
        dhdr = f'--dhdr10-info "{settings.hdr10plus_metadata}"'

    # TODO output-res, crop, remove hdr, time, rotate, flip, seek
    res = ""
    if fastflix.current_video.video_settings.scale:
        res = "--output-res "

    command = [
        f'"{unixy(fastflix.config.nvencc)}"',
        "-i",
        f'"{fastflix.current_video.source}"',
        "-c",
        "hevc",
        "--vbr",
        settings.bitrate,
        "--preset",
        settings.preset,
        "--profile",
        settings.profile,
        "--tier",
        settings.tier,
        f'{f"--lookahead {settings.lookahead}" if settings.lookahead else ""}',
        f'{"--aq" if settings.spatial_aq else "--no-aq"}',
        "--colormatrix",
        (fastflix.current_video.video_settings.color_space or "auto"),
        "--transfer",
        (fastflix.current_video.video_settings.color_transfer or "auto"),
        "--colorprim",
        (fastflix.current_video.video_settings.color_primaries or "auto"),
        f'{master_display if master_display else ""}',
        f'{max_cll if max_cll else ""}',
        f'{dhdr if dhdr else ""}',
        "--output-depth",
        "10" if fastflix.current_video.current_video_stream.bit_depth > 8 else "8",
        build_audio(fastflix.current_video.video_settings.audio_tracks),
        "--multipass",
        "2pass-full",
        "--mv-precision",
        "Q-pel",
        "--chromaloc",
        "auto",
        "--colorrange",
        "auto",
        "-o",
        f'"{unixy(fastflix.current_video.video_settings.output_path)}"',
    ]

    return [Command(command=" ".join(command), name="NVEncC Encode")]


# -i "Beverly Hills Duck Pond - HDR10plus - Jessica Payne.mp4" -c hevc --profile main10 --tier main --output-depth 10 --vbr 6000k --preset quality --multipass 2pass-full --aq --repeat-headers --colormatrix bt2020nc --transfer smpte2084 --colorprim bt2020 --lookahead 16 -o "nvenc-6000k.mkv"

#
# if settings.profile:
#     beginning += f"-profile:v {settings.profile} "
#
# if settings.rc:
#     beginning += f"-rc:v {settings.rc} "
#
# if settings.level:
#     beginning += f"-level:v {settings.level} "
#
# pass_log_file = fastflix.current_video.work_path / f"pass_log_file_{secrets.token_hex(10)}"
#
# command_1 = (
#     f"{beginning} -pass 1 "
#     f'-passlogfile "{pass_log_file}" -b:v {settings.bitrate} -preset:v {settings.preset} -2pass 1 '
#     f'{settings.extra if settings.extra_both_passes else ""} -an -sn -dn -f mp4 {null}'
# )
# command_2 = (
#     f'{beginning} -pass 2 -passlogfile "{pass_log_file}" -2pass 1 '
#     f"-b:v {settings.bitrate} -preset:v {settings.preset} {settings.extra} "
# ) + ending
# return [
#     Command(command=re.sub("[ ]+", " ", command_1), name="First pass bitrate", exe="ffmpeg"),
#     Command(command=re.sub("[ ]+", " ", command_2), name="Second pass bitrate", exe="ffmpeg"),
# ]
