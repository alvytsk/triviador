import { Link, Outlet } from "@tanstack/react-router";
import { cn } from "@/shared/lib";

/**
 * §9.7's six admin destinations plus the one way back out: Questions,
 * Import, Invites, Users and Presets are the five screens Tasks 3–8 each
 * own one route pair for; "Back to lobby" is the sixth — `/admin` is a
 * separate lazy tree (this file), so leaving it needs its own link rather
 * than relying on browser back.
 *
 * All five are typed `<Link>`s. They started as plain `<a href>` elements
 * in Task 1, when none of these five routes existed yet and a `to` outside
 * the generated route tree would have failed `tsc --noEmit` outright — but
 * Tasks 3–8 registered all five long ago, and the plain `<a>`s were never
 * upgraded. A real `<a>` is a full document reload, not a client-side
 * transition, which discards every bit of SPA state on every admin nav
 * click; Task 10's `admin-session.test.tsx` demonstrated this directly
 * (clicking "Invites" from the Questions screen moved nothing under
 * jsdom, which does not implement navigation) and had to fall back to
 * remounting a fresh route for every screen as a result. `/` was already a
 * real `<Link>` from the start.
 */
const SECTIONS: ReadonlyArray<{
  to:
    | "/admin/questions"
    | "/admin/questions/import"
    | "/admin/invites"
    | "/admin/users"
    | "/admin/presets";
  label: string;
}> = [
  { to: "/admin/questions", label: "Questions" },
  { to: "/admin/questions/import", label: "Import" },
  { to: "/admin/invites", label: "Invites" },
  { to: "/admin/users", label: "Users" },
  { to: "/admin/presets", label: "Presets" },
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
            <Link key={section.to} to={section.to} className={NAV_LINK_CLASS}>
              {section.label}
            </Link>
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
