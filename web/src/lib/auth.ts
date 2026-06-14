/**
 * HR 端鉴权前端工具 —— Sprint 5.8 改成 httpOnly cookie。
 *
 * 状态:
 * - JWT token 由后端 Set-Cookie httpOnly + SameSite=Strict, JS 读不到 / 写不到 /
 *   清不掉。Login 时 server set; logout 时 server clear; 续约 / 过期都由 server 管。
 * - 前端不再存 token 在 localStorage。
 * - 只 cache role 在 localStorage, 让顶栏徽章在 mount 时立刻能渲染, 同时由
 *   /auth/me 二次确认 (response 含 role, 会覆盖 cache).
 *
 * 候选人端 (Sprint 4) 完全不经过本模块, 走 candidate_id 路径 soft-auth。
 */

const ROLE_KEY = "hr_role";

export function readRole(): string | null {
  try {
    return localStorage.getItem(ROLE_KEY);
  } catch {
    return null;
  }
}

export function writeRole(role: string) {
  try {
    localStorage.setItem(ROLE_KEY, role);
  } catch {
    /* 隐私模式 / 存满: 静默, role 只是 UI 提示, 没了 server 会重新给 */
  }
}

export function clearRole() {
  try {
    localStorage.removeItem(ROLE_KEY);
  } catch {
    /* 静默 */
  }
}
