#!/usr/bin/env python3
# 云端 ASR：阿里云百炼 Fun-ASR / Paraformer（DashScope SDK）
# 与本地 asr_sensevoice.py 契约一致：python3 asr_dashscope.py <音频> → 转写文本打印到 stdout
# 依赖：pip install dashscope
#
# 凭据（密钥不进本仓库）：
#   优先读环境变量 DASHSCOPE_API_KEY；
#   缺失时从 DASHSCOPE_ENV_FILE 指向的 .env 解析（默认不指定，需自行设置）。
# 环境变量：
#   DASHSCOPE_API_KEY    阿里云百炼 API Key
#   DASHSCOPE_MODEL_ASR  识别模型，默认 paraformer-realtime-v2（通用中文长语音）
#   DASHSCOPE_ENV_FILE   含 DASHSCOPE_API_KEY 的 .env 路径（可选）
import os
import sys

DEFAULT_ENV = os.environ.get("DASHSCOPE_ENV_FILE", "")


def load_api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    env_file = os.environ.get("DASHSCOPE_ENV_FILE", DEFAULT_ENV)
    if os.path.isfile(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DASHSCOPE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("未配置 DASHSCOPE_API_KEY（设环境变量，或确认 DASHSCOPE_ENV_FILE 指向含该键的 .env）")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法: python3 asr_dashscope.py <音频路径(wav/mp3，16k 单声道)>")
    audio = sys.argv[1]
    if not os.path.isfile(audio):
        sys.exit(f"找不到音频文件: {audio}")

    import dashscope
    from dashscope.audio.asr import Recognition

    dashscope.api_key = load_api_key()
    model = os.environ.get("DASHSCOPE_MODEL_ASR", "paraformer-realtime-v2")
    fmt = "wav" if audio.lower().endswith(".wav") else "mp3"

    recognition = Recognition(
        model=model,
        callback=None,
        format=fmt,
        sample_rate=16000,
        language_hints=["zh", "en"],
    )
    result = recognition.call(audio)
    if result.status_code != 200:
        sys.exit(f"ASR 调用失败: {result.status_code} {result.message}")

    sentences = result.get_sentence() or []
    if isinstance(sentences, dict):
        sentences = [sentences]
    text = "".join(s.get("text", "") for s in sentences).strip()
    if not text:
        sys.exit("ASR 未识别到内容（音频可能无人声）")
    print(text)


if __name__ == "__main__":
    main()
