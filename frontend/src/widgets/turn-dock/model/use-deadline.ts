/**
 * Re-exported at this path for `@/widgets/turn-dock`'s public API and this
 * slice's own test (`use-deadline.test.ts`, unchanged). The implementation
 * lives in `shared/lib/use-deadline.ts` — see that file's doc comment for
 * why: `widgets/question-dock` needs the same hook and steiger forbids a
 * cross-slice import between two sibling widgets.
 */
export { useDeadline } from "@/shared/lib";
