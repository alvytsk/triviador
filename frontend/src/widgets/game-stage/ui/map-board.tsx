import { useQuery } from "@tanstack/react-query";
import { Fragment, useMemo } from "react";
import { yourOptions } from "@/entities/game";
import { mapQueryOptions } from "@/entities/map";
import { ownershipOf } from "@/entities/territory";
import type { ClientGameState } from "@/shared/api";
import { seatVar } from "@/shared/config";
import { useBoardStore } from "@/shared/lib";
import { Banner } from "@/shared/ui";

/**
 * §8.1: region appearance is derived, never stored.
 *
 * Fill is `var(--seat-N)` where N is the owner's seat; a free region is a
 * token; an offered region is stroked in gold; everything else during a
 * choosing turn is dimmed. Nothing here holds a colour and nothing here
 * consults a rule — `your_options` is the whole of the client's knowledge
 * about what may be clicked (§8.8).
 */
export function MapBoard({
  state,
  onSelect,
}: {
  state: ClientGameState;
  onSelect: (regionId: string) => void;
}) {
  const map = useQuery(mapQueryOptions(state.map_id));
  const selected = useBoardStore((s) => s.selectedRegionId);
  const ownership = useMemo(() => ownershipOf(state), [state]);
  const options = yourOptions(state);
  const offered = useMemo(
    () => new Set([...options.pick, ...options.attack]),
    [options.pick, options.attack],
  );

  if (map.isPending) {
    return (
      <div className="flex h-full items-center justify-center text-ink-faint">Loading the map…</div>
    );
  }
  if (map.isError) {
    // Decision 12: fail closed and say which map. A partial board is worse
    // than no board, because a player would act on it.
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Banner tone="bad" code={state.map_id}>
          This map could not be drawn. {(map.error as Error).message}
        </Banner>
      </div>
    );
  }

  return (
    <svg viewBox={map.data.viewBox} className="h-full w-full" role="img" aria-label="Game map">
      <title>Game map</title>
      {map.data.regions.map((region) => {
        const held = ownership.get(region.id);
        const isOffered = offered.has(region.id);
        const isSelected = selected === region.id;
        const fill =
          held?.ownerSeat != null
            ? seatVar(held.ownerSeat)
            : isOffered
              ? "var(--color-line-strong)"
              : "var(--color-region-free)";
        return (
          <Fragment key={region.id}>
            {/* biome-ignore lint/a11y/useSemanticElements: a region is a <path>, not a <button> — the geometry itself is what a player clicks, and no HTML element does "a clickable slice of an inline SVG"; role="button" plus aria-disabled is the correct ARIA pattern here. */}
            <path
              d={region.d}
              fillRule={region.fillRule as "evenodd" | "nonzero" | undefined}
              clipRule={region.clipRule}
              fill={fill}
              fillOpacity={offered.size > 0 && !isOffered && !isSelected ? 0.35 : 1}
              stroke={
                isSelected
                  ? "var(--color-ink)"
                  : isOffered
                    ? "var(--color-gold)"
                    : "var(--color-base)"
              }
              strokeWidth={isSelected || isOffered ? 4 : held?.isBase ? 4 : 2}
              className={isOffered ? "cursor-pointer" : undefined}
              role="button"
              aria-label={region.id}
              aria-disabled={!isOffered}
              onClick={isOffered ? () => onSelect(region.id) : undefined}
            />
          </Fragment>
        );
      })}
    </svg>
  );
}
