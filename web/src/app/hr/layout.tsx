"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { clearToken, readRole, readToken } from "@/lib/auth";

/**
 * HR 端布局: 顶栏 + 鉴权 guard。
 *
 * Guard 在客户端跑 (next.js 16 middleware 在 edge 上拿不到 localStorage,
 * 移动到 cookie 才能上 middleware; MVP 用 client-side guard, 接受短暂 flash)。
 *
 * /hr/login 是唯一不需要 token 的 HR 子路由; 其他 /hr/* 缺 token 一律
 * router.push 到 login。
 */

const LOGIN_PATH = "/hr/login";

// Next.js 16 typed routes 要求 layout 显式声明 params (即便没用); 否则
// .next/dev/types/validator.ts 那层校验过不去。
export default function HrLayout({
  children,
}: {
  children: React.ReactNode;
  params: Promise<Record<string, never>>;
}) {
  const pathname = usePathname();
  const router = useRouter();
  // null = 还在检查; true = 通过; false = 被踢回 login
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [role, setRole] = useState<string | null>(null);

  useEffect(() => {
    const token = readToken();
    if (!token) {
      if (pathname !== LOGIN_PATH) {
        router.replace(LOGIN_PATH);
        setAuthed(false);
        return;
      }
      // 已经在 login 页, 不需要 token
      setAuthed(true);
      setRole(null);
      return;
    }
    setAuthed(true);
    setRole(readRole());
  }, [pathname, router]);

  function handleLogout() {
    clearToken();
    router.replace(LOGIN_PATH);
  }

  if (authed === null) {
    // 短暂 flash, 不渲染内容避免内容被未鉴权的用户看到
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
          {pathname !== LOGIN_PATH && readToken() && (
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
