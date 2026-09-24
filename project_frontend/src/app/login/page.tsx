"use client";

import { FormEvent, Suspense, useMemo, useState } from "react";
import { Eye, EyeOff, Lock, Mail } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { mockAccounts, writeAuthSession } from "../../auth/session";
import { cardClassName } from "../../shared/ui";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const nextPath = useMemo(() => searchParams.get("next") || "", [searchParams]);

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setIsLoading(true);
    setError("");

    window.setTimeout(() => {
      const account = mockAccounts.find(
        (item) => item.email.toLowerCase() === email.trim().toLowerCase() && item.password === password
      );
      setIsLoading(false);
      if (!account) {
        setError("อีเมลหรือรหัสผ่านไม่ถูกต้อง");
        return;
      }

      writeAuthSession({ id: account.id, userId: account.id, email: account.email, role: account.role, name: account.name });
      const fallbackPath = account.role === "admin" ? "/admin" : "/";
      const safeNext =
        nextPath &&
        nextPath.startsWith("/") &&
        !nextPath.startsWith("//") &&
        (account.role === "admin" || !nextPath.startsWith("/admin"))
          ? nextPath
          : fallbackPath;
      router.replace(safeNext);
    }, 250);
  };
  return (
    <div className="flex min-h-screen flex-col justify-center bg-slate-50 px-4 py-10 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md text-center">
        <p className="ui-caption font-semibold text-blue-600">พื้นที่ทำงานเอกสารอัจฉริยะ</p>
        <h1 className="ui-page-title mt-1 text-slate-950">ระบบอ่านเอกสารด้วย OCR</h1>
        <p className="ui-body mt-2 text-slate-500">เข้าสู่ระบบเพื่อใช้งานระบบ</p>
      </div>

      <div className="mt-6 sm:mx-auto sm:w-full sm:max-w-md">
        <div className={`${cardClassName} px-5 py-6 sm:px-8`}>
          <form className="space-y-5" onSubmit={handleLogin}>
            <div>
              <h2 className="text-base font-black text-slate-950">เข้าสู่ระบบ</h2>
              <p className="mt-1 text-xs font-semibold text-slate-500">กรอกบัญชีเพื่อเข้าใช้งานพื้นที่เอกสาร</p>
            </div>

            <label className="block">
              <span className="text-xs font-black text-slate-700">อีเมล</span>
              <div className="relative mt-2">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400">
                  <Mail size={18} />
                </div>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="user@ocr.com"
                  className="block w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pl-10 pr-3 text-sm font-semibold text-slate-800 transition-colors placeholder:text-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-blue-100"
                />
              </div>
            </label>

            <label className="block">
              <span className="text-xs font-black text-slate-700">รหัสผ่าน</span>
              <div className="relative mt-2">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400">
                  <Lock size={18} />
                </div>
                <input
                  type={showPassword ? "text" : "password"}
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="password"
                  className="block w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pl-10 pr-10 text-sm font-semibold text-slate-800 transition-colors placeholder:text-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-blue-100"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((current) => !current)}
                  className="absolute inset-y-0 right-0 flex items-center pr-3 text-slate-400 transition-colors hover:text-slate-600"
                  aria-label={showPassword ? "ซ่อนรหัสผ่าน" : "แสดงรหัสผ่าน"}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </label>

            {error && <div className="rounded-lg border border-red-200 bg-red-50 p-2.5 text-xs font-bold text-red-600">{error}</div>}

            <button
              type="submit"
              disabled={isLoading}
              className="flex w-full justify-center rounded-xl border border-transparent bg-blue-600 px-4 py-2.5 text-sm font-black text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-4 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isLoading ? "กำลังเข้าสู่ระบบ..." : "เข้าสู่ระบบ"}
            </button>
          </form>

          <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs font-semibold text-slate-600">
            <p className="font-black text-slate-800">บัญชีทดสอบ</p>
            <p className="mt-1">User: user@ocr.com / user123</p>
            <p>Admin: admin@ocr.com / admin123</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-slate-100 px-6 text-sm font-semibold text-slate-500">
          กำลังเปิดหน้าเข้าสู่ระบบ...
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
