import type { ErrorCode } from "./generated/errors";

/**
 * Two kinds, and the distinction is load-bearing.
 *
 * `envelope` — the server answered in the shape §6.3 guarantees. `code` is a
 * real member of the closed union the backend asserts the shape of, and a
 * caller may switch on it.
 *
 * `transport` — nothing this API can be understood to have said: a dead
 * network, a proxy's HTML error page, a truncated body, or a 2xx whose shape
 * the generated schema rejects. `code` is `null`, and there is deliberately
 * no synthetic member like `"network_error"` to put there: `ErrorCode` is a
 * closed union of two disjoint server enums that a backend test asserts the
 * membership of, and inventing a value would corrupt the one type that makes
 * `switch` exhaustive.
 */
export type ApiFailureKind = "envelope" | "transport";

export class ApiFetchError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number | null;
  readonly code: ErrorCode | null;
  readonly details: Record<string, unknown> | null;

  constructor(init: {
    kind: ApiFailureKind;
    message: string;
    status: number | null;
    code?: ErrorCode | null;
    details?: Record<string, unknown> | null;
    cause?: unknown;
  }) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiFetchError";
    this.kind = init.kind;
    this.status = init.status;
    this.code = init.code ?? null;
    this.details = init.details ?? null;
  }

  /** The one code a guard reacts to structurally rather than by showing it
   *  (§9.4: "a 401 anywhere redirects to /login"). `credentials_invalid` is
   *  deliberately *not* included: that is a form telling you your password
   *  was wrong, not a session that expired. */
  get isUnauthenticated(): boolean {
    return this.kind === "envelope" && this.code === "unauthenticated";
  }
}
