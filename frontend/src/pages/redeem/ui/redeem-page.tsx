import { Link, useNavigate } from "@tanstack/react-router";
import { RedeemForm } from "@/features/redeem-invite";

/** Same wordmark-panel-plus-form layout as `/login` — an invite code gets
 *  you here instead of an account, but redeeming ends in the same signed-in
 *  state, so there is nowhere else for `onDone` to send you. */
export function RedeemPage() {
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen items-center justify-center gap-12 bg-base px-6">
      <div className="hidden w-100 flex-col gap-3 sm:flex">
        <h1 className="font-display text-6xl tracking-wider text-gold">TRIVIADOR</h1>
        <p className="text-[15px] text-ink-dim">
          There is no open sign-up. An invite code is the only way in, and it is spent once.
        </p>
      </div>

      <div className="flex flex-col gap-4">
        <RedeemForm
          onDone={() => {
            void navigate({ to: "/" });
          }}
        />
        <p className="text-center text-[13px] text-ink-dim">
          Already have an account?{" "}
          <Link to="/login" className="text-gold hover:text-gold-bright">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
