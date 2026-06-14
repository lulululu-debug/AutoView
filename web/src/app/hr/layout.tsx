"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { clearRole, readRole, writeRole } from "@/lib/auth";

/**
 * HR 端布局: 顶栏 + 鉴权 guard。
 *
 * Sprint 5.8 起鉴权方式:
 * - cookie httpOnly + SameSite=Strict, JS 读不到, 只能问 server 当前是否登录
 * - mount 时调 GET /auth/me 判 session 是否有效 (cache role 用作 UI 即时渲染,
 *   server 给的 role 覆盖 cache)
 * - 401 -> 跳 /hr/login
 * - 退出按钮调 POST /auth/logout (server 帮清 cookie) 然后跳登录
 *
 * /hr/login 是唯一不需要 session 的 HR 子路由; 其他 /hr/* 缺 session 一律
 * router.push 到 login。
 */

const LOGIN_PATH = "/hr/login";

export default function HrLayout({
  children,
}: {
  children: React.ReactNode;
  params: Promise<Record<string, never>>;
}) {
  const pathname = usePathname();
  const router = useRouter();
  // null = 还在检查 / true = 通过 / false = 被踢回 login
  const [authed, setAuthed] = useState<boolean | null>(null);
  // role 先从 cache 读 (避免 mount 时短暂无徽章), /auth/me 拿到后覆盖
  const [role, setRole] = useState<string | null>(() => readRole());

  useEffect(() => {
    let cancelled = false;
    // login 页不查 /auth/me, 让"未登录直接访问 /hr/login" 不抖闪
    if (pathname === LOGIN_PATH) {
      if (!cancelled) {
        setAuthed(true);
        setRole(null);
      }
      return;
    }
    api
      .getMe()
      .then((me) => {
        if (cancelled) return;
        writeRole(me.role);
        setRole(me.role);
        setAuthed(true);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          clearRole();
          setAuthed(false);
          router.replace(LOGIN_PATH);
        } else {
          // 其他错误 (网络 / 503): 当作"无法验证", 跳 login 让用户重试
          setAuthed(false);
          router.replace(LOGIN_PATH);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  async function handleLogout() {
    try {
      await api.logout();
    } catch {
      /* 即使 server 错也照常清本地 + 跳, 防止用户被卡在"退出按钮无反应" */
    }
    clearRole();
    setRole(null);
    router.replace(LOGIN_PATH);
  }

  if (authed === null) {
    return null;
  }

  return (
    <div className="min-h-screen flex flex-col bg-zinc-50 dark:bg-black">
      <header className="border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <Link
            href="/hr"
            className="font-medium text-sm tracking-tight hover:underline"
          >
            AI Interview · HR
          </Link>
          {pathname !== LOGIN_PATH && authed && (
            <div className="flex items-center gap-3 text-xs text-zinc-500">
              {role && (
                <span className="px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 uppercase tracking-wide">
                  {role}
                </span>
              )}
              <button
                onClick={handleLogout}
                className="hover:text-zinc-900 dark:hover:text-zinc-100"
              >
                登出
              </button>
            </div>
          )}
        </div>
      </header>
      <div className="flex-1">{children}</div>
    </div>
  );
}
