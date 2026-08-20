import type { ReactNode } from "react";
import { cn } from "../lib";

export function Chip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "px-2 py-[2px] text-[9px] font-semibold uppercase tracking-[0.14em]",
        "bg-gold text-base",
        className,
      )}
    >
      {children}
    </span>
  );
}
