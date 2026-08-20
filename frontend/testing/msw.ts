import { setupServer } from "msw/node";

/** No default handlers on purpose: an unhandled request must fail the test
 *  loudly (`onUnhandledRequest: "error"` in setup.ts) rather than hang or
 *  quietly return undefined. Every test declares what it expects to be
 *  asked for. */
export const server = setupServer();
