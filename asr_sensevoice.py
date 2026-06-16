#!/usr/bin/env python3
# 本地 ASR：达摩院 SenseVoice-Small（中文最稳，带标点/ITN）
# 依赖：pip install funasr torch torchaudio
# 用法：python3 asr_sensevoice.py <wav路径>   # 仅转写文本打印到 stdout（日志走 stderr）
# 环境变量：
#   ASR_LANGUAGE  识别语言，默认 zh；中英混说设 auto
#   ASR_DEVICE    推理设备，默认 cpu（Apple Silicon 最稳）；可试 mps，不稳再退回
import os
import sys

if len(sys.argv) != 2:
    sys.exit("用法: python3 asr_sensevoice.py <wav路径>")
wav = sys.argv[1]
if not os.path.isfile(wav):
    sys.exit(f"找不到音频文件: {wav}")

# funasr/modelscope 会往 stdout 打版本号、下载进度等日志，会污染转写结果。
# 在模型加载/推理期间把 fd 1 重定向到 stderr，最后只把转写文本写回真正的 stdout。
_real_stdout_fd = os.dup(1)
os.dup2(2, 1)
try:
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    # 首次运行会自动下载模型（约几百 MB），之后走本地缓存
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device=os.environ.get("ASR_DEVICE", "cpu"),
        disable_update=True,
    )
    res = model.generate(
        input=wav,
        cache={},
        language=os.environ.get("ASR_LANGUAGE", "zh"),
        use_itn=True,            # 数字、标点规整
        batch_size_s=60,
        merge_vad=True,
        merge_length_s=15,
    )
    text = rich_transcription_postprocess(res[0]["text"])
finally:
    sys.stdout.flush()
    os.dup2(_real_stdout_fd, 1)   # 恢复真正的 stdout
    os.close(_real_stdout_fd)

print(text)
