import { useForm } from "@tanstack/react-form";
import { ApiFetchError, redeemRequestSchema } from "@/shared/api";
import { Banner, Button, Field } from "@/shared/ui";
import { useRedeem } from "../model/use-redeem";

export function RedeemForm({ onDone }: { onDone: () => void }) {
  const redeem = useRedeem(onDone);
  const form = useForm({
    defaultValues: { code: "", username: "", display_name: "", password: "" },
    // The generated schema is the validator, same as sign-in: the pattern
    // and the length bounds below are hints for a player, not a second copy
    // of the rule — the rule itself lives in `redeemRequestSchema`.
    validators: { onSubmit: redeemRequestSchema },
    onSubmit: ({ value }) => redeem.mutateAsync(value).catch(() => undefined),
  });

  return (
    <form
      className="flex w-100 flex-col gap-5 border border-line bg-panel p-8"
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <h1 className="font-display text-3xl tracking-wider">REDEEM INVITE</h1>

      {redeem.error instanceof ApiFetchError && (
        // See sign-in-form.tsx: `exactOptionalPropertyTypes` forbids an
        // optional `string` prop from being written as `undefined`, so the
        // prop is spread in only when there is a real server code.
        <Banner tone="bad" {...(redeem.error.code !== null ? { code: redeem.error.code } : {})}>
          {redeem.error.message}
        </Banner>
      )}

      <form.Field name="code">
        {(field) => (
          <Field
            label="Invite code"
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            error={field.state.meta.errors[0]?.message}
            autoComplete="off"
          />
        )}
      </form.Field>

      <form.Field name="username">
        {(field) => {
          // The contract's rule, not this label's: `^[A-Za-z0-9._-]+$`,
          // 3–32 characters. This text must keep matching what
          // `redeemRequestSchema` enforces, because a mismatch here is a
          // hint that lies, not a validation gap. Shown as the error text
          // too (rather than the schema's raw, generic "Invalid") so a
          // rejected pattern still tells the player what is allowed,
          // instead of replacing that explanation with nothing useful.
          const usernameHint = "3–32 characters. Letters, digits, dot, dash, underscore.";
          return (
            <Field
              label="Username"
              value={field.state.value}
              onChange={(e) => field.handleChange(e.target.value)}
              error={field.state.meta.errors.length > 0 ? usernameHint : undefined}
              hint={usernameHint}
              autoComplete="username"
            />
          );
        }}
      </form.Field>

      <form.Field name="display_name">
        {(field) => (
          <Field
            label="Display name"
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            error={field.state.meta.errors[0]?.message}
            autoComplete="name"
          />
        )}
      </form.Field>

      <form.Field name="password">
        {(field) => {
          // The contract's rule, not this label's: `min_length=8`. Same
          // reasoning as the username hint above.
          const passwordHint = "At least 8 characters.";
          return (
            <Field
              label="Password"
              type="password"
              value={field.state.value}
              onChange={(e) => field.handleChange(e.target.value)}
              error={field.state.meta.errors.length > 0 ? passwordHint : undefined}
              hint={passwordHint}
              autoComplete="new-password"
            />
          );
        }}
      </form.Field>

      <Button type="submit" disabled={redeem.isPending}>
        {redeem.isPending ? "Creating account…" : "Create account"}
      </Button>
    </form>
  );
}
