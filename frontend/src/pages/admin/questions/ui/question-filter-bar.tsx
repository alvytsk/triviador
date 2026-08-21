import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { adminCategoriesQueryOptions } from "@/entities/admin";
import {
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/shared/ui";
import { hasActiveFilters, type QuestionSearch } from "../model/types";

const ALL = "all";

/** Every value the bar writes goes through one `navigate({ search })` call
 *  with `offset` reset to `0` — the routing rule §10.2 relies on to keep
 *  "change a filter on page 3" from landing on an empty page 3 of the new,
 *  narrower result set. Paging (`question-page-pager.tsx`'s sibling logic,
 *  inlined below) is the one write that must *not* reset `offset`. */
export function QuestionFilterBar({
  search,
  hideClear = false,
}: {
  search: QuestionSearch;
  /** The empty-filtered-result state below the bar already offers its own
   *  "Clear filters" — suppressing the bar's copy there keeps the page
   *  from showing the same control twice. */
  hideClear?: boolean;
}) {
  const navigate = useNavigate({ from: "/admin/questions" });
  const categories = useQuery(adminCategoriesQueryOptions());

  function setFilter(patch: Partial<QuestionSearch>) {
    navigate({ to: "/admin/questions", search: (prev) => ({ ...prev, ...patch, offset: 0 }) });
  }

  const hasFilters = hasActiveFilters(search);

  return (
    <div className="flex flex-wrap items-end gap-4">
      <Input
        aria-label="Search prompts"
        placeholder="Search prompts…"
        value={search.q ?? ""}
        onChange={(event) =>
          setFilter({ q: event.target.value === "" ? undefined : event.target.value })
        }
        className="w-64"
      />

      <Select
        value={search.kind ?? ALL}
        onValueChange={(value) =>
          setFilter({ kind: value === ALL ? undefined : (value as QuestionSearch["kind"]) })
        }
      >
        <SelectTrigger aria-label="Kind" className="w-44">
          <SelectValue placeholder="Any kind" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any kind</SelectItem>
          <SelectItem value="multiple_choice">Multiple choice</SelectItem>
          <SelectItem value="numeric">Numeric</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={search.difficulty ?? ALL}
        onValueChange={(value) =>
          setFilter({
            difficulty: value === ALL ? undefined : (value as QuestionSearch["difficulty"]),
          })
        }
      >
        <SelectTrigger aria-label="Difficulty" className="w-44">
          <SelectValue placeholder="Any difficulty" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any difficulty</SelectItem>
          <SelectItem value="easy">Easy</SelectItem>
          <SelectItem value="medium">Medium</SelectItem>
          <SelectItem value="hard">Hard</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={search.category_id ?? ALL}
        onValueChange={(value) => setFilter({ category_id: value === ALL ? undefined : value })}
      >
        <SelectTrigger aria-label="Category" className="w-44">
          <SelectValue placeholder="Any category" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any category</SelectItem>
          {(categories.data ?? []).map((category) => (
            <SelectItem key={category.id} value={category.id}>
              {category.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={search.is_active === undefined ? ALL : String(search.is_active)}
        onValueChange={(value) =>
          setFilter({ is_active: value === ALL ? undefined : value === "true" })
        }
      >
        <SelectTrigger aria-label="Active" className="w-36">
          <SelectValue placeholder="Any status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any status</SelectItem>
          <SelectItem value="true">Active</SelectItem>
          <SelectItem value="false">Inactive</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={search.has_media === undefined ? ALL : String(search.has_media)}
        onValueChange={(value) =>
          setFilter({ has_media: value === ALL ? undefined : value === "true" })
        }
      >
        <SelectTrigger aria-label="Media" className="w-36">
          <SelectValue placeholder="Any media" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any media</SelectItem>
          <SelectItem value="true">With media</SelectItem>
          <SelectItem value="false">Without media</SelectItem>
        </SelectContent>
      </Select>

      {hasFilters && !hideClear && (
        <Button
          variant="ghost"
          onClick={() =>
            navigate({
              to: "/admin/questions",
              search: (prev) => ({ limit: prev.limit, offset: 0 }),
            })
          }
        >
          Clear filters
        </Button>
      )}
    </div>
  );
}
