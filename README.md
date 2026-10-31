# 音频转逐字稿

一个本地运行的 Gradio 小工具：上传音频，自动转成中文文字稿，可在线编辑并下载 txt。

底层调用**腾讯云「录音文件识别极速版」**接口，不是本地跑 Whisper，所以**不需要独显、不需要装模型**，1 小时的播客几十秒就能出结果。

## 效果

![界面效果](docs/screenshot.png)
<img width="1940" height="1029" alt="image" src="https://github.com/user-attachments/assets/8d3dbb9f-784b-4565-a835-132fb0952421" />


> 实测：62.5 分钟播客 → 18608 字逐字稿，上传 57.8MB，下载 53.5KB txt。

---

## 功能

- 上传任意常见音频格式（wav / mp3 / m4a / aac / amr / pcm / ogg / opus / silk / speex）
- 自动转成标准格式后再上传，格式兼容问题在本地就消化掉
- 网页界面：上传 → 转写 → 文本可直接编辑 → 下载 .txt
- 按句子分行输出，读起来像人写的一样，不是一大坨没标点的字
- 单文件支持 100MB 以内、2 小时以内音频

---

## 代码里做了哪些优化（避免报错的那些事）

这些都是踩坑之后加上的,不是花架子：

**1. 只用 Python 就能跑,不用装 ffmpeg**
通过 `imageio-ffmpeg` 自动下载并内置一份 ffmpeg 二进制,在代码里用 `get_ffmpeg_exe()` 拿到路径。所以：
- 不用手动装 ffmpeg、不用配 PATH
- 不会出现 `FileNotFoundError: [WinError 2] 系统找不到指定的文件`
- 换电脑迁移时,依赖装好就自带 ffmpeg

**2. 上传前先转码归一化(解决 4007 解码失败)**
有些 m4a/mp4 容器封装不规范(比如用新版本 Lavf 导出的文件),腾讯云服务端解码器认不出来,直接返回 `4007 audio decode failed`。所以代码在上传前统一做一次转码：

```
-ac 1      单声道（引擎输入要求,多声道也只按单声道计费）
-ar 16000  16kHz（匹配 16k_zh 中文引擎）
libmp3lame 输出 mp3（服务端支持最成熟的格式）
```

顺带好处：1 小时 129kbps 的 m4a 约 60MB，转完只有约 24MB，上传快很多。

而且**转码失败不会阻断流程**——会自动回退到原文件继续走，不会比原来更差。

**3. 分块上传(解决上传写超时)**
原来把整个 60MB 一次性塞进 socket,网络差一点就报：

```
('Connection aborted.', TimeoutError('The write operation timed out'))
```

现在改成 256KB 分块流式上传,小步写入,不会因为一次性写大包超时。

**4. 腾讯云域名直连,不走代理**
代码启动时设置 `NO_PROXY=asr.cloud.tencent.com`。如果你挂着 VPN / 梯子,大文件上传会被绕道海外节点,延迟高、丢包多,极易超时。直连最稳。

**5. 失败自动重试**
- 网络请求失败：最多重试 2 次,每次间隔 3 秒
- 接口偶发错误码 5001 / 5002 / 5003：最多重试 2 次,间隔 2 秒

**6. 密钥不写在源码里**
从 `.env` 文件读,`.env` 已在 `.gitignore` 里排除,不会误提交泄露。启动时如果三件套不全,直接明确报出缺哪几个变量,不会等到点了转写才报错。

**7. 临时文件自动清理**
转码产生的中间 mp3 在流程结束后自动删除(`finally` 里做,异常路径也会清理),不会在 temp 目录堆积。原始上传文件不会被删。

**8. 中文路径 / 特殊字符路径可正常处理**
使用的是列表形式传参(不是拼字符串),所以路径里有中文、有 `・` 这类特殊符号都不会出问题。已实测。

---

## 部署(Windows)

### 环境要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（64 位） |
| Python | **3.11**（3.12、3.13 一般也可以，但 3.11 最稳） |
| 网络 | 能访问 `asr.cloud.tencent.com` |
| 显卡 | 不需要，集显即可 |

### 步骤

**① 确认 Python 已安装**

```cmd
python --version
```

不是 3.11 的先装：

```cmd
winget install --id Python.Python.3.11 -e
```

安装时勾选 **Add python.exe to PATH**。

**② 进入项目目录，新建虚拟环境**

```cmd
cd /d D:\PythonProject_mp3_to_txt
python -m venv .venv
```

> ⚠️ 虚拟环境**必须在本机新建**，不要从别的电脑复制 `.venv` 目录。里面含 `.pyd` 编译二进制（如 `charset_normalizer/*.cp311-win_amd64.pyd`），绑定行定 Python 版本和系统架构，换个机器就会加载失败。

**③ 安装依赖**

```cmd
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 已经锁定版本（gradio 6.31.0 / requests 2.34.2 / imageio-ffmpeg 0.7.0 / python-dotenv 1.2.4 等），装出来和开发机一致。

**④ 配置密钥**

在项目根目录新建 `.env`：

```
TENCENT_ASR_APPID=你的APPID
TENCENT_ASR_SECRET_ID=你的SecretId
TENCENT_ASR_SECRET_KEY=你的SecretKey
```

获取密钥：https://console.cloud.tencent.com/cam/capi
开通服务（每月 5 小时免费额度）：https://console.cloud.tencent.com/asr

**⑤ 先跑自检（可选，但强烈建议）**

`test001.py` 是音频体检脚本，只验证 ffmpeg 能不能跑、文件能不能完整解码：

```cmd
.venv\Scripts\python.exe test001.py
```

**需要先修改里面的 audio 路径为你自己的文件**：

```python
audio = r"E:\chromeDownload\小宇宙音频\元.x m4a"
```

输出 `returncode = 0` 说明文件完好。如果是别的数字，文件可能损坏或被截断，换文件再试。

> 这步不做也能直接转写，它的作用是当转写出问题时分清到底是「文件坏了」还是「网络/密钥问题」。

**⑥ 启动主程序**

```cmd
.venv\Scripts\python.exe main.py
```

浏览器会自动打开 `http://127.0.0.1:7860`，上传音频、点「开始转写」就行。

如果浏览器没自动开，手动访问上面那个地址。

---

## 运行顺序总结

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
     ↓
[新建 .env 填密钥]
     ↓
.venv\Scripts\python.exe test001.py      ← 自检（可选）
     ↓
.venv\Scripts\python.exe main.py         ← 主程序
```

---

## 其他注意事项

**关于费用**
- 每月 5 小时免费额度，单条 1 小时音频约消耗 1.05 小时，所以免费额度够你转 4 条左右
- `first_channel_only=1` 只计费首个声道，双声道音频**不会翻倍扣费**
- 超额后按官方定价计费，请留意控制台余量

**关于超限**
- 超过 100MB 或超过 2 小时，极速版接口会拒收。代码会在转码后检查一次大小，超了会明确提示
- 真遇到超长音频，需要改用异步版接口（`create` + `query` 轮询），或先把音频切段

**关于保密**
- `.env` 里有真实密钥，**不要截图发群、不要提交 Git**
- 如果曾经把密钥粘贴给过别人或写进过公开文件，请去控制台**禁用该密钥并重新生成**

**关于网络**
- 如果再次遇到上传超时，先查是否有 VPN / 代理在拦截，先把代理关掉测一次
- 新机器上行带宽较差的，转码后 24MB 的体积通常能顺利传完

**关于迁移到其他电脑**
只复制这三个文件：`main.py`、`test001.py`、`requirements.txt`（以及自己新建 `.env`）。不要复制 `.venv`、`__pycache__`。

---

## 联系

邮箱：1124742205@qq.com
