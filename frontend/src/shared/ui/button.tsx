import type { ButtonHTMLAttributes } from "react";
import { cn } from "../lib";

type Variant = "primary" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-gold text-base hover:bg-gold-bright disabled:bg-track disabled:text-ink-faint",
  ghost: "border-2 border-line text-ink hover:border-line-strong disabled:text-ink-faint",
};

export function Button({ variant = "primary", className, ...props }: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "font-display text-xl tracking-wider px-6 h-12 inline-flex items-center justify-center",
        "transition-colors disabled:cursor-not-allowed",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}
