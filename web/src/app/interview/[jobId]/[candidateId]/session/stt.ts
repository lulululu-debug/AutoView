/**
 * Sprint 6-4: 候选人语音输入 —— 麦克风 PCM 采集 + WS 转写客户端。
 *
 * 数据流:
 *   getUserMedia stream (6-1 已授权, 复用音轨)
 *     → AudioContext(16kHz) + AudioWorklet 抓 Float32 帧
 *     → Int16 PCM, 攒 ~100ms 一片 → WS 二进制帧推后端代理
 *     ← {"type":"partial"|"final","text":累计全文} 回调上层
 *
 * 约束:
 * - 任何失败 (WS 连不上 / Worklet 不支持 / 厂商挂) → onError 一次, 上层收起
 *   录音态提示打字 —— 文本框是唯一真相源, 转写只是填进去的一种方式。
 * - partial/final 的 text 都是**累计全文** (后端契约), 上层整体替换预览。
 */

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_SAMPLES = 1600; // 100ms @ 16kHz

// AudioWorklet 模块用 Blob URL 内联, 免打包器配置。
// processor 不写 outputs (输出静音), 连 destination 只为让图保持运转。
const WORKLET_CODE = `
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

export type SpeechCallbacks = {
  onPartial: (text: string) => void;
  onFinal: (text: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
};

export class SpeechCapture {
  private ctx: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private ws: WebSocket | null = null;
  private chunks: Int16Array[] = [];
  private pendingSamples = 0;
  private capturing = false;
  private disposed = false;
  private settled = false; // done/error 只回调一次

  /** 建 WS + 音频管线并开始推流。抛错 = 没起来, 上层直接收起录音态。 */
  async start(
    stream: MediaStream,
    wsUrl: string,
    cb: SpeechCallbacks,
  ): Promise<void> {
    // 1. WS 先通 —— 连不上就不碰音频管线
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("转写服务连接失败"));
      ws.onclose = () => reject(new Error("转写服务连接失败"));
    });

    const settle = (fn: () => void) => {
      if (this.settled || this.disposed) return;
      this.settled = true;
      fn();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string" || this.disposed) return;
      let msg: { type?: string; text?: string; message?: string };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "partial") cb.onPartial(msg.text ?? "");
      else if (msg.type === "final") cb.onFinal(msg.text ?? "");
      else if (msg.type === "done") settle(() => cb.onDone());
      else if (msg.type === "error")
        settle(() => cb.onError(msg.message ?? "转写失败"));
    };
    ws.onerror = () => settle(() => cb.onError("转写连接中断"));
    // 后端总是先发 done/error 再关; 没等到就断说明链路异常
    ws.onclose = () => settle(() => cb.onError("转写连接中断"));

    // 2. 音频管线: 16kHz 由 AudioContext 直接重采样
    const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    this.ctx = ctx;
    const blobUrl = URL.createObjectURL(
      new Blob([WORKLET_CODE], { type: "application/javascript" }),
    );
    try {
      await ctx.audioWorklet.addModule(blobUrl);
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "pcm-capture");
    node.port.onmessage = (ev) => this.onPcm(ev.data as Float32Array);
    source.connect(node);
    node.connect(ctx.destination); // 输出静音, 仅维持处理图运转
    this.source = source;
    this.node = node;
    this.capturing = true;
  }

  /** 说完了: 停采集, 冲掉尾巴, 发 finish 让厂商定稿。WS 留着等 final/done。 */
  finish(): void {
    if (!this.capturing) return;
    this.capturing = false;
    this.stopAudio();
    this.flush();
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "finish" }));
    }
  }

  /** 无条件拆干净 (幂等)。上层在 done/error/卸载时调。 */
  dispose(): void {
    this.disposed = true;
    this.capturing = false;
    this.stopAudio();
    if (this.ws) {
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      try {
        this.ws.close();
      } catch {
        /* 已关 */
      }
      this.ws = null;
    }
  }

  private onPcm(f32: Float32Array): void {
    if (!this.capturing || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.chunks.push(i16);
    this.pendingSamples += i16.length;
    if (this.pendingSamples >= CHUNK_SAMPLES) this.flush();
  }

  private flush(): void {
    if (
      !this.ws ||
      this.ws.readyState !== WebSocket.OPEN ||
      this.pendingSamples === 0
    ) {
      return;
    }
    const merged = new Int16Array(this.pendingSamples);
    let off = 0;
    for (const c of this.chunks) {
      merged.set(c, off);
      off += c.length;
    }
    this.chunks = [];
    this.pendingSamples = 0;
    this.ws.send(merged.buffer);
  }

  private stopAudio(): void {
    try {
      this.source?.disconnect();
      this.node?.disconnect();
    } catch {
      /* 已断 */
    }
    this.source = null;
    this.node = null;
    if (this.ctx && this.ctx.state !== "closed") {
      this.ctx.close().catch(() => {});
    }
    this.ctx = null;
  }
}
