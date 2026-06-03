# -*- coding: utf-8 -*-
"""
讯飞离线语音唤醒(AIKit/ivw70) + 唤醒后按豆包全局快捷键
依赖: pip install keyboard   (录音用 Windows 自带 winmm,无需 sounddevice)
放置位置: 把本文件放进 SDK 的 bin 目录(和 resource/ testAudio/ 同级)
运行(必须 64 位 Python): python wake_doubao_xfei.py
"""
import os
import re
import sys
import time
import array
import threading
import winsound
import winreg
import configparser
import ctypes as C
from ctypes import wintypes

# ============ 配置(快捷键等也可在 config.ini 里改,优先级更高) ============
# 讯飞凭据请填到同目录 config.ini 的 [doubao] 段(appid/apikey/apisecret),
# 不要写在本文件里,以免上传 git 泄露。这里保持空即可。
APPID = APIKEY = APISECRET = ""
DOUBAO_WAKE_HOTKEY   = "alt+d"   # 喊"豆包豆包" -> 唤起豆包语音
DOUBAO_HANGUP_HOTKEY = "alt+q"   # 喊"豆包闭嘴" -> 挂断豆包语音
HANGUP_INDEX = 1                 # "闭嘴"字串判断为主,索引仅兜底
COOLDOWN   = 2.0                 # 同一动作冷却秒数,防连环触发
MIC_DEVICE = -1                  # 麦克风设备号:-1=系统默认。喊话没反应时,看启动列表改成对应数字
SHOW_LEVEL = False               # True=每秒打印麦克风音量(自检用),调通后设 False 保持清爽
BEEP       = True                # 唤起/挂断时播放提示音(像按住语音键那种"叮")
WAKE_BEEP_DELAY = 0.5            # 唤起音延迟秒数:等豆包语音界面起来后再"叮",更像"可以说话了"
KEEP_AWAKE = True                # 防止系统休眠:关屏后仍持续监听(显示器可正常黑屏)
# 唤醒词 resource/ivw70/xbxb.txt(UTF-8):多个词用英文分号; 分隔且结尾带; 例:豆包豆包;豆包闭嘴;
# ==================================

import keyboard

# 托盘图标(可选):缺 pystray/Pillow 时自动降级为控制台模式
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

# ---------- 路径 ----------
BIN_DIR = os.path.dirname(os.path.abspath(__file__))          # SDK 的 bin 目录
LIB_DIR = os.path.abspath(os.path.join(BIN_DIR, "..", "libs", "64"))
KW_PATH = os.path.join(BIN_DIR, "resource", "ivw70", "xbxb.txt")
SCRIPT_PATH = os.path.abspath(__file__)
CONFIG_PATH = os.path.join(BIN_DIR, "config.ini")   # 用户可改的配置(快捷键等)
ABILITY = b"e867a88f2"

# pythonw(无控制台/后台)时 stdout 为 None,print 会崩。重定向到 run.log 便于排查后台问题
if sys.stdout is None or sys.stderr is None:
    try:
        _logf = open(os.path.join(BIN_DIR, "run.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _logf
        if sys.stderr is None:
            sys.stderr = _logf
    except Exception:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")

# ---------- 加载 DLL ----------
# 把 libs/64 加进 PATH,让引擎在运行时 LoadLibrary 的子DLL(ef7d69542_*.dll)能被找到
# (Windows 默认 LoadLibrary 不搜 libs/64,PATH 是其默认搜索路径之一)
os.environ["PATH"] = LIB_DIR + os.pathsep + os.environ.get("PATH", "")
os.add_dll_directory(LIB_DIR)
lib = C.CDLL(os.path.join(LIB_DIR, "AEE_lib.dll"))

# ---------- 结构体 ----------
class AIKIT_BaseParam(C.Structure):
    pass
AIKIT_BaseParam._fields_ = [
    ("next", C.POINTER(AIKIT_BaseParam)), ("key", C.c_char_p),
    ("value", C.c_void_p), ("reserved", C.c_void_p),
    ("len", C.c_int32), ("type", C.c_int32),
]
AIKIT_BizParam = AIKIT_BaseParam

class AIKIT_BaseData(C.Structure):
    pass
AIKIT_BaseData._fields_ = [
    ("next", C.POINTER(AIKIT_BaseData)), ("desc", C.POINTER(AIKIT_BaseParam)),
    ("key", C.c_char_p), ("value", C.c_void_p), ("reserved", C.c_void_p),
    ("len", C.c_int32), ("type", C.c_int32), ("status", C.c_int32), ("from_", C.c_int32),
]
AIKIT_InputData = AIKIT_BaseData

class AIKIT_CustomData(C.Structure):
    pass
AIKIT_CustomData._fields_ = [
    ("next", C.POINTER(AIKIT_CustomData)), ("key", C.c_char_p),
    ("value", C.c_void_p), ("reserved", C.c_void_p),
    ("index", C.c_int32), ("len", C.c_int32), ("from_", C.c_int32),
]

class AIKIT_BaseDataList(C.Structure):
    _fields_ = [("node", C.POINTER(AIKIT_BaseData)), ("count", C.c_int32), ("totalLen", C.c_int32)]
AIKIT_OutputData = AIKIT_BaseDataList

class AIKIT_BaseParamList(C.Structure):
    _fields_ = [("node", C.POINTER(AIKIT_BaseParam)), ("count", C.c_int32), ("totalLen", C.c_int32)]

class AIKIT_HANDLE(C.Structure):
    _fields_ = [("usrContext", C.c_void_p), ("abilityID", C.c_char_p), ("handleID", C.c_size_t)]

ON_OUTPUT = C.CFUNCTYPE(None, C.POINTER(AIKIT_HANDLE), C.POINTER(AIKIT_BaseDataList))
ON_EVENT  = C.CFUNCTYPE(None, C.POINTER(AIKIT_HANDLE), C.c_int, C.POINTER(AIKIT_BaseParamList))
ON_ERROR  = C.CFUNCTYPE(None, C.POINTER(AIKIT_HANDLE), C.c_int32, C.c_char_p)

class AIKIT_Callbacks(C.Structure):
    _fields_ = [("outputCB", ON_OUTPUT), ("eventCB", ON_EVENT), ("errorCB", ON_ERROR)]

class AIKIT_InitParam(C.Structure):
    _fields_ = [
        ("authType", C.c_int), ("appID", C.c_char_p), ("apiKey", C.c_char_p),
        ("apiSecret", C.c_char_p), ("workDir", C.c_char_p), ("resDir", C.c_char_p),
        ("licenseFile", C.c_char_p), ("batchID", C.c_char_p), ("UDID", C.c_char_p),
        ("cfgFile", C.c_char_p),
    ]

class BuilderData(C.Structure):
    _fields_ = [("type", C.c_int), ("name", C.c_char_p), ("data", C.c_void_p),
                ("len", C.c_int), ("status", C.c_int)]

# ---------- 函数签名 ----------
lib.AIKIT_Init.argtypes = [C.POINTER(AIKIT_InitParam)]; lib.AIKIT_Init.restype = C.c_int32
lib.AIKIT_UnInit.restype = C.c_int32
lib.AIKIT_RegisterAbilityCallback.argtypes = [C.c_char_p, AIKIT_Callbacks]; lib.AIKIT_RegisterAbilityCallback.restype = C.c_int32
lib.AIKIT_EngineInit.argtypes = [C.c_char_p, C.POINTER(AIKIT_BizParam)]; lib.AIKIT_EngineInit.restype = C.c_int32
lib.AIKIT_EngineUnInit.argtypes = [C.c_char_p]; lib.AIKIT_EngineUnInit.restype = C.c_int32
lib.AIKIT_LoadData.argtypes = [C.c_char_p, C.POINTER(AIKIT_CustomData)]; lib.AIKIT_LoadData.restype = C.c_int32
lib.AIKIT_SpecifyDataSet.argtypes = [C.c_char_p, C.c_char_p, C.POINTER(C.c_int), C.c_int]; lib.AIKIT_SpecifyDataSet.restype = C.c_int32
lib.AIKIT_Start.argtypes = [C.c_char_p, C.POINTER(AIKIT_BizParam), C.c_void_p, C.POINTER(C.POINTER(AIKIT_HANDLE))]; lib.AIKIT_Start.restype = C.c_int32
lib.AIKIT_Write.argtypes = [C.POINTER(AIKIT_HANDLE), C.POINTER(AIKIT_InputData)]; lib.AIKIT_Write.restype = C.c_int32
lib.AIKIT_End.argtypes = [C.POINTER(AIKIT_HANDLE)]; lib.AIKIT_End.restype = C.c_int32

lib.AIKITBuilder_Create.argtypes = [C.c_int]; lib.AIKITBuilder_Create.restype = C.c_void_p
lib.AIKITBuilder_AddString.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int]
lib.AIKITBuilder_AddBool.argtypes = [C.c_void_p, C.c_char_p, C.c_bool]
lib.AIKITBuilder_AddBuf.argtypes = [C.c_void_p, C.POINTER(BuilderData)]
lib.AIKITBuilder_BuildParam.argtypes = [C.c_void_p]; lib.AIKITBuilder_BuildParam.restype = C.POINTER(AIKIT_BizParam)
lib.AIKITBuilder_BuildData.argtypes = [C.c_void_p]; lib.AIKITBuilder_BuildData.restype = C.POINTER(AIKIT_InputData)
lib.AIKITBuilder_Destroy.argtypes = [C.c_void_p]

# ---------- Windows 麦克风 (winmm, 无需第三方库) ----------
winmm = C.windll.winmm
WAVE_FORMAT_PCM = 1
WAVE_MAPPER     = 0xFFFFFFFF
WHDR_DONE       = 0x00000001

class WAVEFORMATEX(C.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]

class WAVEHDR(C.Structure):
    pass
WAVEHDR._fields_ = [
    ("lpData", C.c_char_p), ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", C.c_void_p),
    ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
    ("lpNext", C.c_void_p), ("reserved", C.c_void_p),
]

winmm.waveInOpen.argtypes = [C.POINTER(C.c_void_p), wintypes.UINT, C.POINTER(WAVEFORMATEX),
                             C.c_void_p, C.c_void_p, wintypes.DWORD]
winmm.waveInPrepareHeader.argtypes   = [C.c_void_p, C.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveInUnprepareHeader.argtypes = [C.c_void_p, C.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveInAddBuffer.argtypes       = [C.c_void_p, C.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveInStart.argtypes = [C.c_void_p]
winmm.waveInStop.argtypes  = [C.c_void_p]
winmm.waveInReset.argtypes = [C.c_void_p]
winmm.waveInClose.argtypes = [C.c_void_p]

MAXPNAMELEN = 32
class WAVEINCAPSW(C.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD),
    ]
winmm.waveInGetNumDevs.restype = wintypes.UINT
winmm.waveInGetDevCapsW.argtypes = [C.c_size_t, C.POINTER(WAVEINCAPSW), wintypes.UINT]

def list_mics():
    n = winmm.waveInGetNumDevs()
    print(f"---- 录音设备列表(共 {n} 个) ----")
    for i in range(n):
        caps = WAVEINCAPSW()
        if winmm.waveInGetDevCapsW(i, C.byref(caps), C.sizeof(caps)) == 0:
            print(f"   [{i}] {caps.szPname}")
    print("   [-1] 系统默认设备")
    print("--------------------------------")

# ---------- 回调 ----------
_last = {"action": None, "t": 0.0}
_state = {                                  # 托盘/配置运行时状态(config.ini 会覆盖)
    "paused": False, "icon": None,
    "appid": APPID, "apikey": APIKEY, "apisecret": APISECRET,
    "wake_hotkey": DOUBAO_WAKE_HOTKEY, "hangup_hotkey": DOUBAO_HANGUP_HOTKEY,
    "beep": BEEP, "wake_beep_delay": WAKE_BEEP_DELAY,
    "cooldown": COOLDOWN, "mic_device": MIC_DEVICE,
}
_stop = threading.Event()                  # 退出信号

# ---------- 配置文件 config.ini(用户可用记事本改快捷键等,无需动代码) ----------
def apply_config():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        try: cfg.read(CONFIG_PATH, encoding="utf-8")
        except Exception: pass
    sec = "doubao"
    if not cfg.has_section(sec):
        cfg.add_section(sec)

    def g(k):
        v = cfg.get(sec, k, fallback=None)
        return v.strip() if v is not None else None

    for ck in ("appid", "apikey", "apisecret"):     # 讯飞凭据
        if g(ck): _state[ck] = g(ck)
    if g("wake_hotkey"):   _state["wake_hotkey"]   = g("wake_hotkey")
    if g("hangup_hotkey"): _state["hangup_hotkey"] = g("hangup_hotkey")
    b = g("beep")
    if b is not None:      _state["beep"] = b.lower() not in ("0", "false", "no", "off", "")
    for key, cast in (("wake_beep_delay", float), ("cooldown", float), ("mic_device", int)):
        v = g(key)
        if v:
            try: _state[key] = cast(v)
            except Exception: pass

    # 回写:确保 config.ini 存在且含全部项 + 注释说明
    need_write = not os.path.exists(CONFIG_PATH)
    pairs = [("appid", _state["appid"]), ("apikey", _state["apikey"]), ("apisecret", _state["apisecret"]),
             ("wake_hotkey", _state["wake_hotkey"]), ("hangup_hotkey", _state["hangup_hotkey"]),
             ("beep", "1" if _state["beep"] else "0"),
             ("wake_beep_delay", _state["wake_beep_delay"]), ("cooldown", _state["cooldown"]),
             ("mic_device", _state["mic_device"])]
    for k, v in pairs:
        if not cfg.has_option(sec, k):
            cfg.set(sec, k, str(v)); need_write = True
    if need_write:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("; ===== 豆包语音唤醒 配置文件 =====\n")
                f.write("; 改完保存后:右键托盘图标 -> 重新载入配置 即可生效(mic_device/凭据 改动需重启)\n")
                f.write("; appid / apikey / apisecret  讯飞开放平台->你的应用 里的三个凭据(必填)\n")
                f.write("; wake_hotkey   唤起豆包语音的快捷键,例 alt+d / ctrl+alt+v / ctrl+space\n")
                f.write("; hangup_hotkey 挂断语音的快捷键,例 alt+q\n")
                f.write("; beep 1开/0关  wake_beep_delay 唤起提示音延迟(秒)  cooldown 冷却(秒)  mic_device -1=系统默认\n\n")
                cfg.write(f)
        except Exception: pass

# ---------- 开机自启(写注册表 HKCU\...\Run,纯标准库) ----------
RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DoubaoWake"

def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except OSError:
        return False

def autostart_set(enable):
    try:
        if enable:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pyw):
                pyw = sys.executable
            cmd = '"%s" "%s"' % (pyw, SCRIPT_PATH)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_NAME)
    except OSError:
        pass

def _beep(action):
    if not _state["beep"]:
        return
    try:
        if action == "wake":
            time.sleep(_state["wake_beep_delay"])              # 等豆包语音界面起来后再响
            winsound.Beep(988, 90); winsound.Beep(1319, 110)   # 上行双音:提示"可以说话了"
        else:
            winsound.Beep(660, 130)                            # 挂断:单个低音
    except Exception:
        pass

def _fire(action, hotkey, label):
    if _state["paused"]:        # 托盘里点了"暂停监听"则不动作
        return
    now = time.time()
    # 同一动作短时间内只触发一次;不同动作(唤起/挂断)可立即切换
    if action == _last["action"] and now - _last["t"] < _state["cooldown"]:
        return
    _last["action"] = action
    _last["t"] = now
    print(label, "->", hotkey)
    keyboard.send(hotkey)
    threading.Thread(target=_beep, args=(action,), daemon=True).start()  # 异步播提示音,不阻塞

def _on_output(handle, output):
    txt = ""
    try:
        node = output.contents.node
        if node:
            n = node.contents
            txt = C.string_at(n.value, n.len).decode("utf-8", "ignore")
            print("识别输出:", txt)
    except Exception:
        pass
    # 判断命中的是哪个词:"豆包闭嘴"->挂断,否则->唤起
    is_hangup = ("闭嘴" in txt)
    if not is_hangup:
        m = re.search(r'id"?\s*[:=]\s*"?(\d+)', txt)  # 兜底:用关键词索引判断
        if m and int(m.group(1)) == HANGUP_INDEX:
            is_hangup = True
    if is_hangup:
        _fire("hangup", _state["hangup_hotkey"], "🔇 豆包闭嘴!挂断语音")
    else:
        _fire("wake", _state["wake_hotkey"], "🔔 唤醒!进入语音")

def _on_event(handle, etype, ev):
    pass

def _on_error(handle, err, desc):
    print("OnError:", err, desc)

_refs = []  # 防止回调被 GC

def _fatal(msg):
    """致命错误:打印 -> 托盘弹气泡(后台模式也能看到)-> 退出"""
    print(msg)
    ic = _state.get("icon")
    if ic:
        try: ic.notify(str(msg)[:240], "豆包语音唤醒启动失败")
        except Exception: pass
        try: time.sleep(2.5); ic.stop()   # 让气泡显示一会儿
        except Exception: pass
    _stop.set()

def run_engine():
    # 0a. 防止系统休眠,让"关屏后"仍持续监听(只保持系统唤醒,显示器仍可正常黑屏)
    if KEEP_AWAKE:
        try:
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            C.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            print("🟢 已开启防休眠:关屏幕也会继续监听")
        except Exception as e:
            print("防休眠设置失败(不影响唤醒):", e)

    # 0. 把 SDK 日志重定向到文件,保持控制台干净(便于看自检/唤醒打印)
    for fn, argt, val in [("AIKIT_SetLogLevel", [C.c_int32], 4),
                          ("AIKIT_SetLogMode", [C.c_int32], 2),
                          ("AIKIT_SetLogPath", [C.c_char_p], os.path.join(BIN_DIR, "aikit.log").encode("utf-8"))]:
        try:
            f = getattr(lib, fn); f.argtypes = argt; f(val)
        except Exception:
            pass

    # 1. 初始化 SDK(凭据来自 config.ini)
    p = AIKIT_InitParam()
    p.authType = 0
    p.appID = _state["appid"].encode()
    p.apiKey = _state["apikey"].encode()
    p.apiSecret = _state["apisecret"].encode()
    p.workDir = BIN_DIR.encode("utf-8")
    r = lib.AIKIT_Init(C.byref(p))
    if r != 0:
        _fatal("AIKIT_Init 失败: %d (常见: 鉴权失败/未联网首次激活/key错)" % r); return

    # 2. 注册回调
    out_c, ev_c, err_c = ON_OUTPUT(_on_output), ON_EVENT(_on_event), ON_ERROR(_on_error)
    cbs = AIKIT_Callbacks(out_c, ev_c, err_c)
    _refs.extend([out_c, ev_c, err_c, cbs])
    lib.AIKIT_RegisterAbilityCallback(ABILITY, cbs)

    # 3. 引擎初始化
    r = lib.AIKIT_EngineInit(ABILITY, None)
    if r != 0:
        _fatal("AIKIT_EngineInit 失败: %d" % r); return

    # 4. 加载唤醒词
    cd = AIKIT_CustomData()
    cd.key = b"key_word"
    cd.index = 0
    cd.from_ = 2  # AIKIT_DATA_PTR_PATH
    val = KW_PATH.encode("utf-8")
    cd.value = C.cast(C.c_char_p(val), C.c_void_p)
    cd.len = len(val)
    r = lib.AIKIT_LoadData(ABILITY, C.byref(cd))
    if r != 0:
        _fatal("AIKIT_LoadData 失败: %d  唤醒词文件: %s" % (r, KW_PATH)); return

    idx = (C.c_int * 1)(0)
    lib.AIKIT_SpecifyDataSet(ABILITY, b"key_word", idx, 1)

    # 5. 构建参数并启动会话
    pb = lib.AIKITBuilder_Create(0)  # PARAM
    # 不覆盖置信度阈值:让两个唤醒词都用引擎默认阈值。
    # 之前 "0 0:999" 只把关键词0(豆包豆包)设成 999=很严格 -> 识别不到;关键词1(豆包闭嘴)
    # 走默认反而能识别。所以移除覆盖,让两个词都用同一套(宽松的)默认阈值。
    lib.AIKITBuilder_AddBool(pb, b"gramLoad", True)
    param = lib.AIKITBuilder_BuildParam(pb)

    handle = C.POINTER(AIKIT_HANDLE)()
    r = lib.AIKIT_Start(ABILITY, param, None, C.byref(handle))
    lib.AIKITBuilder_Destroy(pb)
    if r != 0:
        _fatal("AIKIT_Start 失败: %d" % r); return

    # 6. 喂数据给引擎
    first = [True]
    def feed(pcm):
        db = lib.AIKITBuilder_Create(1)  # DATA
        bd = BuilderData()
        bd.type = 1                       # AUDIO
        bd.name = b"wav"
        buf = (C.c_char * len(pcm)).from_buffer_copy(pcm)
        bd.data = C.cast(buf, C.c_void_p)
        bd.len = len(pcm)
        bd.status = 0 if first[0] else 1  # 0=首 1=中
        first[0] = False
        lib.AIKITBuilder_AddBuf(db, C.byref(bd))
        inp = lib.AIKITBuilder_BuildData(db)
        lib.AIKIT_Write(handle, inp)      # buf 在本函数内存活,同步调用安全
        lib.AIKITBuilder_Destroy(db)

    # 7. 打开麦克风 (Windows winmm, 16k/16bit/单声道)
    list_mics()
    mic = _state["mic_device"]
    dev = WAVE_MAPPER if mic < 0 else mic
    print(f"使用麦克风设备号: {mic}{' (系统默认)' if mic < 0 else ''}")
    BUF_BYTES, NBUF = 2560, 8          # 每块 ~80ms,8 块双缓冲
    hwi = C.c_void_p()
    wfx = WAVEFORMATEX(WAVE_FORMAT_PCM, 1, 16000, 32000, 2, 16, 0)
    r = winmm.waveInOpen(C.byref(hwi), dev, C.byref(wfx), None, None, 0)
    if r != 0:
        lib.AIKIT_End(handle); lib.AIKIT_EngineUnInit(ABILITY); lib.AIKIT_UnInit()
        _fatal("waveInOpen 失败: %d (麦克风打不开,检查录音设备/权限,或换 MIC_DEVICE 设备号)" % r)
        return

    bufs = []
    for _ in range(NBUF):
        mem = C.create_string_buffer(BUF_BYTES)
        hdr = WAVEHDR()
        hdr.lpData = C.cast(mem, C.c_char_p)
        hdr.dwBufferLength = BUF_BYTES
        winmm.waveInPrepareHeader(hwi, C.byref(hdr), C.sizeof(hdr))
        winmm.waveInAddBuffer(hwi, C.byref(hdr), C.sizeof(hdr))
        bufs.append((hdr, mem))
    winmm.waveInStart(hwi)

    print("✅ 讯飞唤醒已启动:喊'豆包豆包'唤起语音,喊'豆包闭嘴'挂断语音。" +
          ("托盘图标右键可退出。" if HAS_TRAY else "Ctrl+C 退出。"))
    print(f"   当前快捷键:唤起={_state['wake_hotkey']}  挂断={_state['hangup_hotkey']}  (可在 config.ini 改)")
    if SHOW_LEVEL:
        print("   (自检:喊话时'麦克风电平'应明显跳动到几千;若一直≈0就是没录到音)")
    last_t, lvl, fed = time.time(), 0, 0
    try:
        while not _stop.is_set():
            did = False
            for hdr, mem in bufs:
                if hdr.dwFlags & WHDR_DONE:
                    n = hdr.dwBytesRecorded
                    if n:
                        pcm = mem.raw[:n]
                        feed(pcm)
                        fed += 1
                        if SHOW_LEVEL:
                            a = array.array('h'); a.frombytes(pcm[:(n // 2) * 2])
                            if a:
                                m = max(a.tolist(), key=abs)
                                lvl = max(lvl, abs(m))
                    hdr.dwFlags &= ~WHDR_DONE          # 保留 PREPARED 位,清 DONE 位
                    hdr.dwBytesRecorded = 0
                    winmm.waveInAddBuffer(hwi, C.byref(hdr), C.sizeof(hdr))
                    did = True
            if SHOW_LEVEL and time.time() - last_t >= 1.0:
                bar = "#" * min(40, lvl // 200)
                print(f"🎙 麦克风电平 max={lvl:5d} {bar}  (本秒喂{fed}块)")
                last_t, lvl, fed = time.time(), 0, 0
            if not did:
                time.sleep(0.01)
    except KeyboardInterrupt:
        print("退出中...")
    finally:
        try:
            winmm.waveInStop(hwi); winmm.waveInReset(hwi)
            for hdr, mem in bufs:
                winmm.waveInUnprepareHeader(hwi, C.byref(hdr), C.sizeof(hdr))
            winmm.waveInClose(hwi)
        except Exception:
            pass
        lib.AIKIT_End(handle)
        lib.AIKIT_EngineUnInit(ABILITY)
        lib.AIKIT_UnInit()
        if KEEP_AWAKE:                       # 解除防休眠,恢复系统正常休眠策略
            try: C.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception: pass

# ---------- 托盘图标 ----------
def _tray_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=color)          # 圆形底
    d.rectangle((28, 20, 36, 38), fill=(255, 255, 255))   # 简易麦克风
    d.ellipse((24, 34, 40, 46), fill=(255, 255, 255))
    return img

def _tray_title():
    return "豆包语音唤醒 - " + ("已暂停" if _state["paused"] else "监听中")

def _on_toggle(icon, item):
    _state["paused"] = not _state["paused"]
    icon.icon = _tray_image((210, 90, 90) if _state["paused"] else (90, 180, 100))
    icon.title = _tray_title()

def _on_beep(icon, item):
    _state["beep"] = not _state["beep"]

def _on_autostart(icon, item):
    autostart_set(not autostart_enabled())

def _on_open_config(icon, item):
    try: os.startfile(CONFIG_PATH)          # 用记事本打开 config.ini 改快捷键
    except Exception as e: print("打开配置失败:", e)

def _on_reload_config(icon, item):
    apply_config()
    icon.title = _tray_title()
    try: icon.notify("配置已重新载入  唤起=%s 挂断=%s" % (_state["wake_hotkey"], _state["hangup_hotkey"]),
                     "豆包语音唤醒")
    except Exception: pass

def _on_quit(icon, item):
    _stop.set()
    icon.visible = False
    icon.stop()

def run_tray():
    menu = pystray.Menu(
        pystray.MenuItem("喊「豆包豆包」唤起语音", None, enabled=False),   # 信息行1
        pystray.MenuItem("喊「豆包闭嘴」挂断语音", None, enabled=False),   # 信息行2
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda i: "▶ 恢复监听" if _state["paused"] else "⏸ 暂停监听", _on_toggle),
        pystray.MenuItem("提示音", _on_beep, checked=lambda i: _state["beep"]),
        pystray.MenuItem("开机自启", _on_autostart, checked=lambda i: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开配置文件(改快捷键)", _on_open_config),
        pystray.MenuItem("重新载入配置", _on_reload_config),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _on_quit),
    )
    icon = pystray.Icon("doubao_wake", _tray_image((90, 180, 100)), _tray_title(), menu)
    _state["icon"] = icon
    t = threading.Thread(target=run_engine, daemon=True)
    t.start()
    icon.run()                 # 阻塞主线程,直到点"退出"
    _stop.set()
    t.join(timeout=3)

_mutex = None  # 单实例锁,需全局持有防 GC

def main():
    global _mutex
    apply_config()   # 先读 config.ini(首次自动生成),把凭据/快捷键等覆盖进 _state

    # 校验讯飞凭据已在 config.ini 填好
    creds = (_state["appid"], _state["apikey"], _state["apisecret"])
    if not all(creds) or any(("填" in c or "你的" in c.lower() or "your" in c.lower()) for c in creds):
        msg = "请先在 config.ini 的 [doubao] 段填写讯飞 appid / apikey / apisecret"
        print("❌ " + msg + "  (文件: %s)" % CONFIG_PATH)
        ic = _state.get("icon")
        if ic:
            try: ic.notify(msg, "豆包语音唤醒")
            except Exception: pass
        sys.exit(1)

    # 单实例:已在运行就退出,避免两个图标 / 按键翻倍
    try:
        _mutex = C.windll.kernel32.CreateMutexW(None, False, "Global\\DoubaoWakeSingleton")
        if C.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print("豆包语音唤醒已在运行,本次退出。")
            sys.exit(0)
    except Exception:
        pass

    if HAS_TRAY:
        run_tray()             # 托盘模式:右键图标可暂停/退出/开机自启
    else:
        print("（未装 pystray/Pillow,以控制台模式运行;装上可获得托盘图标）")
        run_engine()           # 控制台模式:Ctrl+C 退出

if __name__ == "__main__":
    main()
