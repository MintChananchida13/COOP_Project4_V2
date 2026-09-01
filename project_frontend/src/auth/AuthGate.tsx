"use client";

import { ReactNode, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AuthRole, readAuthSession } from "./session";

export default function AuthGate({
  children,
  requiredRole,
}: {
  children: ReactNode;
  requiredRole?: AuthRole;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    const session = readAuthSession();
    if (!session) {
      router.replace(`/login?next=${encodeURIComponent(pathname || "/")}`);
      return;
    }
    if (requiredRole && session.role !== requiredRole) {
      router.replace(session.role === "admin" ? "/admin" : "/");
      return;
    }
    setAllowed(true);
  }, [pathname, requiredRole, router]);

  if (!allowed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-sm font-semibold text-slate-500">
        กำลังตรวจสอบสิทธิ์การใช้งาน...
      </div>
    );
  }

  return <>{children}</>;
}

