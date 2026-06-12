"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, api } from "@/lib/api";
import { readToken, writeToken } from "@/lib/auth";

type State =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

export default function HrLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  useEffect(() => {
    // 已经有 token 就直接进 dashboard (避免 logged-in 用户再访问 login)
    if (readToken()) {
      router.replace("/hr");
    }
  }, [router]);

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!username || !password) return;
    setState({ kind: "submitting" });
    try {
      const tok = await api.login(username, password);
      writeToken(tok.access_token, tok.role);
      router.replace("/hr");
    } catch (e) {
      let message = "登录失败";
      if (e instanceof ApiError) {
        message = e.status === 401 ? "用户名或密码错误" : `${e.status}: ${e.message}`;
      } else if (e instanceof Error) {
        message = e.message;
      }
      setState({ kind: "error", message });
    }
  }

  return (
    <main className="flex items-center justify-center px-4 py-16 min-h-[calc(100vh-3.25rem)]">
      <div className="w-full max-w-sm">
        <h1 className="text-xl font-semibold mb-1">HR 登录</h1>
        <p className="text-sm text-zinc-500 mb-6">
          通过命令行 <code className="font-mono text-xs">scripts/seed_users</code>{" "}
          种的 HR 账号
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label htmlFor="username" className="block text-xs text-zinc-500 mb-1">
              用户名
            </label>
            <input
              id="username"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={state.kind === "submitting"}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-xs text-zinc-500 mb-1">
              密码
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={state.kind === "submitting"}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
            />
          </div>

          {state.kind === "error" && (
            <div className="rounded-md bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 p-2 text-sm text-red-700 dark:text-red-300">
              {state.message}
            </div>
          )}

          <button
            type="submit"
            disabled={state.kind === "submitting" || !username || !password}
            className="w-full rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {state.kind === "submitting" ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </main>
  );
}
