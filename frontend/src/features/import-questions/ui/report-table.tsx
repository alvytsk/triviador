import type { ImportSummary } from "@/shared/api/generated/admin";
import { Banner, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui";

/**
 * §10.2: a notice (a duplicate prompt, in-file or already in the bank) is
 * a warning, never a rejection — rendered as its own list of `Banner
 * tone="warn"`s, never folded into the rejections table below. The
 * schemas already keep the two apart (`ImportNotice`/`ImportRejection`
 * are distinct types, per `generated/admin.ts`'s own comment); this keeps
 * the render side just as strict, so a future edit cannot quietly start
 * treating one as the other.
 */
export function ReportTable({ summary }: { summary: ImportSummary }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] text-ink-dim">
        {summary.row_count} row{summary.row_count === 1 ? "" : "s"} read, {summary.rejected_count}{" "}
        rejected.
      </p>

      {summary.notices.length > 0 && (
        <div className="flex flex-col gap-2">
          {summary.notices.map((notice) => (
            <Banner key={notice.line} tone="warn">
              Line {notice.line}: {notice.reason}
            </Banner>
          ))}
        </div>
      )}

      {summary.rejections.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Line</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {summary.rejections.map((rejection) => (
              <TableRow key={rejection.line}>
                <TableCell>{rejection.line}</TableCell>
                <TableCell>{rejection.reason}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
