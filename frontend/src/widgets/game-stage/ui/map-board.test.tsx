import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { gameState, player, territory } from "../../../../testing/factories";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { MapBoard } from "./map-board";

const DETAIL = {
  map_id: "czechia",
  svg_url: "/maps/czechia/map.svg",
  regions: [
    { region_id: "praha", display_name: "Praha" },
    { region_id: "stredocesky", display_name: "Středočeský" },
    { region_id: "plzensky", display_name: "Plzeňský" },
  ],
};

const GOOD_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">' +
  '<path id="praha" d="M0 0h1v1z"/>' +
  '<path id="stredocesky" d="M2 2h1v1z"/>' +
  '<path id="plzensky" d="M4 4h1v1z"/>' +
  "</svg>";

const BROKEN_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path id="praha" d="M0 0h1v1z"/></svg>';

function withMap(svg = GOOD_SVG) {
  server.use(
    http.get("/api/maps/czechia", () => HttpResponse.json(DETAIL)),
    http.get("/maps/czechia/map.svg", () => HttpResponse.text(svg)),
  );
}

function stateWithPicking() {
  return gameState({
    players: [player(), player({ player_id: "u2", display_name: "Petra", seat: 1 })],
    territories: [territory({ region_id: "praha", owner_id: "u2" })],
    turn: {
      kind: "expansion_picking",
      current_picker: "u1",
      deadline_at: new Date(Date.now() + 20_000).toISOString(),
      deadline_id: 1,
      grants_remaining: {},
      pick_order: ["u1", "u2"],
      your_options: { pick: ["plzensky"], attack: [] },
    },
  });
}

describe("MapBoard", () => {
  it("fills a region owned by seat 1 with var(--seat-1), and leaves a free region alone", async () => {
    withMap();
    const { container } = renderWithApp(<MapBoard state={stateWithPicking()} onSelect={vi.fn()} />);
    const owned = await screen.findByLabelText("praha");
    expect(owned).toHaveAttribute("fill", "var(--seat-1)");
    const free = container.querySelector('path[aria-label="stredocesky"]');
    expect(free).toHaveAttribute("fill", "var(--color-region-free)");
  });

  it("makes only regions in your_options clickable, and calls onSelect for one of them", async () => {
    withMap();
    const onSelect = vi.fn();
    renderWithApp(<MapBoard state={stateWithPicking()} onSelect={onSelect} />);
    const offered = await screen.findByLabelText("plzensky");
    expect(offered).toHaveAttribute("aria-disabled", "false");
    const notOffered = screen.getByLabelText("stredocesky");
    expect(notOffered).toHaveAttribute("aria-disabled", "true");

    await userEvent.click(offered);
    expect(onSelect).toHaveBeenCalledWith("plzensky");
  });

  it("is keyboard-activatable on an offered region only", async () => {
    withMap();
    const onSelect = vi.fn();
    renderWithApp(<MapBoard state={stateWithPicking()} onSelect={onSelect} />);
    const offered = await screen.findByLabelText("plzensky");
    const notOffered = screen.getByLabelText("stredocesky");

    expect(offered).toHaveAttribute("tabindex", "0");
    expect(notOffered).not.toHaveAttribute("tabindex");

    offered.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("plzensky");

    onSelect.mockClear();
    offered.focus();
    await userEvent.keyboard(" ");
    expect(onSelect).toHaveBeenCalledWith("plzensky");
  });

  it("does nothing when a region that is not offered is clicked", async () => {
    withMap();
    const onSelect = vi.fn();
    renderWithApp(<MapBoard state={stateWithPicking()} onSelect={onSelect} />);
    const notOffered = await screen.findByLabelText("stredocesky");
    await userEvent.click(notOffered);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders the named error, and no <path>, when the SVG violates the contract", async () => {
    withMap(BROKEN_SVG);
    const { container } = renderWithApp(<MapBoard state={stateWithPicking()} onSelect={vi.fn()} />);
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("czechia");
    expect(container.querySelectorAll("path")).toHaveLength(0);
  });
});
