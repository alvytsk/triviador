import { useForm } from "@tanstack/react-form";
import { ApiFetchError, loginRequestSchema } from "@/shared/api";
import { Banner, Button, Field } from "@/shared/ui";
import { useSignIn } from "../model/use-sign-in";

export function SignInForm({ onDone }: { onDone: () => void }) {
  const signIn = useSignIn(onDone);
  const form = useForm({
    defaultValues: { username: "", password: "" },
    // The generated schema is the validator. Writing `minLength: 1` here by
    // hand would be a second copy of a rule the server already owns, and the
    // two would drift the first time the contract moved.
    validators: { onSubmit: loginRequestSchema },
    onSubmit: ({ value }) => signIn.mutateAsync(value).catch(() => undefined),
  });

  return (
    <form
      className="flex w-100 flex-col gap-5 border border-line bg-panel p-8"
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <h1 className="font-display text-3xl tracking-wider">SIGN IN</h1>

      {signIn.error instanceof ApiFetchError && (
        // `exactOptionalPropertyTypes` treats `code={x ?? undefined}` as
        // writing `undefined` into an optional `string` prop, which it
        // forbids — the prop must be entirely absent, not present-and-empty.
        // `error.code` is `null` for a transport failure (never a code), so
        // the prop is spread in only when there is a real server code.
        <Banner tone="bad" {...(signIn.error.code !== null ? { code: signIn.error.code } : {})}>
          {signIn.error.message}
        </Banner>
      )}

      <form.Field name="username">
        {(field) => (
          <Field
            label="Username"
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            error={field.state.meta.errors[0]?.message}
            autoComplete="username"
          />
        )}
      </form.Field>

      <form.Field name="password">
        {(field) => (
          <Field
            label="Password"
            type="password"
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            error={field.state.meta.errors[0]?.message}
            autoComplete="current-password"
          />
        )}
      </form.Field>

      <Button type="submit" disabled={signIn.isPending}>
        {signIn.isPending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
