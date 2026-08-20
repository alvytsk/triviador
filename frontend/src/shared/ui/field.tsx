import type { InputHTMLAttributes, ReactNode } from "react";
import { cn } from "../lib";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: ReactNode;
  error?: string | undefined;
}

export function Field({ label, hint, error, className, id, ...props }: FieldProps) {
  const inputId = id ?? `field-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={inputId} className="text-[10px] font-semibold tracking-[0.14em] text-ink-dim">
        {label.toUpperCase()}
      </label>
      <input
        id={inputId}
        aria-invalid={error !== undefined}
        aria-errormessage={error !== undefined ? `${inputId}-error` : undefined}
        className={cn(
          "bg-raised border-2 px-4 py-3 text-[15px] font-medium text-ink outline-none",
          error === undefined ? "border-line focus:border-gold" : "border-bad",
          className,
        )}
        {...props}
      />
      {error !== undefined ? (
        <p id={`${inputId}-error`} className="text-[11px] font-medium text-bad">
          {error}
        </p>
      ) : (
        hint !== undefined && <p className="text-[11px] text-ink-faint">{hint}</p>
      )}
    </div>
  );
}
