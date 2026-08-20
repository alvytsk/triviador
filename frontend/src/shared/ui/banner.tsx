import type { ReactNode } from "react";
import { cn } from "../lib";

type Tone = "bad" | "warn" | "quiet";

const TONES: Record<Tone, string> = {
  bad: "bg-[#2a1220] border-bad",
  warn: "bg-[#2a2412] border-gold",
  quiet: "bg-[#221c2e] border-ink-dim",
};

/** §11.7's one shape for anything that went wrong: a code, a sentence, and
 *  nothing a player has to interpret. `code` is the server's — never one we
 *  invented (decision 2). */
export function Banner({
  tone,
  code,
  children,
}: {
  tone: Tone;
  code?: string;
  children: ReactNode;
}) {
  return (
    <div role="status" className={cn("flex items-center gap-3 border-l-4 px-4 py-3", TONES[tone])}>
      {code !== undefined && (
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim">
          {code}
        </span>
      )}
      <span className="text-[13px] text-ink">{children}</span>
    </div>
  );
}
