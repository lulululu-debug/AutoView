"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, api } from "@/lib/api";
import { writeRole } from "@/lib/auth";

/**
 * Sprint 6.8: HR 自助注册。成功即登录 (server set cookie), 直接进 /hr。
 * 服务端配置了 REGISTER_INVITE_CODE 时邀请码必填 (403 提示)。
 */

type State =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

export default function HrRegisterPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const mismatch = password2.length > 0 && password !== password2;
  const canSubmit =
    username.trim().length >= 3 && password.length >= 8 && password === password2;

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!canSubmit) return;
    setState({ kind: "submitting" });
    try {
      const tok = await api.register(username.trim(), password, inviteCode.trim());
      writeRole(tok.role);
      router.replace("/hr");
    } catch (e) {
      let message = "注册失败";
      if (e instanceof ApiError) {
        if (e.status === 409) message = "用户名已被占用, 换一个试试";
        else if (e.status === 403) message = "邀请码错误";
        else if (e.status === 422) message = "用户名 3-32 位字母数字, 密码至少 8 位";
        else message = `${e.status}: ${e.message}`;
      } else if (e instanceof Error) {
        message = e.message;
      }
      setState({ kind: "error", message });
    }
  }

  return (
    <main className="flex items-center justify-center px-4 py-16 min-h-[calc(100vh-3.25rem)]">
      <div className="w-full max-w-sm">
        <h1 className="text-xl font-semibold mb-1">注册 HR 账号</h1>
        <p className="text-sm text-zinc-500 mb-6">
          注册后即可创建岗位、邀请候选人并查看评估报告。
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名 (3-32 位字母/数字/_-)"
            autoComplete="username"
            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            placeholder="密码 (至少 8 位)"
            autoComplete="new-password"
            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
          />
          <input
            value={password2}
            onChange={(e) => setPassword2(e.target.value)}
            type="password"
            placeholder="确认密码"
            autoComplete="new-password"
            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
          />
          {mismatch && (
            <p className="text-xs text-red-600">两次输入的密码不一致</p>
          )}
          <input
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
            placeholder="邀请码 (未开启邀请制可留空)"
            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
          />

          {state.kind === "error" && (
            <p className="text-sm text-red-600">{state.message}</p>
          )}

          <button
            type="submit"
            disabled={!canSubmit || state.kind === "submitting"}
            className="w-full rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {state.kind === "submitting" ? "注册中..." : "注册并登录"}
          </button>
        </form>

        <p className="text-sm text-zinc-500 mt-4">
          已有账号?{" "}
          <Link href="/hr/login" className="underline hover:text-zinc-700">
            去登录
          </Link>
        </p>
      </div>
    </main>
  );
}
