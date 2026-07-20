"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/**
 * Sprint 6-1: 会话页媒体侧 —— Consent 门 + 摄像头/麦克风生命周期 + 三区布局组件。
 * 纯前端, 不上传任何音视频数据 (录制归档是 Sprint 6-5 的事)。
 *
 * 合规要点 (改文案前先读 ARCHITECTURE.md §7 + sprint.md Sprint 6):
 * 1. 「AI 虚拟面试官」显著标识 —— 《互联网信息服务深度合成管理规定》要求
 *    AI 合成人像显著标注, 标识在 consent 门与面试官区各出现一次。
 * 2. 录制内容/用途/留存期限说明 (PIPL) —— 6-5 实装录制时, 文案与
 *    RETENTION_DAYS 必须与后端实际留存策略保持同步。
 * 3. 明示视频画面不参与自动评分 —— §7: 软信号仅参考, 绝不自动淘汰。
 * 4. 拒绝授权 / getUserMedia 失败 → 降级纯文字面试, 流程不断
 *    (与 LLM stub fallback 同款双路径哲学)。
 */

/** 录制留存期限 (天)。6-5 实装录制时以后端策略为准, 两处保持同步。 */
const RETENTION_DAYS = 90;

// ---- 摄像头/麦克风生命周期 ----

export type MediaState =
  | { kind: "idle" }
  | { kind: "requesting" }
  | { kind: "granted"; stream: MediaStream }
  | { kind: "unavailable"; reason: string };

/**
 * getUserMedia 封装: request() 申请授权, 失败落 unavailable (调用方降级纯文字)。
 * 组件卸载时自动 stop 所有 track (跳 done 页 / 离开面试不留摄像头红点)。
 */
export function useCandidateMedia() {
  const [state, setState] = useState<MediaState>({ kind: "idle" });
  const streamRef = useRef<MediaStream | null>(null);

  const request = useCallback(async (): Promise<boolean> => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setState({
        kind: "unavailable",
        reason: "当前浏览器不支持摄像头访问 (或页面非 HTTPS 安全上下文)",
      });
      return false;
    }
    setState({ kind: "requesting" });
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: "user",
        },
        // 6-4 (STT) 之前音轨尚未使用, 但一次申请两种权限避免后续二次弹窗
        audio: true,
      });
      streamRef.current = stream;
      setState({ kind: "granted", stream });
      return true;
    } catch (e) {
      setState({ kind: "unavailable", reason: mediaErrMessage(e) });
      return false;
    }
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setState({ kind: "idle" });
  }, []);

  // 卸载即释放
  useEffect(() => stop, [stop]);

  return { state, request, stop };
}

function mediaErrMessage(e: unknown): string {
  if (e instanceof DOMException) {
    if (e.name === "NotAllowedError") return "摄像头/麦克风权限被拒绝";
    if (e.name === "NotFoundError") return "未检测到摄像头或麦克风设备";
    if (e.name === "NotReadableError") return "摄像头被其他应用占用";
  }
  return e instanceof Error ? e.message : String(e);
}

// ---- Consent 门 ----

export function ConsentGate({
  requesting,
  onAccept,
  onTextOnly,
}: {
  requesting: boolean;
  onAccept: () => void;
  onTextOnly: () => void;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-6 sm:p-8">
      <AiBadge />
      <h1 className="text-2xl font-semibold mt-4 mb-2">视频面试须知</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400 mb-5">
        本场面试由 AI 虚拟面试官主持。开始前请确认以下内容:
      </p>
      <ul className="space-y-3 text-sm text-zinc-700 dark:text-zinc-300 mb-6 list-disc pl-5">
        <li>面试官的形象与语音为 AI 合成 (虚拟形象, 非真人)。</li>
        <li>
          开启摄像头与麦克风后, 面试过程的音视频可能被录制,
          仅用于招聘评估与 HR 人工复核。
        </li>
        <li>
          视频画面<strong>不参与自动评分</strong>,
          系统不会仅凭表情、眼神等信号自动淘汰候选人。
        </li>
        <li>
          录制内容最长保留 {RETENTION_DAYS} 天, 招聘流程结束后按留存策略删除。
        </li>
        <li>
          你也可以选择「仅文字作答」, 不开启摄像头与麦克风,
          不影响面试流程与评估。
        </li>
      </ul>
      <div className="flex flex-col sm:flex-row gap-3">
        <button
          onClick={onAccept}
          disabled={requesting}
          className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-5 py-2.5 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {requesting ? "等待授权中..." : "同意并开启摄像头与麦克风"}
        </button>
        <button
          onClick={onTextOnly}
          disabled={requesting}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 px-5 py-2.5 text-sm font-medium text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
        >
          仅文字作答
        </button>
      </div>
      <p className="text-xs text-zinc-400 mt-4">
        点击任一按钮即代表你已阅读并同意上述说明。
      </p>
    </div>
  );
}

/** 「AI 虚拟面试官」显著标识 (深度合成规定要求), consent 门与面试官区共用。 */
export function AiBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-900 px-3 py-1 text-xs font-medium text-blue-700 dark:text-blue-300">
      <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
      AI 虚拟面试官
    </span>
  );
}

// ---- 三区布局: 面试官区 (三态视频 avatar) + 自拍 PiP ----

/**
 * Sprint 6-3: Tier B avatar 状态。
 * - talking: TTS 音频播放中 (出题/追问播报)
 * - thinking: 提交回答后, Assessor + lazy project gen 的空档 (+ 过渡语音)
 * - idle: 其余时间 (聆听候选人)
 */
export type AvatarState = "idle" | "talking" | "thinking";

const AVATAR_STATES: readonly AvatarState[] = ["idle", "talking", "thinking"];

/**
 * 素材约定 (缺失自动退回占位面板, 不阻塞面试):
 * web/public/avatar/{idle,talking,thinking}.mp4 —— 同一人物三段可循环短片,
 * 生成规范见 web/public/avatar/README.md。Tier A (LiveTalking 真口型) 接入时
 * 本组件整体被替换, 状态语义不变。
 */
const AVATAR_SOURCES: Record<AvatarState, string> = {
  idle: "/avatar/idle.mp4",
  talking: "/avatar/talking.mp4",
  thinking: "/avatar/thinking.mp4",
};

const AVATAR_LABELS: Record<AvatarState, string> = {
  idle: "聆听中",
  talking: "提问中",
  thinking: "思考中...",
};

export function InterviewerPanel({ state }: { state: AvatarState }) {
  // 任一段视频加载失败 (素材未放/格式不对) → 整体退回占位面板,
  // 避免三态只剩两态的"半瘫"观感
  const [failed, setFailed] = useState(false);
  const idleRef = useRef<HTMLVideoElement>(null);
  const talkingRef = useRef<HTMLVideoElement>(null);
  const thinkingRef = useRef<HTMLVideoElement>(null);
  const refs: Record<AvatarState, RefObject<HTMLVideoElement | null>> = {
    idle: idleRef,
    talking: talkingRef,
    thinking: thinkingRef,
  };

  // 只播当前态的视频, 其余暂停 —— 三段常驻解码太费 CPU
  useEffect(() => {
    if (failed) return;
    for (const s of AVATAR_STATES) {
      const v = refs[s].current;
      if (!v) continue;
      if (s === state) {
        v.play().catch(() => {
          /* 自动播放被拦截: 视频静止也可接受, 不算 failed */
        });
      } else {
        v.pause();
      }
    }
    // refs 是每次 render 重建的字面量但指向稳定的三个 ref, 不进 deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, failed]);

  return (
    <div className="relative aspect-video rounded-lg overflow-hidden bg-zinc-900 dark:bg-zinc-950 border border-zinc-800">
      {failed ? (
        <PlaceholderFigure />
      ) : (
        AVATAR_STATES.map((s) => (
          <video
            key={s}
            ref={refs[s]}
            src={AVATAR_SOURCES[s]}
            loop
            muted
            playsInline
            preload="auto"
            onError={() => setFailed(true)}
            className={`absolute inset-0 w-full h-full object-cover transition-opacity duration-300 ${
              s === state ? "opacity-100" : "opacity-0"
            }`}
          />
        ))
      )}
      <div className="absolute top-2.5 left-2.5">
        <AiBadge />
      </div>
      <span className="absolute bottom-2.5 left-2.5 rounded bg-black/50 px-2 py-0.5 text-[11px] text-zinc-200">
        {AVATAR_LABELS[state]}
      </span>
    </div>
  );
}

/** 素材缺失时的占位 (Sprint 6-1 原面板), 保住布局与 AI 标识。 */
function PlaceholderFigure() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
      <div className="w-16 h-16 rounded-full bg-zinc-800 flex items-center justify-center">
        <svg
          viewBox="0 0 24 24"
          fill="currentColor"
          className="w-9 h-9 text-zinc-600"
          aria-hidden
        >
          <path d="M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0 2c-4.42 0-8 2.24-8 5v1h16v-1c0-2.76-3.58-5-8-5Z" />
        </svg>
      </div>
      <p className="text-xs text-zinc-500">面试官</p>
    </div>
  );
}

/** 候选人自拍 PiP。muted 防回声, 镜像显示更符合自拍直觉。 */
export function SelfView({ stream }: { stream: MediaStream }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
    }
  }, [stream]);

  return (
    <video
      ref={videoRef}
      autoPlay
      muted
      playsInline
      className="w-full h-full object-cover -scale-x-100"
    />
  );
}
