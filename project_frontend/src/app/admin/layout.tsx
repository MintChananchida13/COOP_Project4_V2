import { ReactNode } from "react";
import AdminShell from "../../admin/AdminShell";
import AuthGate from "../../auth/AuthGate";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate requiredRole="admin">
      <AdminShell>{children}</AdminShell>
    </AuthGate>
  );
}
