import type { InputHTMLAttributes } from "react";
import { cn } from "../lib";

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

/** Vendored from shadcn/ui, restyled to Plan 6's dark broadcast palette —
 *  the same border/raised-background treatment `Field`'s bare `<input>`
 *  already uses, pulled out here as its own primitive because filter bars
 *  (unlike a form) need an input with no attached label or error slot. */
export function Input({ className, ...props }: InputProps) {
  return (
    <input
      className={cn(
        "h-11 w-full bg-raised border-2 border-line px-4 text-[14px] font-medium text-ink outline-none",
        "placeholder:text-ink-faint transition-colors focus:border-gold",
        "disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}
