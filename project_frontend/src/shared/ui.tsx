"use client";

import Link from "next/link";
import { AlertCircle, CheckCircle2, Loader2, SearchX } from "lucide-react";
import { ReactNode } from "react";

type Tone = "primary" | "neutral" | "success" | "warning" | "danger" | "info";

const toneClasses: Record<Tone, { badge: string; soft: string; text: string; border: string }> = {
  primary: {
    badge: "bg-blue-100 text-blue-700",
    soft: "bg-blue-50",
    text: "text-blue-700",
    border: "border-blue-200",
  },
  neutral: {
    badge: "bg-slate-100 text-slate-700",
    soft: "bg-slate-50",
    text: "text-slate-700",
    border: "border-slate-200",
  },
  success: {
    badge: "bg-emerald-100 text-emerald-700",
    soft: "bg-emerald-50",
    text: "text-emerald-700",
    border: "border-emerald-200",
  },
  warning: {
    badge: "bg-amber-100 text-amber-700",
    soft: "bg-amber-50",
    text: "text-amber-700",
    border: "border-amber-200",
  },
  danger: {
    badge: "bg-red-100 text-red-700",
    soft: "bg-red-50",
    text: "text-red-700",
    border: "border-red-200",
  },
  info: {
    badge: "bg-sky-100 text-sky-700",
    soft: "bg-sky-50",
    text: "text-sky-700",
    border: "border-sky-200",
  },
};

export const cardClassName = "rounded-2xl border border-slate-200 bg-white shadow-sm";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <section className={`${cardClassName} p-5`}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          {eyebrow && <p className="ui-caption font-semibold text-blue-600">{eyebrow}</p>}
          <h1 className="ui-page-title mt-1 text-slate-950">{title}</h1>
          {description && <p className="ui-body mt-1 max-w-3xl text-slate-500">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </section>
  );
}

export function StatusBadge({ status, tone, label }: { status: string; tone?: Tone; label?: string }) {
  const resolvedTone =
    tone ||
    (status === "active" || status === "approved" || status === "converted" || status === "completed"
      ? "success"
      : status === "draft" || status === "pending" || status === "embedding_pending" || status === "validated"
        ? "warning"
        : status === "rejected" || status === "failed" || status === "disabled"
          ? "danger"
          : "neutral");
  return (
    <span className={`ui-caption inline-flex items-center rounded-full px-2.5 py-1 font-semibold ${toneClasses[resolvedTone].badge}`}>
      {label || status.replaceAll("_", " ")}
    </span>
  );
}

export function InlineState({
  tone = "neutral",
  title,
  message,
}: {
  tone?: Tone;
  title?: string;
  message: string;
}) {
  const classes = toneClasses[tone];
  return (
    <div className={`rounded-xl border ${classes.border} ${classes.soft} px-4 py-3`}>
      {title && <p className={`ui-label ${classes.text}`}>{title}</p>}
      <p className={`ui-body ${classes.text}`}>{message}</p>
    </div>
  );
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-36 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-6 text-center">
      <SearchX size={24} className="text-slate-400" />
      <h3 className="ui-card-title mt-3 text-slate-800">{title}</h3>
      <p className="ui-body mt-1 max-w-md text-slate-500">{message}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function LoadingState({ message = "Loading..." }: { message?: string }) {
  return (
    <div className="ui-label flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-500">
      <Loader2 size={14} className="animate-spin text-blue-600" />
      {message}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="ui-label flex flex-col gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700 sm:flex-row sm:items-center sm:justify-between">
      <span className="inline-flex items-center gap-2">
        <AlertCircle size={15} />
        {message}
      </span>
      {retry && (
        <button type="button" onClick={retry} className="ui-button-text rounded-lg border border-red-200 bg-white px-3 py-1.5 text-red-700">
          Retry
        </button>
      )}
    </div>
  );
}

export function Stepper({
  steps,
  current,
}: {
  steps: { key: string; label: string; description?: string }[];
  current: string;
}) {
  const currentIndex = Math.max(0, steps.findIndex((step) => step.key === current));
  return (
    <nav aria-label="Progress" className={`${cardClassName} overflow-hidden px-4 py-3`}>
      <ol className="grid gap-2 md:grid-cols-6">
        {steps.map((step, index) => {
          const completed = index < currentIndex;
          const active = index === currentIndex;
          return (
            <li key={step.key} className={`rounded-xl border px-3 py-2 ${active ? "border-blue-200 bg-blue-50" : completed ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-slate-50"}`}>
              <div className="flex items-center gap-2">
                <span className={`ui-caption flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-bold ${active ? "bg-blue-600 text-white" : completed ? "bg-emerald-600 text-white" : "bg-white text-slate-400"}`}>
                  {completed ? <CheckCircle2 size={13} /> : index + 1}
                </span>
                <span className="min-w-0">
                  <span className={`ui-label block truncate ${active ? "text-blue-900" : completed ? "text-emerald-900" : "text-slate-500"}`}>
                    {step.label}
                  </span>
                  {step.description && <span className="ui-caption block truncate text-slate-400">{step.description}</span>}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function ActionButton({
  href,
  onClick,
  children,
  tone = "neutral",
  disabled,
  type = "button",
}: {
  href?: string;
  onClick?: () => void;
  children: ReactNode;
  tone?: Tone;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const base =
    "ui-button-text inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 transition-colors disabled:bg-slate-200 disabled:text-slate-400";
  const toneClass =
    tone === "primary"
      ? "bg-blue-600 text-white hover:bg-blue-700"
      : tone === "success"
        ? "bg-emerald-600 text-white hover:bg-emerald-700"
        : tone === "danger"
          ? "border border-red-200 bg-white text-red-600 hover:bg-red-50"
          : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50";

  if (href && !disabled) {
    return (
      <Link href={href} className={`${base} ${toneClass}`}>
        {children}
      </Link>
    );
  }

  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`${base} ${toneClass}`}>
      {children}
    </button>
  );
}
