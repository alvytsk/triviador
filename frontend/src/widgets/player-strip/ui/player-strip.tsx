import { answeredBy } from "@/entities/game";
import { orderedPlayers } from "@/entities/player";
import { ownershipOf } from "@/entities/territory";
import type { ClientGameState } from "@/shared/api";
import { seatVar } from "@/shared/config";
import { Chip } from "@/shared/ui";

/**
 * §9.5's top band: one fixed-height (`h-24`, set by the caller) strip, seat
 * order left to right. Seat colour is a left border (§8.1: appearance is
 * derived, never stored); the base's remaining hit points are pips read
 * from `ownershipOf` via the player's own `base_region`; and a chip marks
 * you, an eliminated player, or one who has already answered the open
 * question.
 */
export function PlayerStrip({ state }: { state: ClientGameState }) {
  const players = orderedPlayers(state);
  const ownership = ownershipOf(state);
  const answered = new Set(answeredBy(state));
  const youId = state.you.player_id;

  return (
    <div className="flex h-24 shrink-0 items-stretch divide-x divide-line overflow-x-auto bg-panel">
      {players.map((player) => {
        const base = player.base_region !== null ? ownership.get(player.base_region) : undefined;
        const hp = base?.baseHp ?? null;
        const isYou = player.player_id === youId;
        const hasAnswered = !player.is_eliminated && answered.has(player.player_id);

        return (
          <div
            key={player.player_id}
            className="flex min-w-36 flex-1 flex-col justify-center gap-1 border-l-4 px-3"
            style={{ borderLeftColor: seatVar(player.seat) }}
          >
            <div className="flex items-center gap-2">
              <span className="truncate text-[13px] font-semibold text-ink">
                {player.display_name}
              </span>
              {isYou && <Chip>YOU</Chip>}
              {player.is_eliminated && <Chip>OUT</Chip>}
              {hasAnswered && <Chip>ANSWERED</Chip>}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[13px] text-gold">{player.score}</span>
              {hp !== null && (
                <div role="img" className="flex gap-1" aria-label={`${hp} hit points`}>
                  {Array.from({ length: hp }, (_, pip) => (
                    <span
                      // biome-ignore lint/suspicious/noArrayIndexKey: hit-point pips are a fixed-length gauge, not a reorderable list.
                      key={pip}
                      className="h-2 w-2 rounded-full bg-gold"
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
