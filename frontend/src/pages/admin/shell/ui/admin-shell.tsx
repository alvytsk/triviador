import { Link, Outlet } from "@tanstack/react-router";
import { cn } from "@/shared/lib";

/**
 * §9.7's six admin destinations plus the one way back out: Questions,
 * Import, Invites, Users and Presets are the five screens Tasks 3–8 each
 * own one route pair for; "Back to lobby" is the sixth — `/admin` is a
 * separate lazy tree (this file), so leaving it needs its own link rather
 * than relying on browser back.
 *
 * Five of the six point at routes that do not exist yet in this task
 * (`_authed.admin.questions.tsx` and its siblings land in Tasks 3–8), so
 * they are plain `<a>` elements rather than typed `<Link>`s — a `to` that
 * is not in the generated route tree fails `tsc --noEmit` outright, and a
 * cast would only hide that these are forward references. `/` already
 * exists, so "Back to lobby" is a real `<Link>`.
 */
const SECTIONS: ReadonlyArray<{ href: string; label: string }> = [
  { href: "/admin/questions", label: "Questions" },
  { href: "/admin/questions/import", label: "Import" },
  { href: "/admin/invites", label: "Invites" },
  { href: "/admin/users", label: "Users" },
  { href: "/admin/presets", label: "Presets" },
];

const NAV_LINK_CLASS =
  "text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-dim hover:text-ink";

export function AdminShell() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between gap-6 border-b-2 border-line px-6 py-4">
        <span className="font-display text-xl tracking-wider text-gold">Admin</span>
        <nav aria-label="Admin" className="flex flex-1 items-center gap-6">
          {SECTIONS.map((section) => (
            <a key={section.href} href={section.href} className={NAV_LINK_CLASS}>
              {section.label}
            </a>
          ))}
        </nav>
        <Link to="/" className={cn(NAV_LINK_CLASS, "text-gold")}>
          Back to lobby
        </Link>
      </header>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
