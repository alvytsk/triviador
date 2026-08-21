import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { adminKeys, issueInvites } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Banner, Button, Field } from "@/shared/ui";

const DEFAULT_COUNT = 10;
const DEFAULT_EXPIRES_IN_HOURS = 168;

/**
 * §10.5 Decision 5: an issued code is shown exactly once. The backend
 * stores only a SHA-256 digest — `IssuedInvite` (the response to `POST
 * /api/admin/invites`) is the one place the plaintext ever exists, and it
 * never comes back from the listing (`InviteView` has no `code` field —
 * see `invite-table.tsx`'s compile-time assertion). So this is a
 * self-contained inline panel, not a `Dialog` primitive: nothing else in
 * this plan needs a modal (Task 4's own report reached the same
 * conclusion for the question editor — `Banner`/`Field` already cover
 * every status this screen needs), and vendoring shadcn's `dialog.tsx`
 * for one screen would be gold-plating. Closing the panel without
 * copying loses the codes for good — that is the point, and the panel
 * says so before it lets you close it.
 */
export function IssueDialog() {
  const queryClient = useQueryClient();
  const [isOpen, setIsOpen] = useState(false);
  const [count, setCount] = useState(DEFAULT_COUNT);
  const [expiresInHours, setExpiresInHours] = useState(DEFAULT_EXPIRES_IN_HOURS);

  const mutation = useMutation({
    mutationFn: () => issueInvites({ count, expires_in_hours: expiresInHours }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.invites() });
    },
  });
  const error = mutation.error instanceof ApiFetchError ? mutation.error : null;

  function open() {
    mutation.reset();
    setIsOpen(true);
  }

  function close() {
    mutation.reset();
    setIsOpen(false);
  }

  if (!isOpen) {
    return <Button onClick={open}>Issue invites</Button>;
  }

  const issued = mutation.data;

  return (
    <div className="flex flex-col gap-4 border-2 border-line bg-panel p-6">
      {issued === undefined ? (
        <>
          <Field
            label="Count"
            type="number"
            min={1}
            max={500}
            value={count}
            onChange={(event) => {
              const next = event.target.valueAsNumber;
              setCount(Number.isNaN(next) ? 0 : next);
            }}
          />
          <Field
            label="Expires in hours"
            type="number"
            min={1}
            max={8760}
            value={expiresInHours}
            onChange={(event) => {
              const next = event.target.valueAsNumber;
              setExpiresInHours(Number.isNaN(next) ? 0 : next);
            }}
          />
          {error !== null && (
            <Banner tone="bad" {...(error.code !== null ? { code: error.code } : {})}>
              {adminErrorMessage(error.code ?? "validation_failed", error.message)}
            </Banner>
          )}
          <div className="flex items-center gap-3">
            <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>
              Issue
            </Button>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
          </div>
        </>
      ) : (
        <>
          <Banner tone="warn">
            These codes will not be shown again — copy them now. Closing this panel loses any you
            have not saved.
          </Banner>
          <ul className="flex flex-col gap-2">
            {issued.map((invite) => (
              <li
                key={invite.id}
                className="flex items-center justify-between gap-4 bg-raised px-4 py-2"
              >
                <code className="font-mono text-[14px] text-ink">{invite.code}</code>
                <Button
                  variant="ghost"
                  onClick={() => {
                    void navigator.clipboard.writeText(invite.code);
                  }}
                >
                  Copy
                </Button>
              </li>
            ))}
          </ul>
          <Button variant="ghost" onClick={close}>
            Done
          </Button>
        </>
      )}
    </div>
  );
}
