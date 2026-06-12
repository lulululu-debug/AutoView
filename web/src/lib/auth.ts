/**
 * HR JWT token 的 localStorage 存取 + 单一来源。
 *
 * 为什么 localStorage 而不是 httpOnly cookie:
 * - MVP 阶段够用, 前后端在不同 origin (localhost:3000 vs :8000),
 *   cookie 跨 origin 要 SameSite=None + Secure + 跨域 cookie 协议, 配置成本高
 * - 缺点: XSS 拿得到 token (但本应用是后台, 用户控制内容少)
 * - Sprint 5+ 上 prod 时换 httpOnly cookie + SameSite=Strict, 接口不变
 *
 * 候选人端 (Sprint 4) 完全不经过本模块, 走 candidate_id 路径 soft-auth。
 */

const TOKEN_KEY = "hr_jwt";
const ROLE_KEY = "hr_role";

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function writeToken(token: string, role: string) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(ROLE_KEY, role);
  } catch {
    /* 隐私模式 / 存满 等场景静默, 调用方拿不到 token 自然会跳登录 */
  }
}

export function readRole(): string | null {
  try {
    return localStorage.getItem(ROLE_KEY);
  } catch {
    return null;
  }
}

export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
  } catch {
    /* 静默 */
  }
}
