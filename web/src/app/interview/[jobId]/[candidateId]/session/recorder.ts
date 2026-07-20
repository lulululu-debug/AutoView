/**
 * Sprint 6-5: 摄像头录制归档上传。**只录不判** ——
 * 录像仅作 HR 复核素材, 前端不做任何分析, 后端打分路径也不消费 (§7)。
 *
 * 设计:
 * - MediaRecorder timeslice 5s 出分片, POST /interviews/{sid}/recordings 追加。
 * - **串行上传链**保证分片按序落盘 —— 乱序 append 会产出坏 webm。
 * - 一旦某片上传失败, 整体停止录制: 保住"已上传前缀仍是合法文件"的不变量,
 *   面试本身不受影响 (录制是增强, 不是依赖)。
 */

import { api } from "@/lib/api";

const TIMESLICE_MS = 5000;
// vp8+opus 兼容面最广; 不支持时退无参数 webm 让浏览器自选
const MIME_CANDIDATES = ["video/webm;codecs=vp8,opus", "video/webm"];

export class RecordingUploader {
  private mr: MediaRecorder | null = null;
  private queue: Promise<void> = Promise.resolve();
  private failed = false;

  /** 开始录制; 任何不支持/失败都静默返回 (录不上不挡面试)。 */
  start(stream: MediaStream, sessionId: string): void {
    if (this.mr || typeof MediaRecorder === "undefined") return;
    const mime = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
    if (!mime) return;
    let mr: MediaRecorder;
    try {
      mr = new MediaRecorder(stream, {
        mimeType: mime,
        videoBitsPerSecond: 600_000,
        audioBitsPerSecond: 48_000,
      });
    } catch {
      return;
    }
    mr.ondataavailable = (ev) => {
      if (!ev.data.size || this.failed) return;
      this.queue = this.queue.then(async () => {
        if (this.failed) return;
        const ok = await api.uploadRecordingChunk(sessionId, ev.data);
        if (!ok) {
          this.failed = true;
          this.stop();
        }
      });
    };
    try {
      mr.start(TIMESLICE_MS);
    } catch {
      return;
    }
    this.mr = mr;
  }

  /** 停止录制 (幂等)。触发最后一片 flush, 走同一条串行链上传。 */
  stop(): void {
    const mr = this.mr;
    this.mr = null;
    if (mr && mr.state !== "inactive") {
      try {
        mr.stop();
      } catch {
        /* 已停 */
      }
    }
  }
}
