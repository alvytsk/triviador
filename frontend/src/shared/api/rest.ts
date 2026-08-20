import type { z } from "zod";
import { ApiFetchError } from "./errors";
import { errorEnvelopeSchema } from "./generated/public";

/**
 * Every REST call in the app. Two rules it exists to keep:
 *
 * 1. Nothing reads a field off a response until a generated schema has
 *    parsed it (§4's type contract). `schema` is not optional.
 * 2. A failure is classified before it is thrown, so no caller ever has to
 *    ask "is this a server code or a broken pipe" — see `ApiFetchError`.
 */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      // The session is a cookie (§7): `triviador_session`, HttpOnly,
      // SameSite=Lax, path=/. Same-origin because dev proxies /api and
      // production serves both from one origin behind Caddy — a
      // cross-origin credentialled fetch would need CORS the backend
      // deliberately does not grant.
      credentials: "same-origin",
      ...init,
      headers: { accept: "application/json", ...init?.headers },
    });
  } catch (cause) {
    throw new ApiFetchError({
      kind: "transport",
      message: "the server could not be reached",
      status: null,
      cause,
    });
  }

  const text = await response.text();

  if (!response.ok) {
    throw toFailure(response.status, text);
  }

  if (text === "") {
    // 204, and a schema that accepts it (`z.void()`) — logout's shape.
    const empty = schema.safeParse(undefined);
    if (empty.success) return empty.data;
    throw new ApiFetchError({
      kind: "transport",
      message: "the server sent an empty body where one was expected",
      status: response.status,
    });
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (cause) {
    throw new ApiFetchError({
      kind: "transport",
      message: "the server sent a body that is not JSON",
      status: response.status,
      cause,
    });
  }

  const result = schema.safeParse(parsed);
  if (!result.success) {
    // A 2xx in the wrong shape is not a server error — the server believes
    // it succeeded. It is this client and that server disagreeing about the
    // contract, which is the same class of problem as an HTML error page:
    // unusable, and not something to switch on.
    throw new ApiFetchError({
      kind: "transport",
      message: "the server sent a body this client does not understand",
      status: response.status,
      cause: result.error,
    });
  }
  return result.data;
}

/** POST/PUT with a JSON body. `body === undefined` sends no body at all,
 *  which is what `/api/auth/logout` wants. */
export async function apiSend<T>(
  path: string,
  schema: z.ZodType<T>,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  return apiFetch(path, schema, {
    method: "POST",
    ...init,
    ...(body === undefined
      ? {}
      : {
          body: JSON.stringify(body),
          headers: { "content-type": "application/json", ...init?.headers },
        }),
  });
}

function toFailure(status: number, text: string): ApiFetchError {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return new ApiFetchError({
      kind: "transport",
      message: `the server returned ${status} and a body that is not an error envelope`,
      status,
    });
  }
  const envelope = errorEnvelopeSchema.safeParse(parsed);
  if (!envelope.success) {
    return new ApiFetchError({
      kind: "transport",
      message: `the server returned ${status} and a body that is not an error envelope`,
      status,
    });
  }
  return new ApiFetchError({
    kind: "envelope",
    message: envelope.data.message,
    status,
    code: envelope.data.code,
    details: envelope.data.details,
  });
}
