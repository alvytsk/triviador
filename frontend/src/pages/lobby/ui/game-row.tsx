import { useJoinGame } from "@/features/join-game";
import { ApiFetchError, type LobbyGameSummary } from "@/shared/api";
import { cn } from "@/shared/lib";
import { Banner, Button, Chip } from "@/shared/ui";

/**
 * One row per open game, per the design canvas's lobby artboard: map, host,
 * seats filled to `player_count` of `max_players`, the count, a status
 * chip, and one action button.
 *
 * No Rejoin case: `GameRepository.list_joinable()` filters `WHERE status ==
 * "lobby"`, and `GET /api/games`, `lobby.snapshot` and `lobby.update` are
 * all sourced from it, so every row this component ever receives already
 * has `status === "lobby"` — a `!== "lobby"` branch here could never
 * render. That is also a real product gap, not something this component
 * can fix: a player who refreshes mid-game has no lobby affordance back
 * into it, only browser history or the URL.
 */
export function GameRow({ game }: { game: LobbyGameSummary }) {
  const join = useJoinGame();
  const hasRoom = game.player_count < game.max_players;

  return (
    <li className="flex flex-col gap-2 border border-line bg-panel px-5 py-4">
      <div className="flex items-center gap-4">
        <div className="flex flex-1 flex-col gap-1">
          <span className="font-display text-lg tracking-wider text-ink">{game.map_id}</span>
          <span className="text-[11px] text-ink-dim">Host {game.host_id}</span>
        </div>

        <div className="flex items-center gap-1">
          {Array.from({ length: game.max_players }, (_, seat) => (
            <span
              // biome-ignore lint/suspicious/noArrayIndexKey: the seat number *is* the index — a fixed-length fill gauge, not a reorderable list.
              key={seat}
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                seat < game.player_count ? "bg-gold" : "bg-track",
              )}
            />
          ))}
        </div>

        <span className="w-14 text-center text-[13px] text-ink-dim">
          {game.player_count} / {game.max_players}
        </span>

        <Chip>{game.status}</Chip>

        {hasRoom ? (
          <Button
            variant="ghost"
            disabled={join.isPending}
            onClick={() => join.mutate(game.game_id)}
          >
            Join
          </Button>
        ) : (
          <Button variant="ghost" disabled>
            Full
          </Button>
        )}
      </div>

      {join.error instanceof ApiFetchError && (
        <Banner tone="bad" {...(join.error.code !== null ? { code: join.error.code } : {})}>
          {join.error.message}
        </Banner>
      )}
    </li>
  );
}
