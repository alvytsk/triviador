import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { SignInForm } from "@/features/sign-in";

/**
 * The wordmark panel and the sign-in form live side by side, same as the
 * design canvas's login screen: the wordmark identifies the game before
 * anyone has typed a keystroke, the form is the only thing that does work.
 *
 * `useSearch({ from: "/login" })` rather than importing that route's
 * `Route` object: a page is below `app/` in the FSD layer order, and this
 * ties to the route by its string id instead of an import, which is what
 * keeps that direction one-way.
 */
export function LoginPage() {
  const navigate = useNavigate();
  const search = useSearch({ from: "/login" });

  return (
    <div className="flex min-h-screen items-center justify-center gap-12 bg-base px-6">
      <div className="hidden w-100 flex-col gap-3 sm:flex">
        <h1 className="font-display text-6xl tracking-wider text-gold">TRIVIADOR</h1>
        <p className="text-[15px] text-ink-dim">Answer fast, claim the map, hold what you take.</p>
      </div>

      <div className="flex flex-col gap-4">
        <SignInForm
          onDone={() => {
            // `search.next` was already validated by `loginSearchSchema`
            // before this component ever mounted (`validateSearch` throws
            // in the route, not here) — this handler never sees a value
            // that could take the browser off-origin.
            void navigate({ to: search.next ?? "/" });
          }}
        />
        <p className="text-center text-[13px] text-ink-dim">
          Have an invite code?{" "}
          <Link to="/redeem" className="text-gold hover:text-gold-bright">
            Redeem it
          </Link>
        </p>
      </div>
    </div>
  );
}
