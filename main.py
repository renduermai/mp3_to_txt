# -*- coding: utf-8 -*-
"""
音频转逐字稿（腾讯云 · 录音文件识别极速版）
------------------------------------------------------
官方文档: https://cloud.tencent.com/document/product/1093/52097
接口能力: 支持 100MB 以内、2 小时以内的音频文件，HTTPS POST 同步返回结果
免费额度: 每月 5 小时（开通服务后自动发放，详见 https://cloud.tencent.com/document/product/1093/35686 ）

★★★ 密钥请填写在同目录下的 .env 文件中 ★★★
    TENCENT_ASR_APPID=你的APPID
    TENCENT_ASR_SECRET_ID=你的SecretId
    TENCENT_ASR_SECRET_KEY=你的SecretKey
申请地址（腾讯云 API 密钥管理）:
    https://console.cloud.tencent.com/cam/capi
并确保已在「语音识别控制台」开通服务:
    https://console.cloud.tencent.com/asr

依赖安装（Python 3.11，建议在 PyCharm 的 Terminal 中执行）:
    pip install gradio requests imageio-ffmpeg python-dotenv

运行方式:
    python main.py
    启动后在浏览器打开 http://127.0.0.1:7860
"""

import base64
import hashlib
import hmac
import os
import subprocess
import tempfile
import time
from pathlib import Path

import gradio as gr
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

os.environ.setdefault("NO_PROXY", "asr.cloud.tencent.com")

# ======================================================================
# 密钥从环境变量（或 .env 文件）读取，不再硬编码在源码中
# ======================================================================
APPID = os.getenv("TENCENT_ASR_APPID", "").strip()
SECRET_ID = os.getenv("TENCENT_ASR_SECRET_ID", "").strip()
SECRET_KEY = os.getenv("TENCENT_ASR_SECRET_KEY", "").strip()

CREDENTIALS_MISSING = [
    name for name, val in (
        ("TENCENT_ASR_APPID", APPID),
        ("TENCENT_ASR_SECRET_ID", SECRET_ID),
        ("TENCENT_ASR_SECRET_KEY", SECRET_KEY),
    ) if not val
]
# ======================================================================

# 识别引擎：16k_zh = 中文通用（普通话）。其他可选值见官方文档 4.2 节。
ENGINE_TYPE = "16k_zh"

# 接口地址（官方文档 4.x 节）
ASR_HOST = "asr.cloud.tencent.com"
ASR_PATH = "/asr/flash/v1/"
MAX_FILE_BYTES = 100 * 1024 * 1024

# 文件扩展名 -> 接口要求的 voice_format 参数
VOICE_FORMAT_MAP = {
    ".wav": "wav", ".pcm": "pcm", ".mp3": "mp3", ".m4a": "m4a",
    ".aac": "aac", ".amr": "amr", ".ogg": "ogg-opus", ".opus": "ogg-opus",
    ".silk": "silk", ".spx": "speex", ".speex": "speex",
}

RETRYABLE_CODES = {5001, 5002, 5003}
MAX_RETRY = 2

CHUNK_SIZE = 256 * 1024
UPLOAD_TIMEOUT = (60, 600)

def build_signature(params: dict) -> str:
    """
    按官方文档 4.5 节生成签名：
    1) 所有 URL 参数按字典序排序，拼成 k=v&k=v
    2) 签名原文 = "POST" + host + path + appid + "?" + 参数串
    3) 用 SecretKey 做 HMAC-SHA1，再 Base64
    """
    query = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    sign_str = f"POST{ASR_HOST}{ASR_PATH}{APPID}?{query}"
    digest = hmac.new(SECRET_KEY.encode("utf-8"),
                      sign_str.encode("utf-8"),
                      hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")

def _iter_bytes(data: bytes):
    """将字节流分块交付，避免 requests 一次性写入数十 MB 导致写超时。"""
    for i in range(0, len(data), CHUNK_SIZE):
        yield data[i:i + CHUNK_SIZE]

def call_flash_asr(audio_bytes: bytes, voice_format: str) -> dict:
    """调用录音文件识别极速版接口，返回响应 JSON。偶发错误自动重试。"""
    params = {
        "secretid": SECRET_ID,
        "engine_type": ENGINE_TYPE,
        "voice_format": voice_format,
        "timestamp": str(int(time.time())),
        "convert_num_mode": "1",
        "filter_punc": "0",
        "first_channel_only": "1",
    }
    signature = build_signature(params)
    query = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    url = f"https://{ASR_HOST}{ASR_PATH}{APPID}?{query}"
    headers = {
        "Host": ASR_HOST,
        "Authorization": signature,
        "Content-Type": "application/octet-stream",
    }

    last_error = None
    for attempt in range(1, MAX_RETRY + 2):
        try:
            resp = requests.post(url, headers=headers,
                                 data=_iter_bytes(audio_bytes),
                                 timeout=UPLOAD_TIMEOUT)
        except requests.exceptions.RequestException as e:
            last_error = (f"网络请求失败: {e}\n"
                          f"建议检查 VPN/代理是否干扰上传，或上行带宽是否过低。")
            if attempt <= MAX_RETRY:
                time.sleep(3)
                continue
            raise RuntimeError(last_error)

        try:
            result = resp.json()
        except ValueError:
            raise RuntimeError(f"接口返回了非 JSON 内容 (HTTP {resp.status_code}): "
                               f"{resp.text[:300]}")

        code = result.get("code", -1)
        if code == 0:
            return result

        message = result.get("message", "未知错误")
        if code in RETRYABLE_CODES and attempt <= MAX_RETRY:
            time.sleep(2)
            continue
        raise RuntimeError(f"转写失败 [错误码 {code}]: {message} "
                           f"(request_id: {result.get('request_id', 'N/A')})")

    raise RuntimeError(last_error or "未知错误")

def normalize_audio(src_path: str) -> str:
    """用 ffmpeg 将任意音频转为 16k 单声道 mp3，规避服务端解码失败(4007)。
    返回转换后的临时文件路径；若转换不可用则原样返回 src_path。
    """
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return src_path

    out_path = tempfile.mktemp(suffix=".mp3", prefix="asr_norm_")
    cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-i", src_path,
           "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
           "-c:a", "libmp3lame", out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not os.path.exists(out_path):
            return src_path
        if os.path.getsize(out_path) == 0:
            return src_path
        return out_path
    except Exception:
        return src_path

def extract_text(result: dict) -> str:
    """从响应 JSON 中提取逐字稿文本（按句子分行，便于阅读与字幕化）。"""
    lines = []
    for channel in result.get("flash_result", []):
        sentences = channel.get("sentence_list") or []
        if sentences:
            lines.extend(s.get("text", "") for s in sentences if s.get("text"))
        elif channel.get("text"):
            lines.append(channel["text"])
    return "\n".join(lines).strip()

def transcribe(audio_file):
    """Gradio 事件函数：上传 -> 转码 -> 转写 -> 返回（状态, 文本, 可下载的txt文件）"""
    if CREDENTIALS_MISSING:
        return (f"❌ 未设置密钥: {', '.join(CREDENTIALS_MISSING)}\n"
                "请在项目根目录的 .env 文件中填写，申请地址: "
                "https://console.cloud.tencent.com/cam/capi", "", None)
    if audio_file is None:
        return ("⚠️ 请先上传音频文件", "", None)

    file_path = audio_file if isinstance(audio_file, str) else getattr(audio_file, "name", None)
    if not file_path or not os.path.exists(file_path):
        return ("❌ 未找到上传的文件，请重新上传", "", None)

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in VOICE_FORMAT_MAP:
        return (f"❌ 不支持的音频格式 {ext}，支持: wav/mp3/m4a/aac/amr/pcm/ogg/silk/speex",
                "", None)

    if os.path.getsize(file_path) == 0:
        return ("❌ 音频文件为空", "", None)

    norm_path = None
    try:
        yield ("⏳ 正在转为标准音频格式（16k 单声道 mp3）...", "", None)
        norm_path = normalize_audio(file_path)
        if not norm_path or not os.path.exists(norm_path):
            yield ("❌ 音频转换为标准格式失败，请检查文件本身是否损坏", "", None)
            return

        voice_format = VOICE_FORMAT_MAP.get(os.path.splitext(norm_path)[1].lower(), "mp3")
        size_mb = os.path.getsize(norm_path) / 1024 / 1024
        if size_mb > 100:
            yield (f"❌ 转码后仍为 {size_mb:.1f}MB，超过极速版 100MB 限制。\n"
                   "请先用 ffmpeg 压缩或改用异步接口。", "", None)
            return

        yield (f"⏳ 正在上传音频（{size_mb:.1f}MB）...", "", None)
        with open(norm_path, "rb") as f:
            audio_bytes = f.read()

        yield ("⏳ 上传完成，转写中（约需 10~60 秒，请勿关闭页面）...", "", None)
        result = call_flash_asr(audio_bytes, voice_format)

        text = extract_text(result)
        if not text:
            yield ("⚠️ 转写成功但结果为空，可能音频中无人声或音量过低。", "", None)
            return

        duration_s = result.get("audio_duration", 0) / 1000
        tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                          suffix=".txt", prefix="transcript_",
                                          delete=False)
        tmp.write(text)
        tmp.close()

        yield (f"✅ 转写成功！音频时长 {duration_s / 60:.1f} 分钟，"
               f"共 {len(text)} 字，request_id: {result.get('request_id')}",
               text, tmp.name)

    except RuntimeError as e:
        yield (f"❌ {e}", "", None)
    except Exception as e:
        yield (f"❌ 发生未知错误: {type(e).__name__}: {e}", "", None)
    finally:
        if norm_path and norm_path != file_path and os.path.exists(norm_path):
            try:
                os.remove(norm_path)
            except OSError:
                pass

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="音频转逐字稿") as demo:
        gr.Markdown(
            "# 🎙️ 音频转逐字稿\n"
            "基于腾讯云「录音文件识别极速版」· 支持中文普通话长音频"
            "（100MB 以内 / 2 小时以内）· 每月 5 小时免费额度"
        )

        audio_input = gr.File(
            label="① 上传音频文件（wav / mp3 / m4a / aac / amr / pcm / ogg / silk / speex）",
            file_types=[".wav", ".mp3", ".m4a", ".aac", ".amr",
                        ".pcm", ".ogg", ".opus", ".silk", ".spx", ".speex"],
        )
        submit_btn = gr.Button("② 开始转写", variant="primary", size="lg")

        status_box = gr.Textbox(label="状态提示", interactive=False)
        transcript_box = gr.Textbox(label="③ 生成文稿（可编辑）",
                                    lines=18, interactive=True)
        download_file = gr.File(label="下载逐字稿（.txt）", interactive=False)

        submit_btn.click(fn=transcribe,
                         inputs=audio_input,
                         outputs=[status_box, transcript_box, download_file])
    return demo

if __name__ == "__main__":
    if CREDENTIALS_MISSING:
        raise SystemExit(
            "请先在项目根目录创建 .env 文件并填写:\n"
            "  TENCENT_ASR_APPID=你的APPID\n"
            "  TENCENT_ASR_SECRET_ID=你的SecretId\n"
            "  TENCENT_ASR_SECRET_KEY=你的SecretKey\n"
            f"当前缺失: {', '.join(CREDENTIALS_MISSING)}"
        )

    demo = build_ui()
    demo.launch(server_name="127.0.0.1", server_port=7860,
                inbrowser=True, max_file_size="200MB")