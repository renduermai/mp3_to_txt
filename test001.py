import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

import os
import subprocess

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
audio = r"E:\chromeDownload\小宇宙音频\十分吸引 - EP.01 黄金、美债与凯文・沃什.m4a"

print(f"ffmpeg: {FFMPEG}")

if not os.path.isfile(audio):
    print(f"音频文件不存在: {audio}")
    raise SystemExit(1)

result = subprocess.run(
    [FFMPEG, "-hide_banner", "-i", audio, "-f", "null", "-"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)

print(result.stderr)
print(f"returncode = {result.returncode}")