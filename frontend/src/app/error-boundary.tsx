import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiFetchError } from "@/shared/api";
import { Banner, Button } from "@/shared/ui";

interface State {
  error: Error | null;
}

/**
 * §11.7: an error boundary per route.
 *
 * It shows the server's code when there is one and says "something broke"
 * when there is not — never a stack, and never a `code` this client invented
 * (decision 2). The only offered action is to reload, because every other
 * recovery this app has is "take a fresh snapshot", and by the time a render
 * has thrown, the component that would do that is gone.
 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("route boundary caught", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    const code = error instanceof ApiFetchError && error.code !== null ? error.code : undefined;
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
        <Banner tone="bad" {...(code !== undefined ? { code } : {})}>
          {error instanceof ApiFetchError ? error.message : "Something broke on this screen."}
        </Banner>
        <Button onClick={() => window.location.reload()}>Reload</Button>
      </div>
    );
  }
}
